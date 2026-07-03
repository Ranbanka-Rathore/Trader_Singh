import logging
import asyncio
from decimal import Decimal
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.services.broker_service import broker_service
from backend.app.services.database_service import database_service
from backend.app.core.risk_shield import RiskShield
from backend.app.db.models import OpenPosition

logger = logging.getLogger("DeltaHedger")

class DeltaHedger:
    def __init__(self, target_delta: float = 0.0, delta_tolerance_lots: float = 0.15):
        self.target_delta = target_delta
        self.tolerance_lots = delta_tolerance_lots
        self.shield = RiskShield()

    async def audit_and_hedge(self, session: AsyncSession):
        """
        Calculates total net delta of open positions and hedges using Index Futures.
        """
        try:
            # 1. Fetch Open Positions
            open_positions = await database_service.get_open_positions(session)
            if not open_positions:
                logger.info("📊 Delta Hedger: No open positions. Hedging not required.")
                return

            # Group open positions by ticker
            ticker_groups = {}
            for pos in open_positions:
                ticker = pos.ticker
                ticker_groups.setdefault(ticker, []).append(pos)

            # 2. Audit each asset class separately
            for ticker, positions in ticker_groups.items():
                # Only hedge liquid stock indices (NIFTY / BANKNIFTY)
                clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
                if clean_ticker not in ["NIFTY", "BANKNIFTY", "NSEI", "NSEBANK"]:
                    continue

                lot_size = self.shield.get_lot_size(clean_ticker)
                
                # Sum the actual Delta position size (Delta * Lots * LotSize)
                total_delta_shares = 0.0
                for pos in positions:
                    delta = float(pos.net_delta or 0.0)
                    lots = int(pos.lots_sized or 1)
                    total_delta_shares += delta * lots * lot_size

                # Express total delta in terms of lots
                total_delta_lots = total_delta_shares / lot_size
                logger.info(f"📊 Delta Hedger [{clean_ticker}]: Net Delta is {total_delta_lots:.3f} lots ({total_delta_shares:.2f} shares)")

                # 3. Check if net delta lots exceed the tolerance threshold
                if abs(total_delta_lots - self.target_delta) >= self.tolerance_lots:
                    drift_lots = total_delta_lots - self.target_delta
                    
                    # Round hedge order to the nearest whole contract lot
                    hedge_lots = round(abs(drift_lots))
                    if hedge_lots < 1:
                        continue

                    # Decide direction: if drift is positive, we are long delta, so we SELL futures to hedge
                    side = "SELL" if drift_lots > 0 else "BUY"
                    
                    logger.warning(f"🚨 Delta Hedger [{clean_ticker}]: Drift of {drift_lots:.3f} lots exceeds tolerance ({self.tolerance_lots} lots). Placing {side} futures order of {hedge_lots} contract(s)...")

                    # Place the hedge order via Dhan
                    broker = broker_service.get_broker()
                    
                    # Resolve Futures ID dynamically from Master Scrip
                    sec_id = broker.get_futures_security_id(clean_ticker)
                    
                    if sec_id:
                        # Place order on NSE Futures (Dhan SDK segment value is NSE_FNO)
                        order_id = await broker.place_order(
                            security_id=sec_id,
                            exchange_segment="NSE_FNO",
                            side=side,
                            order_type="MARKET",
                            quantity=hedge_lots * lot_size
                        )
                        if order_id:
                            logger.info(f"   ✅ Delta Hedger [{clean_ticker}]: Hedge order executed successfully! ID: {order_id}")
                        else:
                            logger.error(f"   ❌ Delta Hedger [{clean_ticker}]: Hedge order placement failed.")
                    else:
                        logger.error(f"   ❌ Delta Hedger [{clean_ticker}]: Could not resolve Futures contract security ID.")

        except Exception as e:
            logger.error(f"❌ Delta Hedger Error: {e}", exc_info=True)

delta_hedger = DeltaHedger()
