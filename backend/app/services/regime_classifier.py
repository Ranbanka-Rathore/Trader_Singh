import logging
import datetime
import numpy as np
import pandas as pd
from decimal import Decimal
from sqlalchemy import select
from backend.app.db.models import Candle, MarketIndicator
from backend.app.services.redis_service import redis_service

logger = logging.getLogger("RegimeClassifier")

class RegimeClassifier:
    def __init__(self):
        pass

    def calculate_adx_value(self, df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculates Wilder's Average Directional Index (ADX) from daily candles.
        """
        if len(df) < period * 2:
            # Not enough days, default to a neutral/chop indicator
            return 15.0

        df = df.copy()
        df['up'] = df['high'].diff()
        df['down'] = -df['low'].diff()
        
        df['+dm'] = 0.0
        df.loc[(df['up'] > df['down']) & (df['up'] > 0), '+dm'] = df['up']
        
        df['-dm'] = 0.0
        df.loc[(df['down'] > df['up']) & (df['down'] > 0), '-dm'] = df['down']
        
        # True Range
        df['tr0'] = abs(df['high'] - df['low'])
        df['tr1'] = abs(df['high'] - df['close'].shift(1))
        df['tr2'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
        
        df['tr_sum'] = df['tr'].rolling(window=period).sum()
        df['+dm_sum'] = df['+dm'].rolling(window=period).sum()
        df['-dm_sum'] = df['-dm'].rolling(window=period).sum()
        
        df['+di'] = 100 * (df['+dm_sum'] / df['tr_sum'].replace(0, 1))
        df['-di'] = 100 * (df['-dm_sum'] / df['tr_sum'].replace(0, 1))
        
        df['dx'] = 100 * abs(df['+di'] - df['-di']) / (df['+di'] + df['-di']).replace(0, 1)
        df['adx'] = df['dx'].rolling(window=period).mean()
        
        val = df['adx'].iloc[-1]
        return float(val) if not pd.isna(val) else 15.0

    async def get_market_regime(self, session) -> str:
        """
        Loads NIFTY 5m candles, aggregates them to daily candles,
        calculates ADX and yesterday's close, checks the pre-market opening price,
        and determines the optimal strategy mode.
        """
        try:
            # 1. Load NIFTY 5m candles for the last 30 days
            limit_date = datetime.datetime.now() - datetime.timedelta(days=45)
            query = select(Candle).where(
                (Candle.ticker == "NIFTY") &
                (Candle.timeframe == "5m") &
                (Candle.timestamp >= limit_date)
            ).order_by(Candle.timestamp.asc())
            
            res = await session.execute(query)
            candles = res.scalars().all()
            
            if not candles or len(candles) < 200:
                logger.warning("⚠️ Insufficient candles in DB to classify regime. Defaulting to CALENDAR_SPREAD.")
                return "CALENDAR_SPREAD"
                
            # Convert to DataFrame
            df_5m = pd.DataFrame([c.model_dump() for c in candles])
            df_5m['timestamp'] = pd.to_datetime(df_5m['timestamp'])
            df_5m['Date'] = df_5m['timestamp'].dt.date
            
            # Aggregate to daily
            df_daily = df_5m.groupby('Date').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).reset_index()
            
            # 2. Get Yesterday's Close and Daily ADX
            yesterday_close = float(df_daily.iloc[-2]['close']) if len(df_daily) >= 2 else float(df_daily.iloc[-1]['close'])
            adx_val = self.calculate_adx_value(df_daily)
            
            # 3. Get Pre-Market Opening Price from Redis (updated at 09:08 AM)
            snap = await redis_service.get_json("market_snapshot:NIFTY")
            pre_market_open = None
            if snap and "price" in snap:
                pre_market_open = float(snap["price"])
            elif len(df_daily) > 0:
                # Fallback to the latest price in the aggregated daily if Redis is empty (useful in simulation playback)
                pre_market_open = float(df_daily.iloc[-1]['close'])
                
            if not pre_market_open:
                logger.warning("⚠️ No pre-market opening price available. Defaulting to CALENDAR_SPREAD.")
                return "CALENDAR_SPREAD"
                
            # 4. Calculate Gap %
            gap_pct = ((pre_market_open - yesterday_close) / yesterday_close) * 100.0
            
            # 5. Fetch Latest PCR
            pcr_query = select(MarketIndicator.pcr).where(MarketIndicator.ticker == "NIFTY").order_by(MarketIndicator.timestamp.desc()).limit(1)
            pcr_res = await session.execute(pcr_query)
            pcr_val = float(pcr_res.scalar() or 1.0)
            
            logger.info(f"📊 [Regime Classifier] Daily ADX: {adx_val:.2f} | Gap: {gap_pct:.2f}% | PCR: {pcr_val:.2f}")
            
            # 6. Apply Decision Tree Matrix (Maps all 7 Options Strategies)
            if pcr_val > 1.45 or pcr_val < 0.65:
                # 1. Extreme Sentiment / High Volatility -> Credit Spreads (OTM)
                selected = "CREDIT_SPREAD"
            elif gap_pct >= 0.40:
                # 2. Bullish Momentum Breakout (Overrides low EOD ADX due to strong morning gap) -> Covered Call
                selected = "COVERED_CALL"
            elif gap_pct <= -0.50:
                # 3. Bearish Momentum Breakdown -> Credit Spread (Bear Call)
                selected = "CREDIT_SPREAD"
            elif adx_val >= 22.0:
                # Strong Trending Market (inside range)
                selected = "CALENDAR_SPREAD"  # (Note: Debit Spreads removed due to whip-saw stops)
            elif adx_val >= 15.0 and adx_val < 22.0:
                # Moderate/Mild Trend
                if gap_pct >= 0.20:
                    # 5. Mild Bullish Drift -> Cash Secured Put
                    selected = "CASH_SECURED_PUT"
                elif gap_pct <= -0.20:
                    # 6. Mild Bearish Drift -> Diagonal Spread (ATM/OTM Put)
                    selected = "DIAGONAL_SPREAD"
                else:
                    # 7. Normal range-bound day -> Calendar Spread (Default)
                    selected = "CALENDAR_SPREAD"
            else:
                # Low Volatility / Consolidation Chop
                if abs(gap_pct) <= 0.10:
                    # 8. Perfectly Flat Open -> Delta Neutral Straddles
                    selected = "DELTA_NEUTRAL"
                else:
                    selected = "CALENDAR_SPREAD"
                
            # Check Low Margin Mode constraint
            low_margin_val = await redis_service.client.get("low_margin_only")
            low_margin_only = low_margin_val.decode() == "true" if low_margin_val else False
            
            if low_margin_only:
                if selected == "COVERED_CALL":
                    logger.warning("⚠️ [RegimeClassifier] Low Margin Mode Active. Mapping COVERED_CALL -> DEBIT_SPREAD")
                    selected = "DEBIT_SPREAD"
                elif selected == "CASH_SECURED_PUT":
                    logger.warning("⚠️ [RegimeClassifier] Low Margin Mode Active. Mapping CASH_SECURED_PUT -> CREDIT_SPREAD")
                    selected = "CREDIT_SPREAD"
                elif selected == "DELTA_NEUTRAL":
                    logger.warning("⚠️ [RegimeClassifier] Low Margin Mode Active. Mapping DELTA_NEUTRAL -> CALENDAR_SPREAD")
                    selected = "CALENDAR_SPREAD"
                
            logger.info(f"🧠 [Regime Classifier] Optimal strategy selected: {selected}")
            return selected
            
        except Exception as e:
            logger.error(f"❌ Error in RegimeClassifier: {e}")
            return "CALENDAR_SPREAD"

regime_classifier = RegimeClassifier()
