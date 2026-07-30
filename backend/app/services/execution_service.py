import os
import asyncio
import datetime
import logging
import random
import pandas as pd
from typing import List, Dict, Any, Optional
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.db.models import OpenPosition, Trade
from backend.app.core.risk_shield import RiskShield
from backend.app.core.rl_oms import rl_oms
from backend.app.services.database_service import database_service
from backend.app.services.redis_service import redis_service

# Entry/exit decisions were print()-only, so they went to a minimized console
# window and were absent from every log file — which is why a whole day of
# fabricated fills left no trace. Route the decisions that matter here too.
logger = logging.getLogger("ExecutionService")

# Option order books are empty or crossed for the first minutes after the 09:15
# open — the quotes there are stale prints from the previous session, not prices
# anyone will trade at. Both routed exits in the paper record fired at 09:15:0x
# on the first OMS tick of the day and booked fabricated P&L (2026-07-09 and
# 2026-07-28), so hold off on exit decisions until the book has formed. Spot is
# reliable at the open but the exit still has to be PRICED off option marks, so
# suppressing the whole evaluation is the safe move. Set 0 to disable.
MARKET_OPEN_WARMUP_MIN = int(os.getenv("MARKET_OPEN_WARMUP_MIN", "5") or 0)
_MARKET_OPEN_HOUR, _MARKET_OPEN_MINUTE = 9, 15


def _in_open_warmup(now: datetime.datetime) -> bool:
    """True while `now` (IST, naive) sits inside the post-open warm-up window."""
    if MARKET_OPEN_WARMUP_MIN <= 0:
        return False
    opened = now.replace(hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE,
                         second=0, microsecond=0)
    return opened <= now < opened + datetime.timedelta(minutes=MARKET_OPEN_WARMUP_MIN)


