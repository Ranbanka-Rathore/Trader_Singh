import os
import asyncio
import pandas as pd
import numpy as np
import datetime
import json
from typing import List, Dict, Any, Optional
from backend.app.services.broker_service import broker_service
from backend.app.services.data_service import data_service
from backend.app.services.redis_service import redis_service
from backend.app.db.models import MarketIndicator, OpenPosition
from ml_approval_engine import MLApprovalEngine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import logging

logger = logging.getLogger("QuantEngine")

class QuantEngine:
    def __init__(self):
        self.broker = broker_service.get_broker()
        self.brain_config_path = "brain_config.json"
        self.ml_engines = {}
        self._htf_memory = {} # {ticker: {"pdh": ..., "pdl": ..., "obs": []}}
        self._last_memory_refresh = None

    async def _fetch_htf_memory(self, ticker: str):
        """Fetches 5 days of historical data to build Institutional Memory."""
        try:
            clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
            dhan_symbol = clean_ticker
            exchange_segment = "NSE_EQ"
            
            if clean_ticker in ["NSEI", "NIFTY"]: 
                dhan_symbol, exchange_segment, security_id = "NIFTY", "IDX_I", "13"
            elif clean_ticker in ["NSEBANK", "BANKNIFTY"]: 
                dhan_symbol, exchange_segment, security_id = "BANKNIFTY", "IDX_I", "25"
            else:
                security_id = self.broker.get_equity_security_id(clean_ticker)

            if not security_id: return

            # Fetch 5 days of 15m data for structural analysis
            end_date = datetime.datetime.now()
            start_date = end_date - datetime.timedelta(days=7)
            
            raw_data = await self.broker.get_historical_intraday(
                dhan_symbol, security_id, exchange_segment,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d")
            )
            
            if not raw_data: return
            df = pd.DataFrame(raw_data)
            
            # Flexible timestamp parsing (handles 'start_Time' or 'timestamp')
            time_col = 'start_Time' if 'start_Time' in df.columns else 'timestamp'
            if time_col not in df.columns:
                return

            # Auto-detect unit (s vs ms)
            first_val = df[time_col].iloc[0]
            unit = 's' if first_val < 1e11 else 'ms'
            df['timestamp'] = pd.to_datetime(df[time_col], unit=unit, utc=True)
            df.set_index('timestamp', inplace=True)

            # 1. Previous Day High/Low (Ensure we use the most recent completed day before today)
            df['date'] = df.index.date
            dates = sorted(df['date'].unique())
            today_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
            df_hist = df[df['date'] < today_ist]
            hist_dates = sorted(df_hist['date'].unique())
            
            if len(hist_dates) >= 1:
                prev_day = hist_dates[-1]
            elif len(dates) >= 2:
                # Fallback if all data is somehow today or empty historical filter
                prev_day = dates[-2]
            else:
                return

            df_prev = df[df['date'] == prev_day]
            pdh = float(df_prev['high'].max())
            pdl = float(df_prev['low'].min())
            
            # 2. Point of Control (POC) - Volume Profile
            # Multi-day POC (HTF)
            price_min = df['low'].min()
            price_max = df['high'].max()
            bins = np.linspace(price_min, price_max, 50)
            df['bin'] = pd.cut(df['close'], bins=bins)
            
            # Use volume if available and non-zero, else use frequency
            has_volume = 'volume' in df.columns and df['volume'].sum() > 0
            if has_volume:
                poc_bin = df.groupby('bin', observed=True)['volume'].sum().idxmax()
            else:
                poc_bin = df.groupby('bin', observed=True).size().idxmax()
            poc_level = float(poc_bin.mid)
            
            # Session POC (Today only)
            today_date = dates[-1]
            df_today = df[df['date'] == today_date].copy()
            session_poc = poc_level # Fallback
            if not df_today.empty and len(df_today) > 1:
                try:
                    t_min = df_today['low'].min()
                    t_max = df_today['high'].max()
                    if t_max > t_min:
                        t_bins = np.linspace(t_min, t_max, 30)
                        df_today['t_bin'] = pd.cut(df_today['close'], bins=t_bins)
                        
                        t_has_volume = 'volume' in df_today.columns and df_today['volume'].sum() > 0
                        if t_has_volume:
                            t_poc_bin = df_today.groupby('t_bin', observed=True)['volume'].sum().idxmax()
                        else:
                            t_poc_bin = df_today.groupby('t_bin', observed=True).size().idxmax()
                        session_poc = float(t_poc_bin.mid)
                except Exception as e:
                    logger.warning(f"Session POC calc failed for {ticker}: {e}")

            # 3. Weekly High/Low
            weekly_h = float(df['high'].max())
            weekly_l = float(df['low'].min())

            # 4. HTF 15m Swing Levels (Liquidity Pools)
            df['Swing_H'] = df['high'].rolling(window=10).max().shift(1)
            df['Swing_L'] = df['low'].rolling(window=10).min().shift(1)
            htf_highs = df['Swing_H'].dropna().unique().tolist()[-10:] # Last 10 peaks
            htf_lows = df['Swing_L'].dropna().unique().tolist()[-10:] # Last 10 valleys

            # 5. Order Block Detection (Supply/Demand Zones)
            df['body'] = abs(df['close'] - df['open'])
            avg_body = df['body'].rolling(30).mean()
            obs = []
            
            for i in range(1, len(df)):
                if df['body'].iloc[i] > (avg_body.iloc[i] * 2.5):
                    origin_price = float(df['open'].iloc[i])
                    is_bullish = df['close'].iloc[i] > df['open'].iloc[i]
                    
                    obs.append({
                        "type": "DEMAND" if is_bullish else "SUPPLY",
                        "top": origin_price * 1.001 if is_bullish else float(df['high'].iloc[i]),
                        "bottom": float(df['low'].iloc[i]) if is_bullish else origin_price * 0.999,
                        "strength": round(float(df['body'].iloc[i] / avg_body.iloc[i]), 1)
                    })
            
            obs_supply = [ob for ob in obs if ob['type'] == "SUPPLY"][-3:]
            obs_demand = [ob for ob in obs if ob['type'] == "DEMAND"][-3:]
            
            self._htf_memory[ticker] = {
                "pdh": pdh,
                "pdl": pdl,
                "poc": poc_level,
                "session_poc": session_poc,
                "weekly_h": weekly_h,
                "weekly_l": weekly_l,
                "htf_highs": htf_highs,
                "htf_lows": htf_lows,
                "zones": obs_supply + obs_demand,
                "refreshed_at": datetime.datetime.now()
            }

        except Exception as e:
            logger.error(f"⚠️ SMC Memory Error for {ticker}: {e}")

    def get_ml_engine(self, ticker: str, timeframe: int = 1) -> MLApprovalEngine:
        key = f"{ticker}_{timeframe}"
        if key not in self.ml_engines:
            self.ml_engines[key] = MLApprovalEngine(ticker=ticker, timeframe=timeframe)
        return self.ml_engines[key]

    async def get_latest_radar_signal(self, session: AsyncSession, ticker: str = "NIFTY", timeframe: int = 3) -> Dict[str, Any]:
        """Reads the database to get the live institutional signal."""
        try:
            result = await session.execute(
                select(MarketIndicator)
                .where((MarketIndicator.ticker == ticker) & (MarketIndicator.timeframe == timeframe))
                .order_by(MarketIndicator.timestamp.desc())
                .limit(1)
            )
            latest_data = result.scalar_one_or_none()
            
            if not latest_data:
                return {"pcr_signal": "WAIT", "vwap_signal": "WAIT", "pcr_value": 1.0}
            
            pcr = float(latest_data.pcr)
            price = float(latest_data.price)
            vwap = float(latest_data.vwap)
            
            pcr_signal = "BUY" if pcr >= 1.15 else "SELL" if pcr <= 0.85 else "WAIT"
            vwap_signal = "BUY" if price > vwap else "SELL" if price < vwap else "WAIT"
            
            return {
                "pcr_signal": pcr_signal, 
                "vwap_signal": vwap_signal, 
                "pcr_value": pcr
            }
        except Exception:
            return {"pcr_signal": "WAIT", "vwap_signal": "WAIT", "pcr_value": 1.0}

    def _load_brain_config(self) -> Dict:
        if os.path.exists(self.brain_config_path):
            try:
                with open(self.brain_config_path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _calculate_coi_pcr(self, current_spot_price: float, df_chain: pd.DataFrame) -> Dict[str, Any]:
        """Smart Indexing: Dynamically finds ATM and slices +/- 10 strikes."""
        try:
            df_chain = df_chain.copy()
            df_chain['Distance'] = abs(df_chain['Strike'] - current_spot_price)
            atm_idx = df_chain['Distance'].idxmin()
            
            start_idx = max(0, atm_idx - 10)
            end_idx = min(len(df_chain), atm_idx + 11)
            active_strikes_df = df_chain.iloc[start_idx:end_idx]
            
            total_put_coi = active_strikes_df['Put_COI'].sum()
            total_call_coi = active_strikes_df['Call_COI'].sum()
            
            if total_call_coi > 0:
                live_pcr = total_put_coi / total_call_coi
            elif total_call_coi < 0 and total_put_coi > 0:
                live_pcr = 9.99 
            else:
                live_pcr = 1.0
                
            bias = "NEUTRAL"
            if live_pcr >= 1.25: bias = "BULLISH"
            elif live_pcr <= 1.00: bias = "BEARISH"
            
            return {"coi_pcr": round(float(live_pcr), 2), "bias": bias}
        except Exception:
            return {"coi_pcr": 1.0, "bias": "NEUTRAL"}

    async def analyze_universe(self, universe: List[str]) -> List[Dict[str, Any]]:
        """
        Scans assets, engineers institutional features, checks patterns and volumes.
        Uses surgical session management to prevent connection timeouts.
        """
        # --- V8 UPGRADE: MARKET HOUR ENTRY TIME-GUARD (09:15 AM to 03:00 PM IST) ---
        # On weekdays (Mon-Fri), we strictly block entry scans before 09:15 AM or after 03:00 PM IST.
        # This prevents pre-market and EOD entries in both live and paper accounts.
        # Outside weekday hours, testing is allowed if dev_mode is active.
        current_time_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        is_entry_weekday = current_time_ist.weekday() < 5
        is_entry_hour = (datetime.time(9, 15) <= current_time_ist.time() < datetime.time(15, 0))
        
        # Fetch dev_mode status from Redis
        dev_mode_val = await redis_service.get_json("dev_mode")
        is_dev_mode = bool(dev_mode_val) if dev_mode_val is not None else False
        
        # Allow testing outside market hours ONLY if dev_mode is True and it's a weekend or post-market (after 03:30 PM IST)
        is_testing_allowed = is_dev_mode and (not is_entry_weekday or current_time_ist.time() >= datetime.time(15, 30))
        
        if is_entry_weekday and not is_entry_hour and not is_testing_allowed:
            logger.info(f"⏳ [Time Guard] Skipping analyze_universe (outside 09:15 AM - 03:00 PM IST on weekday).")
            return []

        logger.info(f"DEBUG: analyze_universe started for {universe}")
        from backend.app.db.database import engine as db_engine, sessionmaker
        async_session = sessionmaker(db_engine(), class_=AsyncSession, expire_on_commit=False)
        
        passed_assets = []
        brain = self._load_brain_config()

        # 1. Fetch HTF Memory for all assets FIRST (External API calls - No Session)
        for ticker in universe:
            mem = self._htf_memory.get(ticker)
            # Refresh every 1 hour OR if it's a new day
            is_new_day = mem and (datetime.datetime.now().date() != mem['refreshed_at'].date())
            if not mem or (datetime.datetime.now() - mem['refreshed_at']).total_seconds() > 3600 or is_new_day:
                logger.info(f"DEBUG: Fetching HTF Memory for {ticker}")
                await self._fetch_htf_memory(ticker)

        # 2. Get active positions (Short Session)
        async with async_session() as session:
            pos_result = await session.execute(select(OpenPosition.ticker))
            active_tickers = [p for p in pos_result.scalars().all()]

        for ticker in universe:
            clean_ticker = ticker.replace("^", "").replace(".NS", "").replace(".BO", "")
            is_index = clean_ticker in ["NIFTY", "BANKNIFTY", "NSEI", "NSEBANK", "BSESN", "SENSEX"]
            has_active_position = (clean_ticker in active_tickers) or (ticker in active_tickers)

            try:
                mem = self._htf_memory.get(ticker)
                logger.info(f"DEBUG: Starting analysis for {ticker} (Index: {is_index})")
                # 3. Fetch data and analyze (Surgical Sessions)
                async with async_session() as session:
                    # Fetch historical candle data - Increased limit to 250 for EMA stability
                    df = await data_service.get_latest_candles(clean_ticker, timeframe="5m", limit=250)
                    
                    if df.empty or len(df) < 10: # Unified with DataService threshold
                        logger.info(f"DEBUG: Skipping {ticker} (Insufficient candles: {len(df) if not df.empty else 0})")
                        continue
                    
                    logger.info(f"DEBUG: Data fetched for {ticker}, processing {len(df)} candles")

                    # VWAP Calculation (Strict Intraday IST)
                    # Convert UTC index to IST for correct session grouping
                    if df.index.tz is None:
                        df.index = df.index.tz_localize('UTC')
                    df['IST_Time'] = df.index.tz_convert('Asia/Kolkata')
                    df['Date'] = df['IST_Time'].dt.date
                    
                    df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
                    df['VP'] = df['Typical_Price'] * df['Volume']
                    
                    # Use frequency if volume is zero or it is an index
                    if is_index or df['Volume'].sum() == 0:
                        df['Volume_Proxy'] = 1
                        df['VP'] = df['Typical_Price'] * df['Volume_Proxy']
                        df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
                        df['cum_vol'] = df.groupby('Date', observed=True)['Volume_Proxy'].cumsum()
                    else:
                        df['cum_vp'] = df.groupby('Date', observed=True)['VP'].cumsum()
                        df['cum_vol'] = df.groupby('Date', observed=True)['Volume'].cumsum()
                    
                    df['VWAP'] = df['cum_vp'] / df['cum_vol']
                    
                    current_vwap = float(df['VWAP'].iloc[-1])
                    current_price = float(df['Close'].iloc[-1])

                    # --- V8 UPGRADE: SESSION STRUCTURAL MEMORY ---
                    today_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date()
                    # Ensure df['Date'] is compared correctly
                    df_today = df[df['Date'] == today_ist].copy()
                    
                    df_today_prev = df_today.iloc[:-1] if len(df_today) > 1 else pd.DataFrame()
                    if not df_today_prev.empty:
                        session_high = float(df_today_prev['High'].max())
                        session_low = float(df_today_prev['Low'].min())
                    else:
                        # Start of day: use yesterday's high/low if we have memory, else use current candle as fallback (sweeps disabled)
                        if mem:
                            session_high = mem['pdh']
                            session_low = mem['pdl']
                        else:
                            session_high = float(df['High'].iloc[-1])
                            session_low = float(df['Low'].iloc[-1])

                    # Calculate true live Session POC (Point of Control for today's active session)
                    session_poc = current_vwap  # Fallback to current VWAP
                    if not df_today.empty and len(df_today) > 1:
                        try:
                            t_min = df_today['Low'].min()
                            t_max = df_today['High'].max()
                            if t_max > t_min:
                                t_bins = np.linspace(t_min, t_max, 30)
                                df_today['t_bin'] = pd.cut(df_today['Close'], bins=t_bins)
                                
                                t_has_volume = 'Volume' in df_today.columns and df_today['Volume'].sum() > 0
                                if t_has_volume:
                                    t_poc_bin = df_today.groupby('t_bin', observed=True)['Volume'].sum().idxmax()
                                else:
                                    t_poc_bin = df_today.groupby('t_bin', observed=True).size().idxmax()
                                session_poc = float(t_poc_bin.mid)
                        except Exception as e:
                            logger.warning(f"Live Session POC calculation failed for {ticker}: {e}")

                    # Update memory with active session POC
                    if mem:
                        mem['session_poc'] = session_poc

                    # 3. Structural Swing Boundaries (Local vs Session)
                    df['Swing_High'] = df['High'].rolling(window=20).max().shift(1)
                    df['Swing_Low'] = df['Low'].rolling(window=20).min().shift(1)
                    local_swing_high = float(df['Swing_High'].iloc[-1]) if not pd.isna(df['Swing_High'].iloc[-1]) else float('inf')
                    local_swing_low = float(df['Swing_Low'].iloc[-1]) if not pd.isna(df['Swing_Low'].iloc[-1]) else 0.0

                    # 1. VWAP Mean Reversion (Bollinger Bands around VWAP)
                    df['std_20'] = df['Close'].rolling(window=20).std()
                    std_val = float(df['std_20'].iloc[-1]) if not pd.isna(df['std_20'].iloc[-1]) else 0.0
                    upper_band = current_vwap + (2.0 * std_val)
                    lower_band = current_vwap - (2.0 * std_val)
                    
                    # (Re-calculating PCR early for predictive entry and pullback filters)
                    dhan_symbol = clean_ticker
                    opt_type = "OPTIDX" if is_index else "OPTSTK"
                    sec_id = "13" if clean_ticker in ["NSEI", "NIFTY"] else "25" if clean_ticker in ["NSEBANK", "BANKNIFTY"] else "1" if clean_ticker in ["BSESN", "SENSEX"] else self.broker.get_equity_security_id(clean_ticker)
                    
                    live_pcr = 1.0
                    chain_failed = True
                    if sec_id:
                        _, df_chain = await self.broker.get_clean_option_chain(sec_id, opt_type)
                        if df_chain is not None and not df_chain.empty:
                            chain_failed = False
                            pcr_data = self._calculate_coi_pcr(current_price, df_chain)
                            live_pcr = pcr_data['coi_pcr']
                    
                    pa_status = "CHOP_ZONE"
                    
                    # Define trigger groups early for check references
                    bullish_triggers = [
                        "VWAP_BULL_CROSS", "SESSION_HIGH_BREAKOUT", "SMC_DEMAND_BOUNCE", 
                        "HTF_SUPPORT_BOUNCE", "MTF_DOUBLE_BOTTOM", "VWAP_PULLBACK_LONG",
                        "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG", "LOCAL_DOUBLE_BOTTOM_PULLBACK",
                        "BULLISH_HH_BREAKOUT", "BULLISH_HL_PULLBACK"
                    ]
                    bearish_triggers = [
                        "VWAP_BEAR_CROSS", "SESSION_LOW_BREAKDOWN", "SMC_SUPPLY_REJECTION", 
                        "HTF_RESISTANCE_REJECTION", "MTF_DOUBLE_TOP", "VWAP_RALLY_SHORT",
                        "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT", "LOCAL_DOUBLE_TOP_PULLBACK",
                        "BEARISH_LL_BREAKDOWN", "BEARISH_LH_PULLBACK"
                    ]

                    # 2. Liquidity Sweep Detection
                    is_bullish_sweep = False
                    is_bearish_sweep = False
                    
                    if mem:
                        # Sweep of PDL / Session Low (within 0.15% limit)
                        target_low = min(mem['pdl'], session_low)
                        if (target_low * 0.9985 <= df['Low'].iloc[-1] < target_low) and current_price > target_low:
                            is_bullish_sweep = True
                        # Sweep of PDH / Session High (within 0.15% limit)
                        target_high = max(mem['pdh'], session_high)
                        if (target_high < df['High'].iloc[-1] <= target_high * 1.0015) and current_price < target_high:
                            is_bearish_sweep = True
                    else:
                        if (session_low * 0.9985 <= df['Low'].iloc[-1] < session_low) and current_price > session_low:
                            is_bullish_sweep = True
                        if (session_high < df['High'].iloc[-1] <= session_high * 1.0015) and current_price < session_high:
                            is_bearish_sweep = True
                    
                    # Check conditions in order of priority: sweeps first, deviations, then breakouts
                    if is_bullish_sweep:
                        pa_status = "LIQUIDITY_SWEEP_LONG"
                    elif is_bearish_sweep:
                        pa_status = "LIQUIDITY_SWEEP_SHORT"
                    elif current_price >= upper_band and df['Close'].iloc[-2] < upper_band:
                        pa_status = "VWAP_DEVIATION_SHORT"
                    elif current_price <= lower_band and df['Close'].iloc[-2] > lower_band:
                        pa_status = "VWAP_DEVIATION_LONG"
                    elif current_price <= session_low:
                        pa_status = "SESSION_LOW_BREAKDOWN"
                    elif current_price >= session_high:
                        pa_status = "SESSION_HIGH_BREAKOUT"
                    elif current_price <= local_swing_low:
                        if current_price < current_vwap and live_pcr <= 0.90:
                            pa_status = "BEARISH_LL_BREAKDOWN"
                        else:
                            pa_status = "BREAKING_SUPPORT"
                    elif current_price >= local_swing_high:
                        if current_price > current_vwap and live_pcr >= 1.15:
                            pa_status = "BULLISH_HH_BREAKOUT"
                        else:
                            pa_status = "BREAKING_RESISTANCE"

                    # VWAP Rejection Filter (User's Setup)
                    # If price is below VWAP, but was near it recently (within 0.1%) and got rejected
                    was_near_vwap = any(abs(df['High'].iloc[-5:] - df['VWAP'].iloc[-5:]) / df['VWAP'].iloc[-5:] < 0.001)
                    if pa_status == "SESSION_LOW_BREAKDOWN" and was_near_vwap and current_price < current_vwap:
                        pa_status = "VWAP_REJECTION_BREAKDOWN"

                    # 4. Ingest Brain Tuning Config (Dynamic Parameters)
                    ticker_config = brain.get(ticker, brain.get(clean_ticker, {}))
                    ml_margin_pct = ticker_config.get("double_top_bottom_margin", 0.0015)
                    ml_vol_mult = ticker_config.get("vol_surge_multiplier", 2.5)

                    # --- V8 UPGRADE: MTF CONFLUENCE & PREDICTIVE ENTRIES ---
                    mem = self._htf_memory.get(ticker)
                    smc_confluence = "NONE"
                    
                    if mem:
                        # 1. Order Block Confluence
                        for zone in mem['zones']:
                            if zone['bottom'] <= current_price <= zone['top']:
                                smc_confluence = f"SMC_{zone['type']}_ZONE"
                                break
                        
                        # 2. Key Level Confluence (0.05% margin)
                        margin = current_price * 0.0005
                        if abs(current_price - mem['pdh']) <= margin: smc_confluence = "PDH_LEVEL"
                        elif abs(current_price - mem['pdl']) <= margin: smc_confluence = "PDL_LEVEL"
                        elif abs(current_price - mem['poc']) <= margin: smc_confluence = "POC_LEVEL"
                        elif abs(current_price - mem['weekly_h']) <= margin: smc_confluence = "WEEKLY_H_LEVEL"
                        elif abs(current_price - mem['weekly_l']) <= margin: smc_confluence = "WEEKLY_L_LEVEL"

                    # 4. Reversal Pattern Sweeper (MTF Double Tops & Bottoms)
                    local_margin = current_price * ml_margin_pct
                    recent_lows = df['Low'].iloc[-50:-1] # Expanded window to 50 candles
                    recent_highs = df['High'].iloc[-50:-1]
                    
                    is_local_db = abs(float(df['Low'].iloc[-1]) - float(recent_lows.min())) <= local_margin
                    is_local_dt = abs(float(df['High'].iloc[-1]) - float(recent_highs.max())) <= local_margin
                    
                    # Cross-reference with 15m HTF Liquidity Pools
                    if mem:
                        htf_db = any(abs(current_price - lv) <= local_margin for lv in mem['htf_lows'])
                        htf_dt = any(abs(current_price - lv) <= local_margin for lv in mem['htf_highs'])
                        
                        # Only check double top/bottom reversals if we are not in a fresh session breakout
                        if pa_status not in ["SESSION_HIGH_BREAKOUT", "SESSION_LOW_BREAKDOWN"]:
                            if is_local_db and smc_confluence == "SMC_DEMAND_ZONE":
                                pa_status = "SMC_DEMAND_BOUNCE"
                            elif is_local_db and smc_confluence in ["PDL_LEVEL", "POC_LEVEL", "WEEKLY_L_LEVEL"]:
                                pa_status = "HTF_SUPPORT_BOUNCE"
                            elif is_local_dt and smc_confluence == "SMC_SUPPLY_ZONE":
                                pa_status = "SMC_SUPPLY_REJECTION"
                            elif is_local_dt and smc_confluence in ["PDH_LEVEL", "POC_LEVEL", "WEEKLY_H_LEVEL"]:
                                pa_status = "HTF_RESISTANCE_REJECTION"
                            elif is_local_db and htf_db: pa_status = "MTF_DOUBLE_BOTTOM"
                            elif is_local_dt and htf_dt: pa_status = "MTF_DOUBLE_TOP"
                            elif is_local_db:
                                if pa_status not in bullish_triggers:
                                    if current_price > current_vwap and live_pcr >= 1.15:
                                        pa_status = "LOCAL_DOUBLE_BOTTOM_PULLBACK"
                                    else:
                                        pa_status = "LOCAL_DOUBLE_BOTTOM"
                            elif is_local_dt:
                                if pa_status not in bearish_triggers:
                                    if current_price < current_vwap and live_pcr <= 0.90:
                                        pa_status = "LOCAL_DOUBLE_TOP_PULLBACK"
                                    else:
                                        pa_status = "LOCAL_DOUBLE_TOP"
                        else:
                            # If it is a breakout, still allow local pullback triggers if hit
                            if is_local_db and pa_status not in bullish_triggers:
                                if current_price > current_vwap and live_pcr >= 1.15:
                                    pa_status = "LOCAL_DOUBLE_BOTTOM_PULLBACK"
                            elif is_local_dt and pa_status not in bearish_triggers:
                                if current_price < current_vwap and live_pcr <= 0.90:
                                    pa_status = "LOCAL_DOUBLE_TOP_PULLBACK"

                    # 4b. Swing Reversals (Higher Lows & Lower Highs Pullbacks)
                    prior_swing_high = float(df['High'].iloc[-22:-2].max())
                    prior_swing_low = float(df['Low'].iloc[-22:-2].min())
                    
                    is_swing_low = df['Low'].iloc[-2] < df['Low'].iloc[-3] and df['Low'].iloc[-1] > df['Low'].iloc[-2]
                    is_swing_high = df['High'].iloc[-2] > df['High'].iloc[-3] and df['High'].iloc[-1] < df['High'].iloc[-2]
                    
                    if is_swing_low and pa_status not in bullish_triggers:
                        low_val = float(df['Low'].iloc[-2])
                        if low_val > prior_swing_low and current_price > current_vwap and live_pcr >= 1.15:
                            pa_status = "BULLISH_HL_PULLBACK"
                    elif is_swing_high and pa_status not in bearish_triggers:
                        high_val = float(df['High'].iloc[-2])
                        if high_val < prior_swing_high and current_price < current_vwap and live_pcr <= 0.90:
                            pa_status = "BEARISH_LH_PULLBACK"

                    # --- V8 UPGRADE: PREDICTIVE VWAP MEAN REVERSION ---
                    is_near_vwap = abs(current_price - current_vwap) / current_vwap <= 0.0015

                    # Predictive Triggers (Buy the dip / Sell the rip at VWAP)
                    if is_near_vwap:
                        if live_pcr >= 1.25:
                            pa_status = "VWAP_PULLBACK_LONG"
                        elif live_pcr <= 0.90:
                            pa_status = "VWAP_RALLY_SHORT"

                    # 5. Momentum Expansion (Formerly Elephant Candle) - Confirmation Only
                    df['body'] = abs(df['Close'] - df['Open'])
                    avg_body = df['body'].iloc[-11:-1].mean()
                    body_surge = float(df['body'].iloc[-1] / avg_body) if avg_body > 0 else 0
                    
                    avg_vol = df['Volume'].iloc[-11:-1].mean()
                    vol_surge = float(df['Volume'].iloc[-1] / avg_vol) if avg_vol > 0 else 1
                    
                    momentum_expansion = "NONE"
                    if body_surge >= 3.0 and vol_surge >= 3.0:
                        momentum_expansion = "BEARISH" if current_price < df['Open'].iloc[-1] else "BULLISH"

                    # 6. VWAP Momentum Triggers
                    if pa_status == "CHOP_ZONE":
                        if current_price > current_vwap and df['Close'].iloc[-2] <= df['VWAP'].iloc[-2] and (is_index or vol_surge >= 1.5):
                            pa_status = "VWAP_BULL_CROSS"
                        elif current_price < current_vwap and df['Close'].iloc[-2] >= df['VWAP'].iloc[-2] and (is_index or vol_surge >= 1.5):
                            pa_status = "VWAP_BEAR_CROSS"

                    # 7. Primary Bias Mapping
                    direction = None
                    
                    # Confluence Override: If we have momentum expansion in the same direction, boost confidence
                    # For indices, we bypass the vol_surge filter (since indices don't stream real spot volume)
                    if (pa_status in bullish_triggers) and (is_index or vol_surge >= 1.0): direction = "BULLISH"
                    elif (pa_status in bearish_triggers) and (is_index or vol_surge >= 1.0): direction = "BEARISH"

                    # 8. Query Order Book Imbalance (OBI) Filter
                    obi_score = await self.get_order_book_imbalance(clean_ticker)
                    if direction == "BULLISH" and obi_score < -0.15:
                        logger.warning(f"❌ SIGNAL BLOCKED: Bullish trigger on {clean_ticker} blocked by Sell Imbalance ({obi_score})")
                        direction = None
                    elif direction == "BEARISH" and obi_score > 0.15:
                        logger.warning(f"❌ SIGNAL BLOCKED: Bearish trigger on {clean_ticker} blocked by Buy Imbalance ({obi_score})")
                        direction = None

                    # 8. Guardrails and Live Options Interlocks
                    if direction and is_index:
                        radar = await self.get_latest_radar_signal(session, ticker=clean_ticker, timeframe=3)
                        if direction == "BULLISH" and (radar["pcr_signal"] == "SELL" or radar["vwap_signal"] == "SELL"): direction = None
                        elif direction == "BEARISH" and (radar["pcr_signal"] == "BUY" or radar["vwap_signal"] == "BUY"): direction = None

                    # 9. Real-Time Option Chain Filter (Already fetched above)
                    if direction:
                        is_explosive = vol_surge >= 2.5
                        pcr_bullish_limit = 0.80 if is_explosive else 0.90
                        pcr_bearish_limit = 1.20 if is_explosive else 1.10
                        
                        if direction == "BULLISH" and live_pcr < pcr_bullish_limit: direction = None
                        elif direction == "BEARISH" and live_pcr > pcr_bearish_limit: direction = None

                    # --- V8 UPGRADE: HARD RISK FILTERS ---
                    if direction:
                        # Filter 1: Option Chain Failsafe & stabilization Check
                        current_time_ist = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
                        is_market_open_stabilization = (current_time_ist.time() >= datetime.time(9, 15) and current_time_ist.time() <= datetime.time(9, 30))
                        
                        if chain_failed:
                            logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: Option chain is empty/failed to fetch.")
                            direction = None
                        elif is_market_open_stabilization and live_pcr == 1.0:
                            logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: PCR is uninitialized (1.0) during open stabilization.")
                            direction = None

                    if direction:
                        # Filter 2: PCR extreme range skew check
                        pcr_min_limit = float(ticker_config.get("pcr_min_limit", 0.70))
                        pcr_max_limit = float(ticker_config.get("pcr_max_limit", 1.80))
                        if live_pcr < pcr_min_limit or live_pcr > pcr_max_limit:
                            logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: Live PCR {live_pcr:.4f} is outside range [{pcr_min_limit}, {pcr_max_limit}]")
                            direction = None

                    if direction:
                        # Filter 3: PCR Stability and GEX Volatility Check (over lookback windows)
                        pcr_stability_lookback = int(ticker_config.get("pcr_stability_lookback", 5))
                        gex_stability_lookback = int(ticker_config.get("gex_stability_lookback", 3))
                        lookback_limit = max(pcr_stability_lookback, gex_stability_lookback)
                        try:
                            from backend.app.db.models import MarketIndicator
                            indicator_result = await session.execute(
                                select(MarketIndicator)
                                .where(MarketIndicator.ticker == clean_ticker)
                                .order_by(MarketIndicator.timestamp.desc())
                                .limit(lookback_limit)
                            )
                            indicators = indicator_result.scalars().all()
                            
                            if not indicators:
                                logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: No market indicators found in database.")
                                direction = None
                            else:
                                # A. PCR Stability check
                                if pcr_stability_lookback > 1 and len(indicators) >= pcr_stability_lookback:
                                    pcr_vals = [float(ind.pcr) for ind in indicators[:pcr_stability_lookback] if ind.pcr is not None]
                                    if len(pcr_vals) == pcr_stability_lookback:
                                        pcr_spread = max(pcr_vals) - min(pcr_vals)
                                        pcr_stability_limit = float(ticker_config.get("pcr_stability_limit", 0.1))
                                        if pcr_spread > pcr_stability_limit:
                                            logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: PCR is unstable (spread {pcr_spread:.4f} > limit {pcr_stability_limit}) over last {pcr_stability_lookback} intervals.")
                                            direction = None
                                
                                # B. GEX Volatility check
                                if direction and gex_stability_lookback >= 1 and len(indicators) >= gex_stability_lookback:
                                    gex_min_threshold = float(ticker_config.get("gex_min_threshold", -50000000.0))
                                    gex_vals = [float(ind.total_gex) for ind in indicators[:gex_stability_lookback] if ind.total_gex is not None]
                                    if len(gex_vals) == gex_stability_lookback:
                                        volatile_intervals = [gv for gv in gex_vals if gv < gex_min_threshold]
                                        if volatile_intervals:
                                            logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: GEX {volatile_intervals} below safety threshold {gex_min_threshold} during the last {gex_stability_lookback} intervals (High Volatility detected).")
                                            direction = None
                        except Exception as e:
                            logger.error(f"Error querying market indicators for GEX/PCR stability: {e}")

                    if direction:
                        # Filter 5: Trend Confirmation check (consecutive candles in the same direction)
                        trend_confirm_count = int(ticker_config.get("trend_confirm_count", 0))
                        if trend_confirm_count > 0 and len(df) >= trend_confirm_count:
                            is_trend_confirmed = True
                            for i in range(1, trend_confirm_count + 1):
                                idx = -i
                                open_p = float(df['Open'].iloc[idx])
                                close_p = float(df['Close'].iloc[idx])
                                if direction == "BULLISH" and close_p <= open_p:
                                    is_trend_confirmed = False
                                    break
                                elif direction == "BEARISH" and close_p >= open_p:
                                    is_trend_confirmed = False
                                    break
                            
                            if not is_trend_confirmed:
                                logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: Trend confirmation failed. Last {trend_confirm_count} candles do not match {direction} bias.")
                                direction = None

                    if direction:
                        # Filter 4: Time-Based Cooldown Check (maximum 1 trade per hour per asset)
                        try:
                            from backend.app.db.models import Trade
                            from sqlalchemy import desc
                            cooldown_hours = float(ticker_config.get("cooldown_period_hours", 1.0))
                            cooldown_period = datetime.timedelta(hours=cooldown_hours)
                            
                            trade_query = select(Trade).where(Trade.ticker == clean_ticker).order_by(desc(Trade.entry_date)).limit(1)
                            trade_res = await session.execute(trade_query)
                            last_trade = trade_res.scalar_one_or_none()
                            
                            if last_trade and last_trade.entry_date:
                                last_entry = last_trade.entry_date
                                if last_entry.tzinfo is not None:
                                    last_entry = last_entry.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).replace(tzinfo=None)
                                
                                time_elapsed = datetime.datetime.now() - last_entry
                                if time_elapsed < cooldown_period:
                                    logger.warning(f"❌ [Risk Filter] SIGNAL BLOCKED on {clean_ticker}: Cooldown check failed. Last trade at {last_trade.entry_date} (Elapsed: {time_elapsed})")
                                    direction = None
                        except Exception as te:
                            logger.error(f"Error querying recent trades for cooldown: {te}")

                    # 10. Neural Confidence (Direction-Aware)
                    ml_score = 0.5
                    if direction:
                        if is_index:
                            raw_prob = await self.get_ml_engine(dhan_symbol).get_approval_score_async(session)
                            ml_score = raw_prob if direction == "BULLISH" else 1.0 - raw_prob
                            ml_cutoff = 0.65
                            
                            if ml_score < ml_cutoff:
                                logger.info(f"   [ML Guard] {clean_ticker} | Bias: {direction} | Score: {ml_score:.4f} < Cutoff {ml_cutoff} | BLOCKED")
                                direction = None
                            else:
                                logger.info(f"   [ML Guard] {clean_ticker} | Bias: {direction} | Score: {ml_score:.4f} >= Cutoff {ml_cutoff} | APPROVED")
                        else:
                            ml_score = 0.5

                    
                    if direction:
                        win_prob = ml_score 
                        loss_prob = 1.0 - win_prob
                        kelly_fraction = max(0.0, float(win_prob - loss_prob)) 
                        base_lots = 5 if is_index and ml_score > 0.90 else 3 if is_index and ml_score > 0.80 else 2 if is_index and ml_score > 0.70 else 1
                        
                        # Apply max lots limit from configuration
                        max_lots_allowed = int(ticker_config.get("max_lots", 5))
                        base_lots = min(base_lots, max_lots_allowed)

                        if has_active_position:
                            logger.info(f"🛡️ [Active Position Guard] Blocked sending signal for {clean_ticker} to risk committee (Active position exists).")
                        else:
                            passed_assets.append({
                                "ticker": clean_ticker,
                                "spot_price": round(current_price, 2),
                                "bias": direction,
                                "pa_status": pa_status,
                                "vol_surge": round(vol_surge, 1),
                                "coi_pcr": live_pcr,
                                "ml_score": round(ml_score, 2),
                                "recommended_lots": base_lots,
                                "kelly_conviction": round(kelly_fraction, 2)
                            })

                # --- V8 UPGRADE: PUBLISH STRUCTURAL LEVELS FOR DASHBOARD TRANSPARENCY ---
                # Normalize ticker for Frontend Dashboard
                display_ticker = clean_ticker
                if clean_ticker == "NSEI": display_ticker = "NIFTY"
                elif clean_ticker == "NSEBANK": display_ticker = "BANKNIFTY"
                elif clean_ticker == "BSESN": display_ticker = "SENSEX"

                levels_payload = {
                    "ticker": display_ticker,
                    "price": round(current_price, 2),
                    "vwap": round(current_vwap, 2),
                    "poc": mem.get('session_poc', mem.get('poc')) if mem else None,
                    "htf_poc": mem.get('poc') if mem else None,
                    "pdh": mem.get('pdh') if mem else None,
                    "pdl": mem.get('pdl') if mem else None,
                    "weekly_h": mem.get('weekly_h') if mem else None,
                    "weekly_l": mem.get('weekly_l') if mem else None,
                    "order_blocks": mem.get('zones') if mem else [],
                    "pa_status": pa_status,
                    "momentum": momentum_expansion,
                    "obi": obi_score,
                    "timestamp": datetime.datetime.now().isoformat()
                }
                await redis_service.set_json(f"structural_levels:{display_ticker}", levels_payload)
                await redis_service.publish("market_updates", {"type": "STRUCTURAL_LEVELS", "data": levels_payload})
                logger.info(f"DEBUG: Published Structural Intelligence for {display_ticker} | Price: {current_price} | VWAP: {current_vwap} | POC: {levels_payload['poc']}")

                    
            except Exception as e:
                logger.error(f"QuantEngine scanning error on {ticker}: {e}", exc_info=True)
                continue
                
        return passed_assets

    async def get_order_book_imbalance(self, clean_ticker: str) -> float:
        """Calculates Order Book Imbalance (OBI) using Level 2 depth."""
        try:
            segment = None
            sec_id = None
            
            ticker_name = clean_ticker
            if clean_ticker == "NSEI": ticker_name = "NIFTY"
            elif clean_ticker == "NSEBANK": ticker_name = "BANKNIFTY"
            elif clean_ticker == "BSESN": ticker_name = "SENSEX"
            
            is_index = ticker_name in ["NIFTY", "BANKNIFTY", "SENSEX"]
            
            if is_index:
                # Indices do not have a spot order book.
                # Use current-month Futures contract as a proxy for L2 depth!
                sec_id = self.broker.get_futures_security_id(ticker_name)
                segment = "NSE_FNO"
            else:
                sec_id = self.broker.get_equity_security_id(ticker_name)
                segment = "NSE_EQ"
                
            if not sec_id or not segment:
                logger.warning(f"[OBI] Could not resolve security ID or segment for {clean_ticker}")
                return 0.0
                
            # Request raw quote from Dhan (using the correct quote_data method)
            res = await asyncio.to_thread(
                self.broker.dhan.quote_data,
                {segment: [int(sec_id)]}
            )
            
            if res and (res.get('status') == 'success' or res.get('remarks') == 'Success'):
                data = res.get('data', {})
                segment_data = data.get('data', {}).get(segment, {})
                ticker_data = segment_data.get(str(sec_id), {})
                
                if ticker_data:
                    total_buy = float(ticker_data.get('buy_quantity', 0) or 0)
                    total_sell = float(ticker_data.get('sell_quantity', 0) or 0)
                    
                    # Fallback to sum of top 5 levels
                    depth_data = ticker_data.get('depth', {})
                    if total_buy == 0 and total_sell == 0 and isinstance(depth_data, dict):
                        buy_list = depth_data.get('buy', []) or []
                        sell_list = depth_data.get('sell', []) or []
                        total_buy = sum(float(b.get('quantity', 0)) for b in buy_list)
                        total_sell = sum(float(s.get('quantity', 0)) for s in sell_list)
                        
                    if total_buy + total_sell > 0:
                        imbalance = (total_buy - total_sell) / (total_buy + total_sell)
                        obi_val = round(imbalance, 4)
                        logger.info(f"📊 [OBI] Calculated for {ticker_name} (using Futures ID {sec_id}): {obi_val} | Buy: {total_buy} | Sell: {total_sell}")
                        return obi_val
            return 0.0
        except Exception as e:
            logger.error(f"[OBI] Error calculating order book imbalance: {e}")
            return 0.0
