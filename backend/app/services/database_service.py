from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.app.db.models import OpenPosition, Trade, SignalAudit, MarketIndicator, Candle
from typing import List, Dict, Any, Optional

class DatabaseService:
    async def add_open_position(self, session: AsyncSession, position_data: dict) -> OpenPosition:
        """Saves a new active position to the database."""
        position = OpenPosition(**position_data)
        session.add(position)
        await session.commit()
        await session.refresh(position)
        return position

    async def get_open_positions(self, session: AsyncSession) -> List[OpenPosition]:
        """Returns all open positions."""
        result = await session.execute(select(OpenPosition))
        return list(result.scalars().all())

    async def add_signal_audit(self, session: AsyncSession, audit_data: dict) -> SignalAudit:
        """Logs a signal check or decision audit."""
        audit = SignalAudit(**audit_data)
        session.add(audit)
        await session.commit()
        await session.refresh(audit)
        return audit

    async def get_signal_audits(self, session: AsyncSession, limit: int = 50) -> List[SignalAudit]:
        """Returns recent signal audits in reverse chronological order."""
        result = await session.execute(
            select(SignalAudit)
            .order_by(SignalAudit.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save_market_indicator(self, session: AsyncSession, indicator_data: dict) -> MarketIndicator:
        """Persists a time-series market context snapshot."""
        indicator = MarketIndicator(**indicator_data)
        session.add(indicator)
        await session.commit()
        await session.refresh(indicator)
        return indicator

    async def close_position(self, session: AsyncSession, position_id: int, exit_data: dict) -> Optional[Trade]:
        """
        Atomically transition an OpenPosition into closed Trade history.
        Deletes the open position and logs the trade inside a single transaction.
        """
        result = await session.execute(select(OpenPosition).where(OpenPosition.id == position_id))
        pos = result.scalar_one_or_none()
        if not pos:
            return None
        
        # Dump position fields and drop the database id
        pos_dict = pos.model_dump()
        pos_dict.pop('id', None)
        
        # Merge entry fields with exit pricing and dynamic outcomes
        trade_data = {**pos_dict, **exit_data}
        
        # Standardize decimal types if passed as floats
        from decimal import Decimal
        decimal_fields = [
            "spot_price", "leg_1_sell", "leg_2_buy", "net_credit_per_share", 
            "max_risk_per_share", "win_probability", "vol_surge_multiplier", 
            "coi_pcr", "entry_spot_price", "highest_seen", "lowest_seen", 
            "dynamic_sl", "net_delta", "net_gamma", "net_theta", "net_vega", 
            "exit_price", "realized_pnl"
        ]
        for field in decimal_fields:
            if field in trade_data and trade_data[field] is not None:
                trade_data[field] = Decimal(str(trade_data[field]))
        
        trade = Trade(**trade_data)
        session.add(trade)

        # Phase 3 attribution: write the outcome back onto the SignalAudit row
        # that spawned this position (same transaction as the close).
        audit_id = (pos_dict.get("learning_context") or {}).get("signal_audit_id")
        if audit_id:
            res_a = await session.execute(select(SignalAudit).where(SignalAudit.id == int(audit_id)))
            audit = res_a.scalar_one_or_none()
            if audit:
                audit.position_id = position_id
                audit.realized_pnl = trade_data.get("realized_pnl")
                session.add(audit)

        await session.delete(pos)
        await session.commit()
        await session.refresh(trade)
        return trade

# Singleton
database_service = DatabaseService()
