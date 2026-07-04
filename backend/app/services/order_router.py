"""
Phase 2 — multi-leg order routing.

Takes a basket of option/future legs and executes them through the broker with:
  * contract resolution from the scrip master (exact security ids — never guessed),
  * leg sequencing: BUY legs before SELL legs (entries: hedge first, no naked
    interval + margin benefit; exits: cover shorts first, risk off first),
  * an order state machine per leg:
        PENDING -> PLACED -> FILLED | REJECTED | CANCELLED | TIMEOUT
    with polling, a market-chase on limit timeout, and
  * unwind: if a leg fails after earlier legs filled, the filled legs are
    reversed with MARKET orders so no naked position is left behind,
  * a full OrderAudit row per leg (UUID basket id, timestamps, fills, reasons).

PAPER/LIVE: the broker's place_order is already gated by TRADING_MODE. In PAPER
mode it returns a synthetic "PAPER-..." id; the router detects that and marks
the leg filled at its limit price instantly. The exact same code path therefore
runs in both modes — paper trading exercises the real router.
"""
import asyncio
import datetime
import logging
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import scrip_master
from backend.app.db.models import OrderAudit
from backend.app.services.broker_service import broker_service
from trading_mode import mode as trading_mode

logger = logging.getLogger("OrderRouter")

# Dhan order statuses that terminate polling
_FILLED_STATUSES = {"TRADED"}
_DEAD_STATUSES = {"REJECTED", "CANCELLED", "EXPIRED"}

LEG_FILL_TIMEOUT_SEC = 45   # max wait for a LIMIT leg before market-chasing
POLL_INTERVAL_SEC = 2


def _now() -> datetime.datetime:
    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(IST).replace(tzinfo=None)