class ExecutionService:
    def __init__(self):
        self.risk_shield = RiskShield()
        # Position ids currently held because their book won't quote. Tracked so
        # the "holding, unmarkable" notice logs on the state change instead of
        # once every 10s cycle.
        self._unmarkable: set = set()

    def _load_brain_config(self) -> Dict[str, Any]:
        import json
        if os.path.exists("brain_config.json"):
            try:
                with open("brain_config.json", 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    async def execute_trade(self, session: AsyncSession, trade_data: Dict[str, Any]) -> bool:
        """
        Logs a new trade into the Open Positions database.
        Supports Institutional Execution Algos: 'MARKET', 'ICEBERG', 'TWAP', 'RL_HUNT'
        """
        try:
            # V8 Upgrade: Default to RL_HUNT for larger positions
            total_lots = int(trade_data.get("lots_sized", 1))
            execution_algo = trade_data.get("execution_algo", "RL_HUNT" if total_lots >= 3 else "MARKET")
            ticker = trade_data.get("ticker", "UNK")
            spot_price = float(trade_data.get("spot_price", 0.0))
            
            print(f"🚀 EXECUTION START: {ticker} | Algo: {execution_algo} | Lots: {total_lots}")

            if execution_algo == "RL_HUNT":
                side = "BUY" if trade_data.get("bias") == "BULLISH" else "SELL"
                final_fill_price = await rl_oms.execute_limit_hunt(ticker, side, total_lots, spot_price)
            elif execution_algo == "ICEBERG":
                final_fill_price = await self._execute_iceberg(ticker, total_lots, spot_price)
            elif execution_algo == "TWAP":
                duration_mins = int(trade_data.get("algo_duration", 5))
                final_fill_price = await self._execute_twap(ticker, total_lots, duration_mins)
            else:
                final_fill_price = spot_price

            # --- 💰 PHASE 1: REAL OPTION PRICING (entry) ---
            from backend.app.services.options_pricing_service import options_pricing_service
            entry_pricing = await options_pricing_service.price_spread_entry(trade_data)
            real_net_credit = None
            entry_iv = 0.15
            if entry_pricing.get("pricing_source") == "DHAN_LIVE":
                real_net_credit = entry_pricing.get("net_credit_per_share")
                ivs = [float(l.get("iv") or 0) for l in entry_pricing.get("legs", [])
                       if l.get("opt_type") != "fut"]
                ivs = [v for v in ivs if v > 0]
                if ivs:
                    entry_iv = (sum(ivs) / len(ivs)) / 100.0  # Dhan IV is a percent
                print(f"   💰 REAL PRICING: net credit ₹{real_net_credit}/sh from live chain (IV~{entry_iv:.3f})")
            else:
                print(f"   ⚠️ HEURISTIC pricing fallback ({entry_pricing.get('reason')}) — synthetic premium retained")
                logger.warning(f"entry pricing fell back to heuristic for {ticker} "
                               f"{trade_data.get('strategy_type')}: "
                               f"{entry_pricing.get('reason')}")

            # --- 📐 PHASE 4: margin feasibility for naked-leg strategies ---
            # Short straddle / CSP / covered call carry naked-leg margin
            # (~12% of notional per naked leg on index options). A small
            # account physically cannot run them — refuse before routing.
            st_up = str(trade_data.get("strategy_type", "")).upper()
            if st_up in ("DELTA_NEUTRAL", "CASH_SECURED_PUT", "COVERED_CALL"):
                from backend.app.core import position_sizer as _ps
                naked_legs = 2 if st_up in ("DELTA_NEUTRAL", "COVERED_CALL") else 1
                lot_m = self.risk_shield.get_lot_size(ticker)
                est_margin = 0.12 * spot_price * lot_m * naked_legs * max(total_lots, 1)
                equity_m = _ps.account_equity()
                if est_margin > 0.8 * equity_m:
                    print(f"   🚫 MARGIN GATE: {st_up} needs ~₹{est_margin:,.0f} margin "
                          f"(> 80% of TRADING_EQUITY ₹{equity_m:,.0f}) — trade NOT taken")
                    return False

            # --- 📐 PHASE 4: credit floor + risk-based sizing (defined-risk) ---
            if st_up in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR"):
                from backend.app.core import position_sizer
                from backend.app.core import regime_filters as _rf
                from backend.app.services.options_pricing_service import CREDIT_FLOOR, IC_CREDIT_FLOOR

                # A fallback price means we never read the real book. Everything
                # downstream then runs on fiction: the credit floor below is
                # skipped (it only tests a real credit), sizing keys off the
                # synthetic estimate, and no entry legs get stored — so the
                # position can never be marked to market and every exit for the
                # rest of its life falls to the synthetic path. That is exactly
                # how trade 50 was opened on a made-up ₹26/sh credit on
                # 2026-07-30 and then squared off at 15:15 on heuristic math.
                # Refuse: a defined-risk credit spread we cannot price is a
                # spread we cannot close.
                if real_net_credit is None:
                    reason_np = entry_pricing.get("reason")
                    print(f"   🚫 NO REAL PRICING ({reason_np}) — {st_up} needs a live "
                          f"book to size and mark, trade NOT taken")
                    logger.warning(f"{ticker} {st_up} refused: no live pricing ({reason_np})")
                    return False

                floor_now = IC_CREDIT_FLOOR if st_up == "IRON_CONDOR" else CREDIT_FLOOR
                if float(real_net_credit) < floor_now:
                    print(f"   🚫 CREDIT FLOOR: real credit ₹{real_net_credit}/sh < ₹{floor_now} "
                          f"— too thin to beat friction, trade NOT taken")
                    return False

                width = abs(float(trade_data.get("leg_1_sell", 0) or 0)
                            - float(trade_data.get("leg_2_buy", 0) or 0))
                credit_est = float(real_net_credit if real_net_credit is not None
                                   else trade_data.get("net_credit_per_share", 0) or 0)
                lot_size_s = self.risk_shield.get_lot_size(ticker)

                # net spread delta from live chain legs when available
                # (iron condor is delta-flat by construction)
                dnet = 0.05 if st_up == "IRON_CONDOR" else 0.10
                deltas = [abs(float(l.get("delta") or 0))
                          for l in entry_pricing.get("legs", [])
                          if l.get("opt_type") != "fut"]
                deltas = [d / 100.0 if d > 1.0 else d for d in deltas if d > 0]
                if st_up != "IRON_CONDOR" and len(deltas) == 2:
                    dnet = max(max(deltas) - min(deltas), 0.02)

                rv = None
                try:
                    from backend.app.services.regime_service import regime_service
                    if regime_service._closes:
                        rv = _rf.realized_vol(regime_service._closes)
                except Exception:
                    pass

                recent_pnls = None
                try:
                    q_pnl = select(Trade.realized_pnl).order_by(Trade.exit_date.desc()).limit(50)
                    res_pnl = await session.execute(q_pnl)
                    vals = [float(x) for x in res_pnl.scalars().all() if x is not None]
                    recent_pnls = list(reversed(vals))
                except Exception:
                    pass

                if width > 0:
                    sized, sdetail = position_sizer.size_lots(
                        width=width, credit=credit_est, lot_size=lot_size_s,
                        spot=spot_price, dnet=dnet, realized_vol_ann=rv,
                        recent_pnls=recent_pnls)
                    if sized < 1:
                        print(f"   🚫 SIZING VETO: 0 lots ({sdetail}) — trade NOT taken")
                        return False

                    # ── 🪜 PHASE 6: ladder IVR soft-sizing + portfolio cap ──
                    from trading_mode import ladder_enabled, LADDER_PORTFOLIO_MAX_LOSS_FRAC
                    ivr_mult = float(trade_data.get("_ivr_size_mult") or 0.0)
                    if ladder_enabled() and ivr_mult > 0:
                        eq_l = position_sizer.account_equity()
                        mlpl = float(sdetail.get("max_loss_per_lot") or 1.0)
                        hard_lots = max(int(position_sizer.HARD_CAP * eq_l / max(mlpl, 1.0)), 1)
                        sized = min(max(int(round(sized * ivr_mult)), 1),
                                    hard_lots, position_sizer.MAX_LOTS)
                        sdetail["size_mult"] = ivr_mult
                        # portfolio max-loss cap across all open tranches
                        open_ml = 0.0
                        for p_open in await database_service.get_open_positions(session):
                            w_o = abs(float(p_open.leg_1_sell or 0) - float(p_open.leg_2_buy or 0))
                            cr_o = float(p_open.net_credit_per_share or 0)
                            open_ml += max(w_o - cr_o, 0.0) * int(p_open.lots_sized or 1) \
                                * self.risk_shield.get_lot_size(p_open.ticker)
                        new_ml = mlpl * sized
                        if open_ml + new_ml > LADDER_PORTFOLIO_MAX_LOSS_FRAC * eq_l:
                            print(f"   🚫 PORTFOLIO CAP: open max-loss ₹{open_ml:,.0f} + "
                                  f"new ₹{new_ml:,.0f} > {LADDER_PORTFOLIO_MAX_LOSS_FRAC:.0%} "
                                  f"of ₹{eq_l:,.0f} — tranche NOT taken")
                            return False
                        sdetail["portfolio_open_max_loss"] = round(open_ml, 2)
                    if sized != total_lots:
                        print(f"   📐 RISK SIZING: desk asked {total_lots} lots -> sized {sized} ({sdetail})")
                        total_lots = sized
                        trade_data["lots_sized"] = sized
                    learning_context_sizing = sdetail
                else:
                    learning_context_sizing = None
            else:
                learning_context_sizing = None

            # --- 🧺 PHASE 2: ROUTE THE ENTRY BASKET THROUGH THE BROKER ---
            # Real multi-leg order placement (PAPER mode fills instantly at the
            # limit via the broker gate; LIVE places actual orders). A failed
            # basket means NO position — never book a trade that didn't execute.
            if entry_pricing.get("pricing_source") == "DHAN_LIVE":
                from backend.app.services.order_router import order_router
                expiry_date = None
                try:
                    raw_exp = str(entry_pricing.get("expiry") or "")[:10]
                    if raw_exp:
                        expiry_date = datetime.datetime.strptime(raw_exp, "%Y-%m-%d").date()
                except ValueError:
                    pass
                route_legs = [
                    {"opt_type": l["opt_type"], "strike": l["strike"], "side": l["side"],
                     "limit_price": l["entry_fill"]}
                    for l in entry_pricing.get("legs", [])
                ]
                basket = await order_router.route_basket(
                    session, ticker=ticker,
                    strategy_type=str(trade_data.get("strategy_type", "")),
                    legs=route_legs, lots=total_lots, intent="ENTRY", expiry=expiry_date,
                )
                if basket.get("status") != "FILLED":
                    print(f"   ❌ ENTRY BASKET FAILED ({basket.get('reason')}) — trade NOT booked")
                    return False
                # Adopt actual fills as truth: legs now carry real fill prices + order ids
                entry_pricing["legs"] = basket["legs"]
                entry_pricing["basket_id"] = basket["basket_id"]
                entry_pricing["net_premium_per_share"] = basket["net_premium_per_share"]
                real_net_credit = round(-float(basket["net_premium_per_share"]), 2)
                entry_pricing["net_credit_per_share"] = real_net_credit
                print(f"   🧺 BASKET FILLED {basket['basket_id'][:8]}: net credit ₹{real_net_credit}/sh from actual fills")

            # --- 🛡️ RISK SHIELD: CALCULATE INITIAL GREEKS ---
            greeks = {"net_delta": 0.0, "net_gamma": 0.0, "net_theta": 0.0, "net_vega": 0.0}
            try:
                iv = entry_iv
                today = datetime.date.today()
                # Use the real nearest expiry from the scrip master (NIFTY weeklies
                # are Tuesday in 2026, BANKNIFTY is monthly-only). Fall back to a
                # next-Tuesday estimate only if the scrip master has no data.
                from backend.app.core import scrip_master
                expiry = scrip_master.get_nearest_expiry(ticker, today)
                if expiry is None:
                    days_until_tue = (1 - today.weekday()) % 7
                    if days_until_tue == 0:
                        days_until_tue = 7
                    expiry = today + datetime.timedelta(days=days_until_tue)

                g = self.risk_shield.calculate_spread_greeks(
                    spot=final_fill_price,
                    leg_1_strike=float(trade_data.get("leg_1_sell", 0)),
                    leg_2_strike=float(trade_data.get("leg_2_buy", 0)),
                    expiry_date=expiry,
                    volatility=iv,
                    strategy_type=trade_data.get("strategy_type", "BULL_PUT_SPREAD")
                )
                greeks.update(g)
            except Exception as re:
                print(f"   ⚠️ Risk Shield Warning: Could not calc initial Greeks: {re}")

            IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
            now = datetime.datetime.now(IST).replace(tzinfo=None)

            # Merge the real entry pricing into the learning context (audit + P&L source)
            learning_context = dict(trade_data.get("learning_context", {}) or {})
            learning_context["entry_pricing"] = entry_pricing
            if learning_context_sizing:
                learning_context["position_sizing"] = learning_context_sizing

            # Prefer the real net credit from live quotes; keep synthetic only on fallback
            net_credit_value = real_net_credit if real_net_credit is not None else trade_data.get("net_credit_per_share", 0)

            formatted_data = {
                "ticker": ticker,
                "strategy_type": trade_data.get("strategy_type"),
                "spot_price": Decimal(str(round(final_fill_price, 2))),
                "leg_1_sell": Decimal(str(trade_data.get("leg_1_sell", 0))),
                "leg_2_buy": Decimal(str(trade_data.get("leg_2_buy", 0))),
                "net_credit_per_share": Decimal(str(net_credit_value)),
                "max_risk_per_share": Decimal(str(trade_data.get("max_risk_per_share", 0))),
                "risk_reward_ratio": str(trade_data.get("risk_reward_ratio", "1:1")),
                "win_probability": Decimal(str(trade_data.get("win_probability", 85.0))),
                "vol_surge_multiplier": Decimal(str(trade_data.get("vol_surge_multiplier", 1.0))),
                "coi_pcr": Decimal(str(trade_data.get("coi_pcr", 1.0))),
                "bias": str(trade_data.get("bias", "BULLISH")),
                "execution_time": now.time(),
                "mode": trade_data.get("mode", "Paper"),
                "lots_sized": total_lots,
                "entry_date": now,
                "entry_spot_price": Decimal(str(round(final_fill_price, 2))),
                "highest_seen": Decimal(str(round(final_fill_price, 2))),
                "lowest_seen": Decimal(str(round(final_fill_price, 2))),
                "dynamic_sl": Decimal(str(trade_data.get("leg_1_sell", 0))),
                "net_delta": Decimal(str(greeks.get("net_delta", 0.0))),
                "net_gamma": Decimal(str(greeks.get("net_gamma", 0.0))),
                "net_theta": Decimal(str(greeks.get("net_theta", 0.0))),
                "net_vega": Decimal(str(greeks.get("net_vega", 0.0))),
                "learning_context": learning_context
            }
            
            await database_service.add_open_position(session, formatted_data)
            print(f"✅ TRADE LOCKED: {ticker} at ₹{final_fill_price:.2f}")
            return True
        except Exception as e:
            print(f"   ⚠️ Error executing trade: {e}")
            return False

    async def _execute_iceberg(self, ticker: str, total_lots: int, start_price: float) -> float:
        visible_size = max(1, total_lots // 4)
        remaining = total_lots
        fill_prices = []
        
        print(f"   ↳ Iceberg Active: Visible size {visible_size} lots.")
        
        while remaining > 0:
            chunk = min(visible_size, remaining)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            slippage = start_price * random.uniform(0.0001, 0.0005)
            fill_prices.append(start_price + slippage)
            remaining -= chunk
            
        return sum(fill_prices) / len(fill_prices)

    async def _execute_twap(self, ticker: str, total_lots: int, duration_mins: int) -> float:
        slices = 5
        interval = (duration_mins * 60) / slices / 100 
        remaining = total_lots
        fill_prices = []
        
        print(f"   ↳ TWAP Active: {total_lots} lots over {duration_mins} mins.")
        
        # Get start price from latest tick snapshot
        snap = await redis_service.get_json(f"market_snapshot:{ticker}")
        start_price = float(snap["price"]) if snap and "price" in snap else 100.0

        for i in range(slices):
            chunk = total_lots // slices if i < slices - 1 else remaining
            snap = await redis_service.get_json(f"market_snapshot:{ticker}")
            current_price = float(snap["price"]) if snap and "price" in snap else start_price
            fill_prices.append(current_price)
            remaining -= chunk
            if remaining > 0:
                await asyncio.sleep(interval)
                
        return sum(fill_prices) / len(fill_prices)

    def _calculate_strategy_pnl(self, pos, current_price: float, now_time) -> float:
        """
        Calculates the actual strategy-specific realized P&L based on options equations.
        """
        try:
            entry_price = float(pos.entry_spot_price)
            net_credit = float(pos.adjusted_net_credit if pos.adjusted_net_credit is not None else pos.net_credit_per_share)
            premium_paid = float(pos.net_credit_per_share) # Alias for debit
            lots = int(pos.lots_sized)
            lot_size = self.risk_shield.get_lot_size(pos.ticker)
            total_multiplier = lots * lot_size
            
            hours_elapsed = 0.0
            if pos.entry_date:
                time_diff = now_time - pos.entry_date
                hours_elapsed = time_diff.total_seconds() / 3600.0
                
            spot_change = current_price - entry_price
            
            # Determine delta
            if pos.strategy_type in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD", "DEBIT_SPREAD"]:
                delta = 0.30
                if pos.bias == "BULLISH":
                    current_spread_value = premium_paid + (spot_change * delta)
                else:
                    current_spread_value = premium_paid - (spot_change * delta)
                return (current_spread_value - premium_paid) * total_multiplier
                
            elif pos.strategy_type == "CALENDAR_SPREAD":
                delta = 0.05
                appreciation = premium_paid * 0.08 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "DIAGONAL_SPREAD":
                delta = 0.20
                appreciation = premium_paid * 0.05 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "DELTA_NEUTRAL":
                delta = 0.02
                appreciation = net_credit * 0.04 * hours_elapsed
                loss_from_delta = abs(spot_change) * delta * lot_size * lots
                return (appreciation - loss_from_delta)
                
            elif pos.strategy_type == "COVERED_CALL":
                delta = 0.30
                realized_pnl_pts = spot_change - ( (net_credit + spot_change * delta - net_credit * 0.05 * hours_elapsed) - net_credit )
                return realized_pnl_pts * total_multiplier
                
            elif pos.strategy_type == "CASH_SECURED_PUT":
                realized_pnl_pts = (spot_change * 0.30) + net_credit * 0.05 * hours_elapsed
                return realized_pnl_pts * total_multiplier
                
            else: # Default Credit Spread (OTM)
                delta = 0.15
                if pos.bias == "BULLISH":
                    current_spread_value = net_credit - (spot_change * delta)
                else:
                    current_spread_value = net_credit + (spot_change * delta)
                return (net_credit - current_spread_value) * total_multiplier
                
        except Exception as e:
            print(f"Error in _calculate_strategy_pnl: {e}")
            return 0.0

    async def _route_exit_basket(self, session, pos, real_mark, use_market: bool = False):
        """Close a position through the broker by reversing its entry legs.

        Limit prices come from current marks; in PAPER mode we never send a
        price-less order (a paper MARKET would 'fill' at 0), so we fall back to
        the entry fill if no mark exists. use_market=True (panic path) sends true
        MARKET orders only in LIVE mode.
        Returns {pnl_per_share, basket, friction} from ACTUAL exit fills, or
        None if the basket could not be routed/filled — in LIVE that means the
        position is still open at the broker and the DB row must NOT be closed.
        friction is the round-trip (entry+exit) cost breakdown in Rs; callers
        subtract friction['total'] from the gross realized P&L.
        """
        from trading_mode import is_live
        from backend.app.core import friction_model
        from backend.app.services.order_router import order_router

        ctx = getattr(pos, "learning_context", None) or {}
        entry = ctx.get("entry_pricing") or {}
        entry_legs = entry.get("legs")
        if not entry_legs:
            return None

        marks = {}
        for l in (real_mark or {}).get("legs", []):
            marks[(l["opt_type"], round(float(l["strike"]), 2))] = float(l.get("current_mark") or 0)

        exit_legs = []
        for l in entry_legs:
            key = (l["opt_type"], round(float(l["strike"]), 2))
            mark = marks.get(key) or 0.0
            if use_market and is_live():
                lp = None  # true MARKET order — certainty over price in a panic
            else:
                lp = mark if mark > 0 else float(l.get("entry_fill") or 0)
            exit_legs.append({
                "opt_type": l["opt_type"], "strike": l["strike"],
                "side": "SELL" if str(l["side"]).upper() == "BUY" else "BUY",
                "limit_price": lp,
            })

        expiry_date = None
        try:
            raw_exp = str(entry.get("expiry") or "")[:10]
            if raw_exp:
                expiry_date = datetime.datetime.strptime(raw_exp, "%Y-%m-%d").date()
        except ValueError:
            pass

        basket = await order_router.route_basket(
            session, ticker=pos.ticker, strategy_type=str(pos.strategy_type or ""),
            legs=exit_legs, lots=int(pos.lots_sized or 1), intent="EXIT",
            expiry=expiry_date, position_id=pos.id,
        )
        if basket.get("status") != "FILLED":
            return None

        # Realized P&L per share from actual entry and exit fills:
        # long leg: exit - entry ; short leg: entry - exit.
        entry_map = {(l["opt_type"], round(float(l["strike"]), 2)): l for l in entry_legs}
        pnl = 0.0
        for x in basket["legs"]:
            e = entry_map.get((x["opt_type"], round(float(x["strike"]), 2)))
            if not e:
                continue
            sign = 1 if str(e["side"]).upper() == "BUY" else -1
            pnl += sign * (float(x["entry_fill"]) - float(e["entry_fill"]))

        # Round-trip friction (Rs): entry legs may predate the router and lack
        # a quantity — fall back to lots x lot_size for those.
        default_qty = int(pos.lots_sized or 1) * int(self.risk_shield.get_lot_size(pos.ticker))
        try:
            friction = friction_model.round_trip_friction(
                entry_legs, basket["legs"], default_quantity=default_qty)
        except Exception as e:
            print(f"   ⚠️ Friction computation failed (using zero): {e}")
            friction = {"entry": {}, "exit": {}, "total": 0.0}
        return {"pnl_per_share": round(pnl, 2), "basket": basket, "friction": friction}

    async def _publish_held_expiries(self, open_positions) -> List[str]:
        """Tell the harvester which expiries still need a live chain.

        The entry selector rolls forward as positions age, so without this the
        only published chain stops covering anything we already hold and MTM has
        nothing legitimate to mark against. Keyed per ticker; TTL outlives a few
        harvester cycles so a slow cycle doesn't drop the chain.
        """
        by_ticker: Dict[str, set] = {}
        for pos in open_positions:
            exp = self._position_expiry(pos)
            if exp is None:
                continue
            lookup = {"NSEBANK": "BANKNIFTY", "NSEI": "NIFTY"}.get(pos.ticker, pos.ticker)
            by_ticker.setdefault(lookup, set()).add(exp.isoformat())

        published: List[str] = []
        for ticker, expiries in by_ticker.items():
            try:
                await redis_service.set_json(f"held_expiries:{ticker}",
                                             sorted(expiries), expire=300)
                published.extend(sorted(expiries))
            except Exception as e:
                print(f"   ⚠️ could not publish held expiries for {ticker}: {e}")
        return published

    def _position_expiry(self, pos):
        """Expiry date of the position's priced legs, or None."""
        try:
            ctx = getattr(pos, "learning_context", None) or {}
            raw = str((ctx.get("entry_pricing") or {}).get("expiry") or "")[:10]
            return datetime.date.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def _time_stop_reason(self, pos, is_eod: bool) -> Optional[str]:
        """Expiry-driven exits, shared by the real-mark and heuristic paths.

        Both paths have to agree about when time forces us out. When they drifted
        the heuristic path still carried a legacy unconditional 15:15 square-off,
        which flattened 30-45 DTE ladder positions every single afternoon.
        """
        expiry = self._position_expiry(pos)
        if expiry is None:
            return None
        days_left = (expiry - datetime.date.today()).days
        from trading_mode import ladder_enabled, ladder_manage_dte
        if ladder_enabled() and days_left <= ladder_manage_dte():
            return f"⏳ MANAGE @{ladder_manage_dte()}DTE (expiry {expiry})"
        if days_left < 0 or (days_left <= 1 and is_eod):
            return f"⏳ TIME STOP T-1 (expiry {expiry})"
        return None

    def _real_exit_decision(self, pos, pnl_per_share: float, is_eod: bool,
                            spot: Optional[float] = None):
        """Decide exit from REAL mark-to-market P&L (per share).

        Phase 4 exit stack (validated in backtest/real_backtester.py v2):
          1. TAKE PROFIT at +0.50 x credit — the last part of a credit has the
             worst theta/gamma ratio; free the margin and re-arm
          2. STRIKE-TOUCH STOP — spot through the short strike (checked every
             5-min cycle, which is exactly why it beats the old mark stop)
          3. backstop mark stop at -1.5 x credit (gap protection)
          4. TIME STOP at T-1 before expiry, 15:15 — never hold expiry-day gamma
        Set INTRADAY_SQUARE_OFF=true to restore the legacy daily 15:15 exit.
        Returns (exit, reason, pnl_ps) or None -> heuristic path.
        """
        st = (pos.strategy_type or "").upper()
        credit = abs(float(pos.adjusted_net_credit if pos.adjusted_net_credit is not None
                           else (pos.net_credit_per_share or 0.0)))
        if credit <= 0:
            return None

        # legacy behavior on demand
        if is_eod and os.getenv("INTRADAY_SQUARE_OFF", "").lower() == "true":
            return True, "⏰ EOD SQUARE OFF (INTRADAY_SQUARE_OFF) [LIVE]", pnl_per_share

        # time stop: T-1 before the priced expiry (or past expiry = fail-safe);
        # ladder mode manages much earlier — at 21 DTE, per the validated
        # income structure (never hold the gamma half of an option's life)
        time_stop = self._time_stop_reason(pos, is_eod)
        if time_stop:
            return True, f"{time_stop} [LIVE]", pnl_per_share

        if st in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CASH_SECURED_PUT", "IRON_CONDOR"):
            tp, sl = credit * 0.50, -credit * 1.5
            # strike-touch stop: short strike breached by spot
            if spot is not None and spot > 0:
                short_strike = float(getattr(pos, "leg_1_sell", 0) or 0)
                breached = (spot <= short_strike if st != "BEAR_CALL_SPREAD"
                            else spot >= short_strike)
                if st == "IRON_CONDOR":
                    # call-side short lives in the entry legs (put side in leg_1_sell)
                    ctx_ic = getattr(pos, "learning_context", None) or {}
                    ce_shorts = [float(l.get("strike") or 0)
                                 for l in (ctx_ic.get("entry_pricing") or {}).get("legs", [])
                                 if str(l.get("opt_type", "")).lower() == "ce"
                                 and str(l.get("side", "")).upper() == "SELL"]
                    if ce_shorts and spot >= min(ce_shorts):
                        breached = True
                if short_strike > 0 and breached:
                    return True, f"🛑 STOP: SHORT STRIKE TOUCHED ({short_strike:.0f}) [LIVE]", pnl_per_share
        elif st == "DELTA_NEUTRAL":
            tp, sl = credit * 0.50, -credit * 0.80
        elif st in ("DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD"):
            tp, sl = credit * 0.50, -credit * 0.30
        elif st == "COVERED_CALL":
            tp, sl = credit * 1.00, -credit * 2.00
        else:
            return None  # calendar/diagonal/unknown -> heuristic fallback

        if pnl_per_share >= tp:
            return True, f"🎯 TAKE PROFIT ({pos.strategy_type}) [LIVE]", pnl_per_share
        if pnl_per_share <= sl:
            return True, f"🛑 STOP LOSS ({pos.strategy_type}) [LIVE]", pnl_per_share
        return False, "", pnl_per_share

    async def square_off_all_positions(self, session: AsyncSession, reason: str = "EMERGENCY_SQUARE_OFF") -> int:
        open_positions = await database_service.get_open_positions(session)
        if not open_positions:
            return 0

        print(f"🚨 EMERGENCY: Squaring off all {len(open_positions)} positions. Reason: {reason}")
        count = 0
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now = datetime.datetime.now(IST).replace(tzinfo=None)
        
        for pos in open_positions:
            try:
                snap = await redis_service.get_json(f"market_snapshot:{pos.ticker}")
                if snap and "price" in snap:
                    current_price = float(snap["price"])
                else:
                    print(f"⚠️ Warning: No live price for {pos.ticker} during emergency exit. Using last seen price: ₹{pos.highest_seen}")
                    current_price = float(pos.highest_seen or 0.0)

                if current_price == 0.0:
                    print(f"   ❌ Critical: No price data available for {pos.ticker}. Skipping square off.")
                    continue

                # --- 🧺 PHASE 2: real broker square-off (panic = MARKET in LIVE) ---
                exit_reason = reason
                has_routed_legs = bool(((pos.learning_context or {}).get("entry_pricing") or {}).get("legs"))
                if has_routed_legs:
                    from backend.app.services.options_pricing_service import options_pricing_service
                    real_mark = await options_pricing_service.mark_position_pnl(pos, current_price)
                    routed = await self._route_exit_basket(session, pos, real_mark, use_market=True)
                    if routed is not None:
                        lot_size = self.risk_shield.get_lot_size(pos.ticker)
                        pnl = float(routed["pnl_per_share"]) * int(pos.lots_sized or 1) * lot_size
                        friction = routed.get("friction") or {}
                        friction_total = float(friction.get("total") or 0.0)
                        if friction_total > 0:
                            pnl -= friction_total
                            ctx = dict(pos.learning_context or {})
                            ctx["friction_costs"] = friction
                            pos.learning_context = ctx
                            session.add(pos)
                        exit_reason = f"{reason} [ROUTED]"
                    else:
                        from trading_mode import is_live
                        if is_live():
                            print(f"   🚨🚨 PANIC EXIT BASKET FAILED for {pos.ticker} (pos {pos.id}) — "
                                  f"BROKER POSITION MAY STILL BE OPEN. Manual square-off required!")
                            continue
                        pnl = self._calculate_strategy_pnl(pos, current_price, now)
                else:
                    # Position was never routed to the broker (heuristic/pre-Phase-2)
                    # — a DB-only close is the correct square-off for it.
                    pnl = self._calculate_strategy_pnl(pos, current_price, now)

                exit_data = {
                    "exit_date": now,
                    "exit_price": Decimal(str(round(current_price, 2))),
                    "exit_reason": exit_reason,
                    "realized_pnl": Decimal(str(round(pnl, 2)))
                }
                await database_service.close_position(session, pos.id, exit_data)
                count += 1
            except Exception as e:
                print(f"   ⚠️ Failed to emergency close {pos.ticker}: {e}")

        return count

    async def evaluate_open_positions(self, session: AsyncSession) -> List[Dict[str, Any]]:
        open_positions = await database_service.get_open_positions(session)
        if not open_positions:
            return []

        print(f"\n🔍 EXECUTION SERVICE: Evaluating {len(open_positions)} Open Positions...")
        
        # Calculate VaR
        portfolio_var = self.risk_shield.calculate_portfolio_var(open_positions)
        print(f"🛡️ PORTFOLIO RISK: 1-Day VaR (95%): ₹{portfolio_var:,.2f}")

        closed_trades_report = []
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now = datetime.datetime.now(IST).replace(tzinfo=None)
        is_eod = (now.hour == 15 and now.minute >= 15) or (now.hour > 15)

        # Publish BEFORE the warm-up return so the harvester is already fetching
        # our held expiries while exits are suppressed — the chain is then warm
        # the moment evaluation resumes.
        await self._publish_held_expiries(open_positions)

        if _in_open_warmup(now):
            print(f"   ⏸️  OPEN WARM-UP ({MARKET_OPEN_WARMUP_MIN}m): option books not formed "
                  f"yet — holding {len(open_positions)} position(s), no exit checks")
            return []

        for pos in open_positions:
            ticker = pos.ticker
            # Normalize ticker for Redis lookup (Handle DB vs Live Feed naming mismatch)
            lookup_ticker = ticker
            if ticker == "NSEBANK": lookup_ticker = "BANKNIFTY"
            elif ticker == "NSEI": lookup_ticker = "NIFTY"
            
            try:
                snap = await redis_service.get_json(f"market_snapshot:{lookup_ticker}")
                if snap and "price" in snap:
                    current_price = float(snap["price"])
                else:
                    if is_eod:
                        print(f"⚠️ Warning: No live price for {lookup_ticker} during EOD check. Attempting DB candle close fallback...")
                        from backend.app.db.models import Candle
                        q_last_candle = select(Candle.close).where(
                            (Candle.ticker == pos.ticker) & (Candle.timeframe == "5m")
                        ).order_by(Candle.timestamp.desc()).limit(1)
                        res_lc = await session.execute(q_last_candle)
                        db_close = res_lc.scalar()
                        if db_close is not None:
                            current_price = float(db_close)
                            print(f"   ✅ DB Fallback Price Found: ₹{current_price}")
                        else:
                            current_price = float(pos.highest_seen or pos.entry_spot_price or 0.0)
                            print(f"   ⚠️ No DB candle found. Using fallback: ₹{current_price}")
                    else:
                        print(f"⚠️ Warning: No live price for {lookup_ticker} (DB Ref: {ticker}). Skipping evaluation.")
                        continue

                # --- OPTIONS FIREFIGHTER (ADJUSTMENT ENGINE) ---
                firefighting_enabled = await redis_service.get_json("firefighting_enabled")
                if firefighting_enabled:
                    from backend.app.services.firefighter_service import firefighter_service
                    # adj_data must be defined before the check below: if no adjustment
                    # is needed it stays None, otherwise a NameError would be raised and
                    # caught by the outer except — silently skipping this position's
                    # SL/TP evaluation for the cycle (stops would stop working).
                    adj_data = None
                    if firefighter_service.evaluate_adjustment_need(pos, current_price):
                        adj_data = firefighter_service.build_adjustment_trade(pos, current_price)
                    if adj_data:
                        print(f"🚨 [Firefighter] ADJUSTING position {pos.id} ({pos.ticker}) | Bias: {pos.bias} | Spot: {current_price}")
                        original_credit = pos.original_net_credit or pos.net_credit_per_share or Decimal("0.0")
                        adj_credit = adj_data["net_credit_per_share"]
                        
                        pos.is_adjusted = True
                        pos.adjustment_count = (pos.adjustment_count or 0) + 1
                        pos.original_net_credit = original_credit
                        pos.adjusted_net_credit = original_credit + adj_credit
                        
                        context = pos.learning_context or {}
                        
                        if pos.strategy_type == "CALENDAR_SPREAD":
                            # Calendar Roll
                            old_strike = float(pos.leg_1_sell)
                            new_strike = float(adj_data["leg_1_sell"])
                            pos.leg_1_sell = adj_data["leg_1_sell"]
                            pos.entry_spot_price = adj_data["leg_1_sell"] # Reset profit center
                            
                            rolls = context.setdefault("rolls", [])
                            rolls.append({
                                "date": now.isoformat(),
                                "old_strike": old_strike,
                                "new_strike": new_strike,
                                "credit_collected": float(adj_credit)
                            })
                        else:
                            # Credit Spread -> Convert to Iron Condor
                            context["iron_condor_adjustment"] = {
                                "date": now.isoformat(),
                                "opposing_strategy": adj_data["strategy_type"],
                                "leg_1_sell": float(adj_data["leg_1_sell"]),
                                "leg_2_buy": float(adj_data["leg_2_buy"]),
                                "credit_collected": float(adj_credit)
                            }
                            
                        pos.learning_context = context
                        session.add(pos)
                        await session.commit()
                        print(f"🛡️ [Firefighter] Adjustment successfully executed and recorded for position {pos.id}!")
                        # Skip evaluating exits in the same cycle to allow the adjustment to settle
                        continue

                # --- 💰 PHASE 1: REAL MARK-TO-MARKET EXIT PATH ---
                # If we have real entry legs + live marks, drive exits from actual
                # premiums. Falls through to the synthetic heuristic otherwise.
                from backend.app.services.options_pricing_service import options_pricing_service
                real_mark = await options_pricing_service.mark_position_pnl(pos, current_price)

                # A position opened on real legs has to be closed on real marks.
                # When the book is momentarily unquotable, hold and retry next
                # cycle instead of dropping to the synthetic path — that path
                # invents a P&L, which is how trade 49 booked a "🎯 TAKE PROFIT
                # +₹470.60" on 2026-07-30 that no real book ever offered. Same
                # stance the open warm-up and the failed-exit-basket retry take.
                entry_ctx = (getattr(pos, "learning_context", None) or {}).get("entry_pricing") or {}
                if real_mark is None and entry_ctx.get("pricing_source") == "DHAN_LIVE":
                    if pos.id not in self._unmarkable:
                        self._unmarkable.add(pos.id)
                        msg = (f"position {pos.id} ({ticker} {pos.strategy_type}, expiry "
                               f"{entry_ctx.get('expiry')}) has real entry legs but cannot be "
                               f"marked — holding, no exit checks until the book quotes again")
                        print(f"   ⏸️  UNMARKABLE: {msg}")
                        logger.warning(msg)
                    continue
                if pos.id in self._unmarkable:
                    self._unmarkable.discard(pos.id)
                    logger.info(f"position {pos.id} is markable again — exit checks resumed")

                if real_mark and real_mark.get("pricing_source") == "DHAN_LIVE":
                    lots_r = int(pos.lots_sized or 1)
                    lot_size_r = self.risk_shield.get_lot_size(ticker)
                    total_mult_r = lots_r * lot_size_r
                    pnl_ps = float(real_mark["pnl_per_share"])
                    decision = self._real_exit_decision(pos, pnl_ps, is_eod, spot=current_price)
                    if decision is not None:
                        exit_now, exit_reason_r, realized_ps = decision
                        # Keep water marks fresh for reporting continuity
                        pos.highest_seen = Decimal(str(round(max(float(pos.highest_seen or current_price), current_price), 2)))
                        if exit_now:
                            # --- 🧺 PHASE 2: route the exit through the broker ---
                            routed = await self._route_exit_basket(session, pos, real_mark)
                            friction_total_r = 0.0
                            if routed is not None:
                                realized_ps = float(routed["pnl_per_share"])
                                exit_reason_r += " [ROUTED]"
                                friction_r = routed.get("friction") or {}
                                friction_total_r = float(friction_r.get("total") or 0.0)
                                if friction_total_r > 0:
                                    ctx_r = dict(pos.learning_context or {})
                                    ctx_r["friction_costs"] = friction_r
                                    pos.learning_context = ctx_r
                                    session.add(pos)
                            else:
                                from trading_mode import is_live
                                if is_live():
                                    # Broker still holds the legs — closing the DB row
                                    # here would orphan a live position. Retry next cycle.
                                    print(f"   🚨 EXIT BASKET FAILED for {ticker} (pos {pos.id}) — "
                                          f"position NOT closed, will retry next cycle")
                                    continue
                            net_realized = realized_ps * total_mult_r - friction_total_r
                            exit_data = {
                                "exit_date": now,
                                "exit_price": Decimal(str(round(current_price, 2))),
                                "exit_reason": exit_reason_r,
                                "realized_pnl": Decimal(str(round(net_realized, 2))),
                            }
                            closed_trade = await database_service.close_position(session, pos.id, exit_data)
                            if closed_trade:
                                closed_trades_report.append(closed_trade.model_dump())
                                print(f"   -> EXIT [LIVE]: {ticker} | {exit_reason_r} | "
                                      f"PnL: ₹{net_realized:.2f} (friction ₹{friction_total_r:.2f})")
                        else:
                            session.add(pos)
                            await session.commit()
                            print(f"   -> HOLD [LIVE]: {ticker} (Spot ₹{current_price:.2f} | MTM ₹{pnl_ps*total_mult_r:.2f})")
                        continue  # real path handled this position

                # Technical EMA Stop: Calculate 26-EMA on daily
                exit_triggered = False
                exit_reason = ""
                realized_pnl = 0.0

                bias = pos.bias
                short_strike = float(pos.leg_1_sell)
                entry_price = float(pos.entry_spot_price)
                net_credit = float(pos.adjusted_net_credit if pos.adjusted_net_credit is not None else pos.net_credit_per_share)
                lots = int(pos.lots_sized)
                lot_size = self.risk_shield.get_lot_size(ticker)
                total_multiplier = lots * lot_size

                highest_seen = float(pos.highest_seen or current_price)
                lowest_seen = float(pos.lowest_seen or current_price)
                dynamic_sl = float(pos.dynamic_sl or (short_strike if short_strike > 0 else entry_price))

                is_credit = pos.strategy_type in ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "DELTA_NEUTRAL", "COVERED_CALL", "CASH_SECURED_PUT"]
                
                # Determine delta
                if pos.strategy_type in ["DEBIT_BULL_SPREAD", "DEBIT_BEAR_SPREAD"]:
                    delta = 0.30
                elif pos.strategy_type == "CALENDAR_SPREAD":
                    delta = 0.05
                elif pos.strategy_type == "DIAGONAL_SPREAD":
                    delta = 0.20
                elif pos.strategy_type == "DELTA_NEUTRAL":
                    delta = 0.02
                elif pos.strategy_type in ["COVERED_CALL", "CASH_SECURED_PUT"]:
                    delta = 0.30
                else: # Default Credit Spread
                    delta = 0.15

                brain = self._load_brain_config()
                clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
                ticker_config = brain.get(ticker, brain.get(clean_ticker, {}))
                sl_ratio = float(ticker_config.get("stop_loss_pct", 1.0))
                be_lock_ratio = float(ticker_config.get("breakeven_lock_pct", 0.30))

                has_reached_be_lock = False
                
                # Calculate hours elapsed since trade entry (for theta decay simulation)
                hours_elapsed = 0.0
                if pos.entry_date:
                    time_diff = now - pos.entry_date
                    hours_elapsed = time_diff.total_seconds() / 3600.0

                # Realized PnL logic
                if is_credit:
                    # Short Option math (Credit)
                    if pos.strategy_type == "DELTA_NEUTRAL":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        decay = net_credit * 0.10 * hours_elapsed
                        current_spread_value = net_credit + (price_change * delta) - decay
                        
                        is_tp = current_spread_value <= net_credit * 0.50 # 50% decay captured
                        is_sl = current_spread_value >= net_credit * 1.80  # 80% loss
                        realized_pnl_per_share = net_credit - current_spread_value
                        
                        spot_sl_est = entry_price + (net_credit * 0.80 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "COVERED_CALL":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        spot_change = current_price - entry_price
                        decay = net_credit * 0.05 * hours_elapsed
                        current_call_value = net_credit + (spot_change * delta) - decay
                        
                        realized_pnl_per_share = spot_change - (current_call_value - net_credit)
                        max_profit_pts = 100.0 + net_credit
                        is_tp = realized_pnl_per_share >= max_profit_pts * 0.95
                        is_sl = realized_pnl_per_share <= -200.0
                        
                        if is_tp:
                            realized_pnl_per_share = max_profit_pts
                        elif is_sl:
                            realized_pnl_per_share = -200.0
                            
                        spot_sl_est = entry_price - 200.0
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "CASH_SECURED_PUT":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        spot_change = current_price - entry_price
                        decay = net_credit * 0.05 * hours_elapsed
                        current_put_value = net_credit - (spot_change * delta) - decay
                        
                        realized_pnl_per_share = net_credit - current_put_value
                        is_tp = current_put_value <= net_credit * 0.20
                        is_sl = current_put_value >= net_credit * 2.0
                        
                        if is_tp:
                            realized_pnl_per_share = net_credit * 0.80
                        elif is_sl:
                            realized_pnl_per_share = -net_credit * 1.0
                            
                        spot_sl_est = entry_price - (net_credit * 1.0 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    else: # Standard Credit Spreads (BULL_PUT_SPREAD or BEAR_CALL_SPREAD)
                        if bias == "BULLISH":
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            
                            price_change = current_price - entry_price
                            current_spread_value = net_credit - (price_change * delta)
                            
                            has_reached_be_lock = (highest_seen - entry_price) * delta >= net_credit * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_spread_value >= net_credit
                            else:
                                spot_sl_est = entry_price - (net_credit * sl_ratio / delta)
                                is_sl = current_spread_value >= net_credit * (1.0 + sl_ratio)
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_spread_value <= net_credit * 0.20
                            realized_pnl_per_share = net_credit - current_spread_value
                        else: # BEARISH
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            
                            price_change = entry_price - current_price
                            current_spread_value = net_credit - (price_change * delta)
                            
                            has_reached_be_lock = (entry_price - lowest_seen) * delta >= net_credit * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_spread_value >= net_credit
                            else:
                                spot_sl_est = entry_price + (net_credit * sl_ratio / delta)
                                is_sl = current_spread_value >= net_credit * (1.0 + sl_ratio)
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_spread_value <= net_credit * 0.20
                            realized_pnl_per_share = net_credit - current_spread_value
                else:
                    # Long Option math (Debit / Calendar / Diagonal)
                    premium_paid = net_credit
                    if pos.strategy_type == "CALENDAR_SPREAD":
                        # Neutral Debit: profits on consolidation (weekly decays faster), loses on violent moves
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        appreciation = premium_paid * 0.08 * hours_elapsed
                        loss_from_delta = price_change * delta
                        current_option_value = premium_paid + appreciation - loss_from_delta
                        
                        is_tp = current_option_value >= premium_paid * 1.35 # 35% gain
                        is_sl = current_option_value <= premium_paid * 0.75 # 25% loss
                        realized_pnl_per_share = current_option_value - premium_paid
                        
                        spot_sl_est = entry_price + (premium_paid * 0.25 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    elif pos.strategy_type == "DIAGONAL_SPREAD":
                        highest_seen = max(highest_seen, current_price)
                        pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                        lowest_seen = min(lowest_seen, current_price)
                        pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                        
                        price_change = abs(current_price - entry_price)
                        appreciation = premium_paid * 0.06 * hours_elapsed
                        loss_from_delta = price_change * delta
                        current_option_value = premium_paid + appreciation - loss_from_delta
                        
                        is_tp = current_option_value >= premium_paid * 1.40 # 40% gain
                        is_sl = current_option_value <= premium_paid * 0.75 # 25% loss
                        realized_pnl_per_share = current_option_value - premium_paid
                        
                        spot_sl_est = entry_price + (premium_paid * 0.25 / delta)
                        pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                    else: # Standard Debit Spreads
                        if bias == "BULLISH":
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            
                            price_change = current_price - entry_price
                            current_option_value = premium_paid + (price_change * delta)
                            
                            has_reached_be_lock = (highest_seen - entry_price) * delta >= premium_paid * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_option_value <= premium_paid
                            else:
                                spot_sl_est = entry_price - (premium_paid * 0.30 / delta)
                                is_sl = current_option_value <= premium_paid * 0.70
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_option_value >= premium_paid * 1.50
                            realized_pnl_per_share = current_option_value - premium_paid
                        else: # BEARISH
                            lowest_seen = min(lowest_seen, current_price)
                            pos.lowest_seen = Decimal(str(round(lowest_seen, 2)))
                            highest_seen = max(highest_seen, current_price)
                            pos.highest_seen = Decimal(str(round(highest_seen, 2)))
                            
                            price_change = entry_price - current_price
                            current_option_value = premium_paid + (price_change * delta)
                            
                            has_reached_be_lock = (entry_price - lowest_seen) * delta >= premium_paid * be_lock_ratio
                            
                            if has_reached_be_lock:
                                spot_sl_est = entry_price
                                is_sl = current_option_value <= premium_paid
                            else:
                                spot_sl_est = entry_price + (premium_paid * 0.30 / delta)
                                is_sl = current_option_value <= premium_paid * 0.70
                                
                            pos.dynamic_sl = Decimal(str(round(spot_sl_est, 2)))
                            is_tp = current_option_value >= premium_paid * 1.50
                            realized_pnl_per_share = current_option_value - premium_paid

                # Exit logic
                # This used to be a bare `if is_eod:` that flattened everything at
                # 15:15 — legacy intraday-scalper behavior. Under LADDER_MODE the
                # book is 30-45 DTE credit spreads, so it closed positions the
                # strategy meant to hold for weeks, and the entry gate (which only
                # asks "is a position already open?") re-armed seconds later. Honor
                # the same INTRADAY_SQUARE_OFF flag the real-mark path checks, and
                # otherwise fall back to the shared expiry time stop.
                eod_squareoff = is_eod and os.getenv("INTRADAY_SQUARE_OFF", "").lower() == "true"
                time_stop = None if eod_squareoff else self._time_stop_reason(pos, is_eod)
                if eod_squareoff:
                    exit_triggered = True
                    exit_reason = "⏰ EOD SQUARE OFF (INTRADAY_SQUARE_OFF)"
                    realized_pnl = realized_pnl_per_share * total_multiplier
                elif time_stop:
                    exit_triggered = True
                    exit_reason = time_stop
                    realized_pnl = realized_pnl_per_share * total_multiplier
                elif is_tp:
                    exit_triggered = True
                    exit_reason = f"🎯 TAKE PROFIT ({pos.strategy_type})"
                    # P&L calculations
                    if pos.strategy_type == "DELTA_NEUTRAL":
                        realized_pnl = net_credit * 0.50 * total_multiplier
                    elif pos.strategy_type == "CALENDAR_SPREAD":
                        realized_pnl = premium_paid * 0.35 * total_multiplier
                    elif pos.strategy_type == "DIAGONAL_SPREAD":
                        realized_pnl = premium_paid * 0.40 * total_multiplier
                    else:
                        realized_pnl = (net_credit * 0.80 if is_credit else premium_paid * 0.50) * total_multiplier
                elif is_sl:
                    exit_triggered = True
                    if has_reached_be_lock:
                        exit_reason = "🛡️ TRAILING STOP (Breakeven)"
                        realized_pnl = 0.0
                    else:
                        exit_reason = f"🛑 STOP LOSS ({pos.strategy_type})"
                        if pos.strategy_type == "DELTA_NEUTRAL":
                            realized_pnl = -net_credit * 0.80 * total_multiplier
                        elif pos.strategy_type == "CALENDAR_SPREAD":
                            realized_pnl = -premium_paid * 0.25 * total_multiplier
                        elif pos.strategy_type == "DIAGONAL_SPREAD":
                            realized_pnl = -premium_paid * 0.25 * total_multiplier
                        else:
                            realized_pnl = (-net_credit * sl_ratio if is_credit else -premium_paid * 0.30) * total_multiplier


                if exit_triggered:
                    exit_pnl_total = realized_pnl
                    exit_data = {
                        "exit_date": now,
                        "exit_price": Decimal(str(round(current_price, 2))),
                        "exit_reason": exit_reason,
                        "realized_pnl": Decimal(str(round(exit_pnl_total, 2)))
                    }
                    closed_trade = await database_service.close_position(session, pos.id, exit_data)
                    if closed_trade:
                        closed_trades_report.append(closed_trade.model_dump())
                        print(f"   -> EXIT TRIGGERED: {ticker} | Reason: {exit_reason} | PnL: ₹{exit_pnl_total:.2f}")
                else:
                    session.add(pos)
                    await session.commit()
                    print(f"   -> HOLD: {ticker} (Current: ₹{current_price:.2f} | Trailing SL: ₹{dynamic_sl:.2f})")

            except Exception as e:
                print(f"   -> Error evaluating {ticker}: {e}")

        return closed_trades_report

execution_service = ExecutionService()
