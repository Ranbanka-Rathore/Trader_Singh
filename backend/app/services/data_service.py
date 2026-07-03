import asyncio
import logging
import json
import pandas as pd
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Dict, Any, Optional
from backend.app.services.redis_service import redis_service
from backend.app.services.broker_service import broker_service
from backend.app.db.database import engine as db_engine
from backend.app.db.models import MarketIndicator, Candle
from institutional_edge import InstitutionalEdgeEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

logger = logging.getLogger("DataService")

class DataService:
    def __init__(self):
        self.broker = broker_service.get_broker()
        self.edge_engine = InstitutionalEdgeEngine()
        self.timeframes = [3, 5, 15]
        self.async_session = sessionmaker(
            db_engine(), class_=AsyncSession, expire_on_commit=False
        )
        self._candle_buffer = {} # { (ticker, timeframe, timestamp): candle_data }
        self._flush_task = None
        self._lock = asyncio.Lock()

    async def _start_flush_task(self):
        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._periodic_flush())

    async def _periodic_flush(self):
        while True:
            await asyncio.sleep(5) # Flush every 5 seconds
            try:
                await self.flush_candles()
            except Exception as e:
                logger.error(f"Error in periodic candle flush: {e}")

    async def ingest_tick(self, ticker: str, price: float, volume: int = 1):
        """
        Stores raw tick in Redis list and updates the in-memory candle buffer.
        """
        # Ensure flush task is running
        if self._flush_task is None:
            await self._start_flush_task()

        timestamp = datetime.now(timezone.utc)
        tick_data = {"t": timestamp.isoformat(), "p": price, "v": volume}
        await redis_service.client.lpush(f"ticks:{ticker}", json.dumps(tick_data))
        await redis_service.client.ltrim(f"ticks:{ticker}", 0, 999)
        
        # Update buffer instead of immediate DB write
        await self.buffer_candle_tick(ticker, timestamp, price, volume)

    async def buffer_candle_tick(self, ticker: str, timestamp: datetime, price: float, volume: int):
        """Updates the in-memory candle buffer."""
        timeframes = ["1m", "5m"]
        async with self._lock:
            for tf in timeframes:
                if tf == "1m":
                    candle_ts = timestamp.replace(second=0, microsecond=0)
                elif tf == "5m":
                    minute = (timestamp.minute // 5) * 5
                    candle_ts = timestamp.replace(minute=minute, second=0, microsecond=0)
                else:
                    continue

                key = (ticker, tf, candle_ts)
                if key not in self._candle_buffer:
                    self._candle_buffer[key] = {
                        "open": Decimal(str(price)),
                        "high": Decimal(str(price)),
                        "low": Decimal(str(price)),
                        "close": Decimal(str(price)),
                        "volume": volume
                    }
                else:
                    data = self._candle_buffer[key]
                    data["high"] = max(data["high"], Decimal(str(price)))
                    data["low"] = min(data["low"], Decimal(str(price)))
                    data["close"] = Decimal(str(price))
                    data["volume"] += volume

    async def flush_candles(self):
        """Persists buffered candles to the database."""
        async with self._lock:
            if not self._candle_buffer:
                return
            
            buffer_to_flush = self._candle_buffer
            self._candle_buffer = {}

        async with self.async_session() as session:
            try:
                for (ticker, tf, ts), data in buffer_to_flush.items():
                    # Check if candle exists in DB
                    result = await session.execute(
                        select(Candle).where(
                            (Candle.ticker == ticker) & 
                            (Candle.timestamp == ts) & 
                            (Candle.timeframe == tf)
                        )
                    )
                    candle = result.scalars().first()
                    
                    if candle:
                        candle.high = max(candle.high, data["high"])
                        candle.low = min(candle.low, data["low"])
                        candle.close = data["close"]
                        candle.volume += data["volume"]
                    else:
                        candle = Candle(
                            timestamp=ts,
                            ticker=ticker,
                            timeframe=tf,
                            open=data["open"],
                            high=data["high"],
                            low=data["low"],
                            close=data["close"],
                            volume=data["volume"]
                        )
                        session.add(candle)
                
                await session.commit()
                # logger.info(f"Successfully flushed {len(buffer_to_flush)} candle updates to DB.")
            except Exception as e:
                logger.error(f"Error flushing candles to DB: {e}")
                await session.rollback()
                # Restore buffer on failure to avoid data loss
                async with self._lock:
                    for key, data in buffer_to_flush.items():
                        if key not in self._candle_buffer:
                            self._candle_buffer[key] = data
                        else:
                            # Merge back
                            existing = self._candle_buffer[key]
                            existing["high"] = max(existing["high"], data["high"])
                            existing["low"] = min(existing["low"], data["low"])
                            existing["close"] = data["close"]
                            existing["volume"] += data["volume"]

    async def aggregate_latest_candle(self, ticker: str, timestamp: datetime, price: float, volume: int):
        """DEPRECATED: Use buffer_candle_tick + flush_candles instead."""
        await self.buffer_candle_tick(ticker, timestamp, price, volume)

    async def get_latest_candles(self, ticker: str, timeframe: str = "5m", limit: int = 100) -> pd.DataFrame:
        """
        Fetches candles from DB. If not enough data, falls back to yfinance (for dev/sim mode).
        """
        async with self.async_session() as session:
            try:
                result = await session.execute(
                    select(Candle)
                    .where((Candle.ticker == ticker) & (Candle.timeframe == timeframe))
                    .order_by(Candle.timestamp.desc())
                    .limit(limit)
                )
                candles = result.scalars().all()
                
                if len(candles) >= 10: # Lowered requirement significantly for system boot
                    df = pd.DataFrame([{
                        "Open": float(c.open),
                        "High": float(c.high),
                        "Low": float(c.low),
                        "Close": float(c.close),
                        "Volume": c.volume
                    } for c in reversed(candles)], index=[c.timestamp for c in reversed(candles)])
                    return df
            except Exception as e:
                logger.error(f"DB Candle Fetch Error for {ticker}: {e}")

        # Fallback removed (yfinance dependency eliminated)
        logger.warning(f"Insufficient DB candles for {ticker}. No fallback data source available.")
        return pd.DataFrame()

    async def backfill_historical_data(self, ticker: str, days: int = 5):
        """
        Harvests historical OHLCV data from Dhan and populates the Candle table.
        """
        clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
        
        # 1. Resolve Security ID and Segment
        dhan_symbol = clean_ticker
        exchange_segment = "NSE_EQ"
        
        if clean_ticker in ["NSEI", "NIFTY"]: 
            dhan_symbol, exchange_segment, security_id = "NIFTY", "IDX_I", "13"
        elif clean_ticker in ["NSEBANK", "BANKNIFTY"]: 
            dhan_symbol, exchange_segment, security_id = "BANKNIFTY", "IDX_I", "25"
        else:
            security_id = self.broker.get_equity_security_id(clean_ticker)
            
        if not security_id:
            logger.error(f"Cannot backfill {ticker}: Security ID not found.")
            return False

        # 2. Define Date Range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # 3. Fetch 1m Data from Dhan
        raw_data = await self.broker.get_historical_intraday(
            dhan_symbol, security_id, exchange_segment,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )
        
        if not raw_data:
            return False

        # 4. Process and Save to DB
        async with self.async_session() as session:
            try:
                # Dhan returns a dict of lists: {"open": [...], "high": [...], ...}
                if not isinstance(raw_data, dict) or "open" not in raw_data:
                    logger.error(f"Unexpected data format from Dhan for {ticker}: {type(raw_data)}")
                    return False

                df_raw = pd.DataFrame(raw_data)
                records_added = 0
                
                for _, row in df_raw.iterrows():
                    raw_ts = row.get('start_Time') or row.get('timestamp')
                    if raw_ts is None: continue
                    
                    if isinstance(raw_ts, (int, float)):
                        unit = 'ms' if raw_ts > 1e11 else 's'
                        ts = pd.to_datetime(raw_ts, unit=unit)
                    else:
                        ts = pd.to_datetime(raw_ts)

                    # Create 1m Candle
                    candle = Candle(
                        timestamp=ts,
                        ticker=ticker,
                        timeframe="1m",
                        open=Decimal(str(row['open'])),
                        high=Decimal(str(row['high'])),
                        low=Decimal(str(row['low'])),
                        close=Decimal(str(row['close'])),
                        volume=int(row.get('volume', 0))
                    )
                    session.add(candle)
                    records_added += 1
                
                await session.commit()
                logger.info(f"✅ Backfilled {records_added} 1m candles for {ticker}")
                
                # 5. Aggregate 5m candles from the 1m data
                await self.generate_5m_from_1m(ticker, session)
                return True
                
            except Exception as e:
                logger.error(f"Error saving backfill for {ticker}: {e}")
                await session.rollback()
                return False

    async def generate_5m_from_1m(self, ticker: str, session: AsyncSession):
        """Helper to build 5m candles from existing 1m database records."""
        logger.info(f"Aggregating 5m candles for {ticker}...")
        try:
            # Fetch all 1m candles for this ticker
            result = await session.execute(
                select(Candle).where(
                    (Candle.ticker == ticker) & (Candle.timeframe == "1m")
                ).order_by(Candle.timestamp.asc())
            )
            candles_1m = result.scalars().all()
            
            if not candles_1m: return
            
            data_list = []
            for c in candles_1m:
                ts = c.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                data_list.append({
                    "timestamp": ts,
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": c.volume
                })
            
            df = pd.DataFrame(data_list)
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            df.set_index("timestamp", inplace=True)
            
            # Resample to 5m
            resampled = df.resample('5min', label='left').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            
            # Save 5m candles
            for ts, row in resampled.iterrows():
                # Ensure ts is aware
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                candle_5m = Candle(
                    timestamp=ts,
                    ticker=ticker,
                    timeframe="5m",
                    open=Decimal(str(row['open'])),
                    high=Decimal(str(row['high'])),
                    low=Decimal(str(row['low'])),
                    close=Decimal(str(row['close'])),
                    volume=int(row['volume'])
                )
                session.add(candle_5m)
            
            await session.commit()
            logger.info(f"✅ Generated {len(resampled)} 5m candles for {ticker}")
        except Exception as e:
            logger.error(f"Error aggregating 5m candles: {e}")
            await session.rollback()

    async def run_autotrender_cycle(self):
        """
        Calculates GEX, POC, and PCR and persists to DB/Redis.
        """
        try:
            # Primary Tracking: NIFTY
            dhan_ticker = "NIFTY"
            sec_id = "13"
            opt_type = "OPTIDX"
            
            # 1. Fetch Option Chain
            spot, df_chain = await self.broker.get_clean_option_chain(sec_id, opt_type)
            if df_chain is None or df_chain.empty:
                return

            # 2. Institutional Logic (GEX/POC)
            expiry = datetime.now().date() + timedelta(days=1) 
            
            gex_matrix = self.edge_engine.calculate_gex_matrix(spot, df_chain, expiry)
            total_gex = gex_matrix['Total_GEX'].sum() if not gex_matrix.empty else 0.0
            pcr = df_chain['Put_COI'].sum() / df_chain['Call_COI'].sum() if df_chain['Call_COI'].sum() > 0 else 1.0
            
            # --- INSTITUTIONAL GAMMA LEVELS ---
            gamma_levels = self.edge_engine.identify_gamma_levels(gex_matrix)
            
            # --- DASHBOARD BIAS CALCULATION ---
            bias = "NEUTRAL"
            if pcr >= 1.25: bias = "BULLISH"
            elif pcr <= 1.00: bias = "BEARISH"

            # 3. Buffering
            snapshot = {
                "ticker": dhan_ticker,
                "price": round(spot, 2),
                "total_gex": round(float(total_gex), 2),
                "pcr": round(float(pcr), 2),
                "coi_pcr": round(float(pcr), 2), 
                "bias": bias,
                "gamma_flip": gamma_levels.get('gamma_flip', 0.0),
                "call_wall": gamma_levels.get('call_wall', 0.0),
                "put_wall": gamma_levels.get('put_wall', 0.0),
                "timestamp": datetime.now().isoformat(),
                "source": "DHAN_LIVE"
            }
            await redis_service.set_json(f"market_snapshot:{dhan_ticker}", snapshot)
            await redis_service.publish("market_updates", {"type": "SNAPSHOT", "data": snapshot})
            
            # 4. Persistence to MarketIndicator
            async with self.async_session() as session:
                indicator = MarketIndicator(
                    timestamp=datetime.now(),
                    ticker=dhan_ticker,
                    timeframe=1, # 1-minute snapshot
                    pcr=pcr,
                    price=spot,
                    total_gex=total_gex
                )
                session.add(indicator)
                await session.commit()
            
        except Exception as e:
            logger.error(f"Error in autotrender cycle: {e}")

# Singleton
data_service = DataService()
