from datetime import datetime, time
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Column, DateTime, Text, Numeric, Time, JSON
from decimal import Decimal

class Trade(SQLModel, table=True):
    __tablename__ = "trades"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(max_length=20)
    strategy_type: Optional[str] = Field(default=None, max_length=50)
    spot_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    leg_1_sell: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    leg_2_buy: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    net_credit_per_share: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    max_risk_per_share: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    risk_reward_ratio: Optional[str] = Field(default=None, max_length=20)
    win_probability: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(5, 2)))
    learning_context: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    vol_surge_multiplier: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 4)))
    coi_pcr: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 4)))
    bias: Optional[str] = Field(default=None, max_length=10)
    execution_time: Optional[time] = Field(default=None, sa_column=Column(Time))
    mode: Optional[str] = Field(default=None, max_length=10)
    lots_sized: Optional[int] = Field(default=None)
    entry_date: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    entry_spot_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    highest_seen: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    lowest_seen: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    dynamic_sl: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    
    # Greeks
    net_delta: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_gamma: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_theta: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_vega: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))

    exit_date: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    exit_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    exit_reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    realized_pnl: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))

class OpenPosition(SQLModel, table=True):
    __tablename__ = "open_positions"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(max_length=20)
    strategy_type: Optional[str] = Field(default=None, max_length=50)
    spot_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    leg_1_sell: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    leg_2_buy: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    net_credit_per_share: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    max_risk_per_share: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    risk_reward_ratio: Optional[str] = Field(default=None, max_length=20)
    win_probability: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(5, 2)))
    learning_context: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    vol_surge_multiplier: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 4)))
    coi_pcr: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 4)))
    bias: Optional[str] = Field(default=None, max_length=10)
    execution_time: Optional[time] = Field(default=None, sa_column=Column(Time))
    mode: Optional[str] = Field(default=None, max_length=10)
    lots_sized: Optional[int] = Field(default=None)
    entry_date: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    entry_spot_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    highest_seen: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    dynamic_sl: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    
    # Firefighting
    is_adjusted: Optional[bool] = Field(default=False)
    adjustment_count: Optional[int] = Field(default=0)
    original_net_credit: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    adjusted_net_credit: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    
    # Greeks
    net_delta: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_gamma: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_theta: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))
    net_vega: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(10, 4)))

class SignalAudit(SQLModel, table=True):
    __tablename__ = "signal_audit"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime(timezone=True)))
    ticker: str = Field(max_length=20)
    pa_status: str = Field(max_length=50)
    pcr: Decimal = Field(sa_column=Column(Numeric(10, 2)))
    gex_mn: Decimal = Field(sa_column=Column(Numeric(15, 2)))
    ml_score: Decimal = Field(sa_column=Column(Numeric(5, 2)))
    committee_verdict: str = Field(max_length=20)
    committee_reasoning: Optional[str] = Field(default=None, sa_column=Column(Text))
    backtester_rule_match: Optional[str] = Field(default=None, max_length=100)

class MarketIndicator(SQLModel, table=True):
    __tablename__ = "market_indicators"
    
    # Existing table doesn't have a PK, so we use a composite or just define it for SQLModel
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True), primary_key=True))
    ticker: str = Field(sa_column=Column(Text, primary_key=True))
    timeframe: int = Field(sa_column=Column(Numeric, primary_key=True)) # Use Numeric for Integer if needed, but Integer is better
    call_oi: Optional[Decimal] = Field(sa_column=Column(Numeric(20, 0)))
    put_oi: Optional[Decimal] = Field(sa_column=Column(Numeric(20, 0)))
    oi_diff: Optional[Decimal] = Field(sa_column=Column(Numeric(20, 0)))
    pcr: Optional[Decimal] = Field(sa_column=Column(Numeric(15, 4)))
    vwap: Optional[Decimal] = Field(sa_column=Column(Numeric(15, 2)))
    price: Optional[Decimal] = Field(sa_column=Column(Numeric(15, 2)))
    total_gex: Optional[Decimal] = Field(sa_column=Column(Numeric(25, 2)))
    poc: Optional[Decimal] = Field(sa_column=Column(Numeric(15, 2)))

class OrderAudit(SQLModel, table=True):
    """One row per broker order leg. A multi-leg spread shares one basket_id.

    This is the immutable audit trail Fable's Phase 2 item 13 requires: every
    order gets a UUID, timestamps, legs, status transitions, and fills — in both
    PAPER and LIVE mode (paper orders carry mode='PAPER' and PAPER-* order ids).
    """
    __tablename__ = "order_audit"

    id: Optional[int] = Field(default=None, primary_key=True)
    basket_id: str = Field(max_length=40, index=True)     # UUID shared by all legs of a spread
    position_id: Optional[int] = Field(default=None)      # open_positions.id once linked
    ticker: str = Field(max_length=20)
    strategy_type: Optional[str] = Field(default=None, max_length=50)
    intent: str = Field(max_length=10)                    # ENTRY | EXIT | UNWIND
    mode: str = Field(max_length=10)                      # PAPER | LIVE

    # Leg description
    leg_index: int = Field(default=0)
    side: str = Field(max_length=4)                       # BUY | SELL
    opt_type: str = Field(max_length=4)                   # CE | PE | FUT
    strike: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    expiry: Optional[str] = Field(default=None, max_length=12)
    security_id: Optional[str] = Field(default=None, max_length=20)
    trading_symbol: Optional[str] = Field(default=None, max_length=60)
    exchange_segment: Optional[str] = Field(default=None, max_length=12)
    quantity: int = Field(default=0)                      # units (lots * lot_size)

    # Order lifecycle
    broker_order_id: Optional[str] = Field(default=None, max_length=60)
    status: str = Field(default="PENDING", max_length=16) # PENDING/PLACED/FILLED/REJECTED/CANCELLED/TIMEOUT/UNWOUND
    limit_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    fill_price: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(15, 2)))
    placed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    detail: Optional[str] = Field(default=None, sa_column=Column(Text))  # rejection reason / notes


class Candle(SQLModel, table=True):
    __tablename__ = "candles"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(sa_column=Column(DateTime(timezone=True), index=True))
    ticker: str = Field(max_length=20, index=True)
    timeframe: str = Field(max_length=10) # e.g., "1m", "5m"
    open: Decimal = Field(sa_column=Column(Numeric(15, 2)))
    high: Decimal = Field(sa_column=Column(Numeric(15, 2)))
    low: Decimal = Field(sa_column=Column(Numeric(15, 2)))
    close: Decimal = Field(sa_column=Column(Numeric(15, 2)))
    volume: int = Field(default=0)
