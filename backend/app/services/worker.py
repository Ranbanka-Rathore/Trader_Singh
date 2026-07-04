import asyncio
import json
import logging
import os
import selectors
import time
import random
import warnings
from datetime import datetime, time as dt_time, date, timezone as dt_timezone, timedelta as dt_timedelta
from decimal import Decimal
from typing import List

from backend.app.core.quant_engine import QuantEngine
from backend.app.core.risk_shield import RiskShield
from backend.app.core.risk_committee import risk_committee
from backend.app.services.redis_service import redis_service
from backend.app.services.data_service import data_service
from backend.app.services.market_data_service import market_data_service
from backend.app.services.database_service import database_service
from backend.app.services.options_desk_service import options_desk_service
from backend.app.services.execution_service import execution_service
from backend.app.db.database import engine as db_engine
from backend.app.db.models import OpenPosition, Trade, MarketIndicator, SignalAudit
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func, func

# Configure Logging with both Stream and File handlers
from logging.handlers import RotatingFileHandler
os.makedirs("logs", exist_ok=True)
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Rotating File Handler — cap disk usage (10MB x 5 files) so logs can't grow to 100MB+
file_handler = RotatingFileHandler(
    "logs/trader_singh.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Stream Handler (Terminal)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
root_logger.addHandler(stream_handler)

logger = logging.getLogger("AutopilotWorker")

class AutopilotWorker:
    def __init__(self):
        self.engine = QuantEngine()
        self.shield = RiskShield()
        self.is_active = False # Controls the process lifecycle
        self.is_paused = False # Controls the trading cycle (Paused by default)
        self.universe = ["NIFTY"]
        self.heartbeat_file = "heartbeat.txt"
        self.async_session = sessionmaker(
            db_engine(), class_=AsyncSession, expire_on_commit=False
        )

    async def run(self):
        from trading_mode import banner, TRADING_MODE
        logger.warning(banner())
        logger.info(f"Starting Autopilot Background Worker... [TRADING_MODE={TRADING_MODE}]")
        self.is_active = True

        # Phase 2: adopt broker reality before trading (LIVE only).
        try:
            await self.reconcile_with_broker()
        except Exception as e:
            logger.error(f"Startup reconciliation error (continuing): {e}")
        
        # Start Live Market Feed (WebSockets)
        asyncio.create_task(market_data_service.start())
        
        # Listen for control signals in a separate task
        asyncio.create_task(self.listen_for_commands())
        
        while self.is_active:
            try:
                # Update Heartbeat even if paused to show process is alive
                with open(self.heartbeat_file, "w") as f:
                    f.write(str(time.time()))

                # Ensure dev_mode is a standard bool for JSON serialization
                dev_mode_val = await redis_service.get_json("dev_mode")
                is_dev_mode = bool(dev_mode_val) if dev_mode_val is not None else False

                IST = dt_timezone(dt_timedelta(hours=5, minutes=30))
                now_ist = datetime.now(IST)

                if self.is_paused:
                    await redis_service.set_json("autopilot_status", {
                        "last_run": now_ist.isoformat(),
                        "status": "PAUSED",
                        "active_nodes": 0,
                        "source": "IDLE"
                    }, expire=30)
                    await asyncio.sleep(1) # Very fast check
                    continue

                is_weekday = now_ist.weekday() < 5
                is_market_open = (dt_time(9, 15) <= now_ist.time() <= dt_time(15, 30))

                logger.info(f"Cycle Check - Paused: {self.is_paused}, Dev: {is_dev_mode}, Open: {is_market_open}")

                if is_dev_mode or (is_weekday and is_market_open):
                    await self.run_cycle()
                    # After cycle, update status to ACTIVE
                    await redis_service.set_json(f"autopilot_status", {
                        "last_run": now_ist.isoformat(),
                        "status": "ACTIVE",
                        "active_nodes": 3,
                        "source": "LIVE" if not is_dev_mode else "SIMULATED"
                    }, expire=30)
                    await asyncio.sleep(5) # Run every 5s for "Live" feel
                else:
                    await redis_service.set_json("autopilot_status", {
                        "last_run": now_ist.isoformat(),
                        "status": "WAITING_FOR_MARKET",
                        "active_nodes": 0,
                        "source": "IDLE"
                    }, expire=30)
                    await asyncio.sleep(5)
            
            except Exception as e:
                logger.error(f"Error in autopilot cycle: {e}")
                await asyncio.sleep(5)

    async def listen_for_commands(self):
        try:
            pubsub = await redis_service.subscribe("autopilot_commands")
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    command = data.get("command")
                    if command == "STOP":
                        logger.info("Pausing autopilot cycle...")
                        self.is_paused = True
                    elif command == "START":
                        logger.info("Resuming autopilot cycle...")
                        self.is_paused = False
        except Exception as e:
            logger.error(f"Error in command listener: {e}")

    async def run_cycle(self):
        logger.info("Pulsing the Matrix (Autopilot Cycle)...")
        
        IST = dt_timezone(dt_timedelta(hours=5, minutes=30))
        now_ist_aware = datetime.now(IST)
        now_ist_naive = now_ist_aware.replace(tzinfo=None)

        # 1. Run Data Ingestion (GEX/POC/PCR calculation and buffer update)
        await data_service.run_autotrender_cycle()

        async with self.async_session() as session:
            # 2. Evaluate Open Positions & Dynamic Trailing Stop Levels
            closed_trades = await execution_service.evaluate_open_positions(session)
            if closed_trades:
                logger.info(f"Closed {len(closed_trades)} open positions due to target/stop levels.")
                await redis_service.publish("market_updates", {
                    "type": "TRADES_CLOSED",
                    "timestamp": now_ist_naive.isoformat(),
                    "data": closed_trades
                })

            # 3. Risk Audit
            risk_ok, risk_report = await self.perform_risk_audit(session)
            await redis_service.set_json("portfolio_stats", risk_report)
            
            if not risk_ok:
                logger.warning(f"RISK SHIELD TRIGGERED: {risk_report.get('kill_reason')}")
                return 

            # --- V8 UPGRADE: DYNAMIC DELTA HEDGING ---
            from backend.app.core.delta_hedger import delta_hedger
            await delta_hedger.audit_and_hedge(session)

            # --- FORENSIC AUDIT HARD FILTER 1: MAX DAILY TRADES (3) ---
            today_start = now_ist_naive.replace(hour=0, minute=0, second=0, microsecond=0)
            trade_count_result = await session.execute(
                select(func.count(Trade.id)).where(Trade.entry_date >= today_start)
            )
            daily_trade_count = trade_count_result.scalar() or 0
            if daily_trade_count >= 3:
                logger.warning(f"HARD FILTER: Max daily trades reached ({daily_trade_count}/3). Standing down.")
                return

            # --- FORENSIC AUDIT HARD FILTER 2: GEX VOLATILITY GUARD ---
            gex_val = risk_report.get("total_gex", 0)
            if abs(gex_val) > 20000000000: # Example: 2000Cr GEX threshold for index stability
                logger.warning(f"HARD FILTER: High GEX Volatility detected ({gex_val:,.0f}). directional trades blocked.")
                return

            # --- V8 UPGRADE: MARKET HOUR ENTRY TIME-GUARD (09:15 AM to 03:00 PM IST) ---
            is_entry_weekday = now_ist_aware.weekday() < 5
            is_entry_hour = (dt_time(9, 15) <= now_ist_aware.time() < dt_time(15, 0))
            
            # Fetch dev_mode status
            dev_mode_val = await redis_service.get_json("dev_mode")
            is_dev_mode = bool(dev_mode_val) if dev_mode_val is not None else False
            
            # Allow out-of-hours testing/simulation only if dev_mode is True and it's a weekend or post-market hours (after 03:30 PM IST)
            is_testing_allowed = is_dev_mode and (not is_entry_weekday or now_ist_aware.time() >= dt_time(15, 30))
            
            if is_entry_weekday and not is_entry_hour and not is_testing_allowed:
                logger.info(f"⏳ [Time Guard] Skipping entry scans (outside 09:15 AM - 03:00 PM IST on weekday). Active positions and exits will still be evaluated.")
                passed = []
            else:
                # 4. Run Scan Logic (Quantitative Reversals & Crossovers)
                passed = await self.engine.analyze_universe(self.universe)
            
            if passed:
                logger.info(f"Found {len(passed)} assets passing initial scan. Routing to Options Desk...")
                
                # 5. Build Options Spreads
                strategy_mode = await redis_service.get("execution_strategy_mode") or "CREDIT_SPREAD"
                structured_spreads = options_desk_service.process_approved_assets(passed, strategy_mode=strategy_mode)
                
                for spread in structured_spreads:
                    ml_score = spread.get("ml_score", 0.5)
                    ticker = spread.get("ticker", "UNKNOWN")
                    
                    # 6. AI Risk Committee Verdict
                    # Call our new headless Autogen Risk Committee
                    verdict, reasoning = await risk_committee.evaluate_trade(spread)

                    # Log to database SignalAudit table
                    audit_record = {
                        "ticker": ticker,
                        "pa_status": spread["learning_context"].get("PA_Status", "UNKNOWN"),
                        "pcr": Decimal(str(spread["coi_pcr"])),
                        "gex_mn": Decimal("1450.00"), # Nominal representation
                        "ml_score": Decimal(str(spread["ml_score"])),
                        "committee_verdict": verdict,
                        "committee_reasoning": reasoning
                    }
                    audit = await database_service.add_signal_audit(session, audit_record)
                    
                    # Publish live audit results
                    await redis_service.publish("market_updates", {
                        "type": "SCAN_RESULTS",
                        "timestamp": now_ist_naive.isoformat(),
                        "data": passed
                    })
                    
                    if verdict == "APPROVED":
                        # Phase 3 attribution: carry the audit id into the position's
                        # learning_context so close_position can write the outcome back.
                        spread.setdefault("learning_context", {})["signal_audit_id"] = audit.id
                        # 7. Execute Spread Trade via Execution Service
                        spread["execution_algo"] = "ICEBERG" if spread["lots_sized"] > 2 else "MARKET"
                        success = await execution_service.execute_trade(session, spread)
                        if success:
                            logger.info(f"Execution success for spread trade on {ticker}")
            
            # Update heartbeats in Redis
            await redis_service.set_json("autopilot_status", {
                "last_run": now_ist_naive.isoformat(),
                "status": "ACTIVE",
                "active_nodes": len(passed) if passed else 0
            }, expire=120)

    async def reconcile_with_broker(self):
        """Startup reconciliation (Fable Phase 2 item 11): if the bot restarts
        mid-day it must adopt broker reality, not the DB's fantasy.

        Compares net quantity per security id implied by the DB's open-position
        entry legs against the broker's actual positions. Mismatches are logged
        loudly and published to Redis ('reconciliation_report') for the dashboard.
        Nothing is auto-closed or auto-adopted — a human decides; the report and
        trading pause are the safety, silent divergence is the hazard.
        """
        from trading_mode import is_live
        from backend.app.services.broker_service import broker_service

        if not is_live():
            logger.info("🧻 Reconciliation skipped: PAPER mode (no broker positions to adopt).")
            return

        broker = broker_service.get_broker()
        broker_positions = await broker.get_positions()
        if broker_positions is None:
            logger.critical(
                "🚨 RECONCILIATION FAILED: cannot fetch broker positions. "
                "PAUSING trading cycle until resolved — unverified state is not tradeable."
            )
            self.is_paused = True
            return

        async with self.async_session() as session:
            db_positions = await database_service.get_open_positions(session)

        # Expected net units per security id from routed entry legs
        expected: dict = {}
        unverifiable = []
        for pos in db_positions:
            legs = ((pos.learning_context or {}).get("entry_pricing") or {}).get("legs") or []
            if not legs:
                unverifiable.append({"position_id": pos.id, "ticker": pos.ticker,
                                     "strategy": pos.strategy_type})
                continue
            for l in legs:
                sid = str(l.get("security_id") or "")
                if not sid:
                    continue
                sign = 1 if str(l.get("side", "")).upper() == "BUY" else -1
                expected[sid] = expected.get(sid, 0) + sign * int(l.get("quantity", 0))

        actual = {}
        for p in broker_positions:
            sid = str(p.get("securityId") or p.get("security_id") or "")
            if sid:
                actual[sid] = actual.get(sid, 0) + int(p.get("netQty", p.get("net_qty", 0)) or 0)

        mismatches = []
        for sid in set(expected) | set(actual):
            exp_qty, act_qty = expected.get(sid, 0), actual.get(sid, 0)
            if exp_qty != act_qty:
                mismatches.append({"security_id": sid, "db_expected": exp_qty, "broker_actual": act_qty})
                logger.critical(
                    f"🚨 RECONCILIATION MISMATCH sec_id={sid}: DB expects net {exp_qty}, "
                    f"broker holds {act_qty}"
                )

        report = {
            "checked_at": datetime.now().isoformat(),
            "db_positions": len(db_positions),
            "broker_lines": len(broker_positions),
            "mismatches": mismatches,
            "unverifiable_positions": unverifiable,
            "status": "CLEAN" if not mismatches else "MISMATCH",
        }
        await redis_service.set_json("reconciliation_report", report)

        if mismatches:
            logger.critical(
                f"🚨 {len(mismatches)} reconciliation mismatch(es). PAUSING trading — "
                "resolve manually, then send START."
            )
            self.is_paused = True
        else:
            logger.info(
                f"✅ Reconciliation CLEAN: {len(db_positions)} DB positions match broker "
                f"({len(unverifiable)} unverifiable legacy rows noted)."
            )

    async def perform_risk_audit(self, session: AsyncSession):
        """Checks all kill switches and calculates portfolio risk."""
        # A. Fetch Open Positions
        open_positions = await database_service.get_open_positions(session)
        
        # B. Fetch Today's Realized PnL
        IST = dt_timezone(dt_timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST).replace(tzinfo=None)
        today_start = datetime.combine(now_ist.date(), dt_time.min)
        pnl_result = await session.execute(
            select(func.sum(Trade.realized_pnl))
            .where(Trade.exit_date >= today_start)
        )
        daily_pnl = float(pnl_result.scalar() or 0.0)

        # B2. Bug #6 fix: include unrealized mark-to-market of OPEN positions in the
        # daily-loss gate. Realized-only P&L lets an open position bleed well past the
        # kill line (e.g. a covered call down ₹13k) while the switch stays asleep.
        open_mtm = 0.0
        for pos in open_positions:
            try:
                lookup_ticker = pos.ticker
                if lookup_ticker == "NSEBANK":
                    lookup_ticker = "BANKNIFTY"
                elif lookup_ticker == "NSEI":
                    lookup_ticker = "NIFTY"
                snap = await redis_service.get_json(f"market_snapshot:{lookup_ticker}")
                if snap and "price" in snap:
                    current_price = float(snap["price"])
                    # Prefer real mark-to-market; fall back to the heuristic estimate.
                    from backend.app.services.options_pricing_service import options_pricing_service
                    rm = await options_pricing_service.mark_position_pnl(pos, current_price)
                    if rm and rm.get("pricing_source") == "DHAN_LIVE":
                        lot_size = self.shield.get_lot_size(pos.ticker)
                        open_mtm += float(rm["pnl_per_share"]) * int(pos.lots_sized or 1) * lot_size
                    else:
                        open_mtm += execution_service._calculate_strategy_pnl(pos, current_price, now_ist)
            except Exception as e:
                logger.debug(f"MTM calc failed for {getattr(pos, 'ticker', '?')}: {e}")

        # Combined P&L is what the kill switches must evaluate.
        combined_pnl = daily_pnl + open_mtm

        # C. Fetch Latest GEX (Proxy: NIFTY)
        gex_result = await session.execute(
            select(MarketIndicator.total_gex)
            .where(MarketIndicator.ticker == "NIFTY")
            .order_by(MarketIndicator.timestamp.desc())
            .limit(1)
        )
        total_gex = float(gex_result.scalar() or 0.0)
        
        # D. Consecutive Losses (From Redis)
        losses = int(await redis_service.get("consecutive_losses") or 0)
        
        # E. Last Trade Time
        last_trade_result = await session.execute(
            select(Trade.entry_date).order_by(Trade.entry_date.desc()).limit(1)
        )
        last_trade_time = last_trade_result.scalar()

        # F. Calculate VaR
        portfolio_var = self.shield.calculate_portfolio_var(open_positions)
        
        # G. Check Kill Switches — evaluate realized + open MTM together.
        is_killed, reason = self.shield.check_kill_switches(
            current_daily_pnl=combined_pnl,
            open_positions_count=len(open_positions),
            total_gex=total_gex,
            last_trade_time=last_trade_time,
            current_consecutive_losses=losses,
            portfolio_var=portfolio_var
        )

        report = self.shield.get_risk_report(open_positions, combined_pnl)
        report["kill_reason"] = reason
        report["total_gex"] = total_gex
        report["realized_pnl"] = round(daily_pnl, 2)
        report["open_mtm"] = round(open_mtm, 2)
        report["combined_pnl"] = round(combined_pnl, 2)

        return not is_killed, report

if __name__ == "__main__":
    if os.name == 'nt':
        selector = selectors.SelectSelector()
        loop = asyncio.SelectorEventLoop(selector)
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
        
    worker = AutopilotWorker()
    loop.run_until_complete(worker.run())