def sequence_legs(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """BUY legs first, then SELL legs (stable within each group)."""
    buys = [l for l in legs if str(l.get("side", "")).upper() == "BUY"]
    sells = [l for l in legs if str(l.get("side", "")).upper() != "BUY"]
    return buys + sells


class OrderRouter:
    def __init__(self):
        self.leg_fill_timeout = LEG_FILL_TIMEOUT_SEC
        self.poll_interval = POLL_INTERVAL_SEC

    # ── audit helpers ──────────────────────────────────────────────────────
    async def _audit(self, session: Optional[AsyncSession], row: OrderAudit):
        if session is None:
            return
        try:
            row.updated_at = _now()
            session.add(row)
            await session.commit()
        except Exception as e:
            logger.error(f"OrderAudit write failed (non-fatal): {e}")
            try:
                await session.rollback()
            except Exception:
                pass

    # ── contract resolution ────────────────────────────────────────────────
    def _resolve_leg(self, ticker: str, leg: Dict[str, Any], expiry=None) -> Optional[Dict[str, Any]]:
        """Resolve one leg to {security_id, exchange_segment, lot_size, trading_symbol}.
        A leg may carry its own 'expiry' (multi-expiry structures like
        calendars) which overrides the basket-level expiry."""
        expiry = leg.get("expiry") or expiry
        opt_type = str(leg.get("opt_type", "")).lower()
        if opt_type == "fut":
            broker = broker_service.get_broker()
            sec_id = broker.get_futures_security_id(ticker)
            if not sec_id:
                return None
            return {
                "security_id": str(sec_id),
                "exchange_segment": "NSE_FNO",
                "lot_size": scrip_master.get_lot_size(ticker),
                "trading_symbol": f"{ticker}-FUT",
                "expiry": expiry,
            }
        return scrip_master.resolve_option_contract(ticker, leg.get("strike"), opt_type, expiry)

    # ── single-leg execution (the state machine) ───────────────────────────
    async def _execute_leg(
        self,
        session: Optional[AsyncSession],
        audit: OrderAudit,
        security_id: str,
        exchange_segment: str,
        side: str,
        quantity: int,
        limit_price: Optional[float],
    ) -> Dict[str, Any]:
        """Runs one leg to a terminal state. Returns {status, fill_price, order_id}."""
        broker = broker_service.get_broker()
        order_type = "LIMIT" if limit_price and limit_price > 0 else "MARKET"

        order_id = await broker.place_order(
            security_id=security_id,
            exchange_segment=exchange_segment,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=float(limit_price or 0.0),
        )

        if not order_id:
            audit.status = "REJECTED"
            audit.detail = "place_order returned no order id"
            await self._audit(session, audit)
            return {"status": "REJECTED", "fill_price": None, "order_id": None}

        audit.broker_order_id = str(order_id)
        audit.status = "PLACED"
        audit.placed_at = _now()
        await self._audit(session, audit)

        # PAPER mode: the broker gate returned a synthetic id — instant simulated
        # fill at the limit price (which came from real bid/ask paper-fill math).
        if str(order_id).startswith("PAPER-"):
            fill = float(limit_price or 0.0)
            audit.status = "FILLED"
            audit.fill_price = Decimal(str(round(fill, 2)))
            audit.detail = "paper fill (simulated at limit)"
            await self._audit(session, audit)
            return {"status": "FILLED", "fill_price": fill, "order_id": order_id}

        # LIVE mode: poll to a terminal state
        elapsed = 0.0
        while elapsed < self.leg_fill_timeout:
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
            details = await broker.get_order_details(order_id)
            status = (details or {}).get("status", "UNKNOWN")

            if status in _FILLED_STATUSES:
                fill = float(details.get("avg_price") or limit_price or 0.0)
                audit.status = "FILLED"
                audit.fill_price = Decimal(str(round(fill, 2)))
                await self._audit(session, audit)
                return {"status": "FILLED", "fill_price": fill, "order_id": order_id}

            if status in _DEAD_STATUSES:
                audit.status = "REJECTED"
                audit.detail = f"broker status {status}: {(details or {}).get('reason', '')}"
                await self._audit(session, audit)
                return {"status": "REJECTED", "fill_price": None, "order_id": order_id}

        # Timeout on a LIMIT order -> cancel and chase with MARKET for certainty
        if order_type == "LIMIT":
            logger.warning(f"⏳ Leg timeout after {self.leg_fill_timeout}s — cancelling and chasing with MARKET")
            await broker.cancel_order(order_id)
            market_id = await broker.place_order(
                security_id=security_id,
                exchange_segment=exchange_segment,
                side=side,
                order_type="MARKET",
                quantity=quantity,
            )
            if market_id:
                await asyncio.sleep(self.poll_interval)
                details = await broker.get_order_details(market_id)
                if details and details.get("status") in _FILLED_STATUSES:
                    fill = float(details.get("avg_price") or 0.0)
                    audit.broker_order_id = str(market_id)
                    audit.status = "FILLED"
                    audit.fill_price = Decimal(str(round(fill, 2)))
                    audit.detail = "filled via market chase after limit timeout"
                    await self._audit(session, audit)
                    return {"status": "FILLED", "fill_price": fill, "order_id": market_id}

        audit.status = "TIMEOUT"
        audit.detail = "no fill within timeout (after market chase attempt)"
        await self._audit(session, audit)
        return {"status": "TIMEOUT", "fill_price": None, "order_id": order_id}

    # ── unwind ─────────────────────────────────────────────────────────────
    async def _unwind_filled_legs(
        self,
        session: Optional[AsyncSession],
        basket_id: str,
        ticker: str,
        strategy_type: str,
        filled: List[Dict[str, Any]],
    ):
        """Reverse already-filled legs with MARKET orders so a partially executed
        basket never leaves a naked position."""
        for f in filled:
            reverse_side = "SELL" if f["side"] == "BUY" else "BUY"
            logger.warning(
                f"↩️ UNWIND: {reverse_side} {f['quantity']} of {f.get('trading_symbol')} "
                f"(basket {basket_id[:8]})"
            )
            audit = OrderAudit(
                basket_id=basket_id, ticker=ticker, strategy_type=strategy_type,
                intent="UNWIND", mode=trading_mode(), leg_index=f["leg_index"],
                side=reverse_side, opt_type=f["opt_type"],
                strike=Decimal(str(f.get("strike") or 0)),
                expiry=str(f.get("expiry") or ""),
                security_id=f["security_id"], trading_symbol=f.get("trading_symbol"),
                exchange_segment=f["exchange_segment"], quantity=f["quantity"],
            )
            try:
                await self._execute_leg(
                    session, audit, f["security_id"], f["exchange_segment"],
                    reverse_side, f["quantity"], None,  # MARKET
                )
            except Exception as e:
                # An unwind failure means a live naked leg — loudest possible log.
                logger.critical(
                    f"🚨🚨 UNWIND FAILED for {f.get('trading_symbol')} — MANUAL INTERVENTION "
                    f"REQUIRED (basket {basket_id}): {e}"
                )

    # ── basket execution ───────────────────────────────────────────────────
    async def route_basket(
        self,
        session: Optional[AsyncSession],
        *,
        ticker: str,
        strategy_type: str,
        legs: List[Dict[str, Any]],
        lots: int,
        intent: str = "ENTRY",
        expiry=None,
        position_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute a multi-leg basket to completion or clean failure.

        legs: [{opt_type: 'ce'|'pe'|'fut', strike, side: 'BUY'|'SELL',
                limit_price (optional — None => MARKET)}]
        Returns {status: 'FILLED'|'FAILED', basket_id, legs: [...], net_premium_per_share}
        net premium is signed: + paid (debit) / − received (credit).
        """
        basket_id = str(uuid.uuid4())
        mode_now = trading_mode()
        ordered = sequence_legs(legs)
        logger.info(
            f"🧺 ROUTE BASKET {basket_id[:8]} [{mode_now}] {intent} {strategy_type} on {ticker}: "
            f"{len(ordered)} legs x {lots} lots"
        )

        # Resolve ALL legs before placing ANY order — a basket with an
        # unresolvable leg must not start executing.
        resolved = []
        for idx, leg in enumerate(ordered):
            contract = self._resolve_leg(ticker, leg, expiry)
            if contract is None:
                logger.error(
                    f"❌ Basket {basket_id[:8]}: cannot resolve leg {leg.get('opt_type')} "
                    f"{leg.get('strike')} — basket aborted before any order"
                )
                return {"status": "FAILED", "basket_id": basket_id,
                        "reason": f"unresolvable_leg_{leg.get('opt_type')}_{leg.get('strike')}",
                        "legs": []}
            resolved.append((idx, leg, contract))

        filled: List[Dict[str, Any]] = []
        for idx, leg, contract in resolved:
            side = str(leg["side"]).upper()
            quantity = int(lots) * int(contract["lot_size"])
            limit_price = leg.get("limit_price")

            audit = OrderAudit(
                basket_id=basket_id, position_id=position_id, ticker=ticker,
                strategy_type=strategy_type, intent=intent, mode=mode_now,
                leg_index=idx, side=side, opt_type=str(leg.get("opt_type", "")).upper(),
                strike=Decimal(str(leg.get("strike") or 0)),
                expiry=str(contract.get("expiry") or ""),
                security_id=contract["security_id"],
                trading_symbol=contract.get("trading_symbol"),
                exchange_segment=contract["exchange_segment"],
                quantity=quantity, status="PENDING",
                limit_price=Decimal(str(round(float(limit_price), 2))) if limit_price else None,
            )
            await self._audit(session, audit)

            result = await self._execute_leg(
                session, audit, contract["security_id"], contract["exchange_segment"],
                side, quantity, limit_price,
            )

            if result["status"] != "FILLED":
                logger.error(
                    f"❌ Basket {basket_id[:8]}: leg {idx} ({side} {leg.get('opt_type')} "
                    f"{leg.get('strike')}) ended {result['status']} — unwinding {len(filled)} filled leg(s)"
                )
                if filled:
                    await self._unwind_filled_legs(session, basket_id, ticker, strategy_type, filled)
                    for f in filled:
                        f["status"] = "UNWOUND"
                return {"status": "FAILED", "basket_id": basket_id,
                        "reason": f"leg_{idx}_{result['status']}",
                        "legs": filled}

            filled.append({
                "leg_index": idx, "opt_type": str(leg.get("opt_type", "")).lower(),
                "strike": float(leg.get("strike") or 0), "side": side,
                "quantity": quantity, "security_id": contract["security_id"],
                "trading_symbol": contract.get("trading_symbol"),
                "exchange_segment": contract["exchange_segment"],
                "expiry": str(contract.get("expiry") or ""),
                "entry_fill": float(result["fill_price"]),
                "order_id": str(result["order_id"]),
                "status": "FILLED",
            })

        # Signed net premium per share (futures legs excluded — they are not premium)
        net = sum(
            (1 if f["side"] == "BUY" else -1) * f["entry_fill"]
            for f in filled if f["opt_type"] != "fut"
        )
        logger.info(
            f"✅ Basket {basket_id[:8]} FILLED: {len(filled)} legs, "
            f"net premium {net:+.2f}/share [{mode_now}]"
        )
        return {"status": "FILLED", "basket_id": basket_id, "legs": filled,
                "net_premium_per_share": round(net, 2)}


order_router = OrderRouter()
