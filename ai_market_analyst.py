import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import json
import datetime
from decimal import Decimal
from sqlalchemy import select, create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import Trade, Candle
from backend.app.db.database import get_session, engine as db_engine
from sqlalchemy.orm import sessionmaker
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
import asyncio
import warnings

if os.name == 'nt':
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class AIMarketAnalyst:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            self.model_client = None
            self.agent = None
            return

        self.model_client = OpenAIChatCompletionClient(
            model="llama-3.3-70b-versatile",
            api_key=self.api_key,
            base_url="https://api.groq.com/openai/v1",
            model_info={
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "family": "unknown",
            }
        )
        self.agent = AssistantAgent(
            name="SessionAttributionReviewer",
            model_client=self.model_client,
            system_message="""You are a Lead Quant Strategist at a premier digital options trading fund.
            Your task is to write a high-fidelity Post-Market Strategy Attribution & Session Review.
            You will review the performance of today's actual live execution versus all 7 potential options playbooks.
            Critique the daily strategy selector's pre-market choice and mid-day pivot, referencing actual market dynamics.
            Ensure the report reads with professional executive style—insightful, rigorous, and highly analytical."""
        )

    def load_clean_data(self):
        # Read database candles using pandas
        db_user = os.getenv("DB_USER", "trader")
        db_password = os.getenv("DB_PASSWORD", "")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "agentic_trader")
        sync_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        engine = create_engine(sync_url)
        query = """
            SELECT timestamp, open, high, low, close, volume FROM candles
            WHERE ticker = 'NIFTY' AND timeframe = '5m'
            ORDER BY timestamp ASC
        """
        df = pd.read_sql(query, engine)
        if df.empty:
            return df
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.drop_duplicates(subset=['timestamp'])
        df['IST_Time'] = df['timestamp'].dt.tz_convert('Asia/Kolkata') if df['timestamp'].dt.tz is not None else df['timestamp']
        df['Date'] = df['IST_Time'].dt.date
        
        # Filter for today's candles
        today = datetime.date(2026, 7, 3)  # Pin to July 3 session
        df_filtered = df[df['Date'] == today].copy()
        
        for col in ['open', 'high', 'low', 'close']:
            df_filtered[col] = df_filtered[col].astype(float)
            
        # Calculate technical indicators
        df_filtered['Typical_Price'] = (df_filtered['high'] + df_filtered['low'] + df_filtered['close']) / 3
        df_filtered['TP_Vol'] = df_filtered['Typical_Price'] * df_filtered['volume']
        df_filtered['Cum_TP_Vol'] = df_filtered['TP_Vol'].cumsum()
        df_filtered['Cum_Vol'] = df_filtered['volume'].cumsum()
        df_filtered['VWAP'] = df_filtered['Cum_TP_Vol'] / df_filtered['Cum_Vol']
        df_filtered['StdDev'] = df_filtered['close'].expanding().std().fillna(0)
        df_filtered['Upper_Band'] = df_filtered['VWAP'] + (df_filtered['StdDev'] * 1.5)
        df_filtered['Lower_Band'] = df_filtered['VWAP'] - (df_filtered['StdDev'] * 1.5)
        df_filtered['session_high'] = df_filtered['high'].cummax()
        df_filtered['session_low'] = df_filtered['low'].cummin()
        
        return df_filtered

    def run_attribution_simulations(self, df):
        if df.empty:
            return {}

        strategies = [
            "CALENDAR_SPREAD",
            "CREDIT_SPREAD",
            "DEBIT_SPREAD",
            "DIAGONAL_SPREAD",
            "DELTA_NEUTRAL",
            "COVERED_CALL",
            "CASH_SECURED_PUT"
        ]
        
        results = {}
        lot_size = 65
        
        for strategy in strategies:
            in_trade = False
            entry_price = 0.0
            entry_time = None
            trade_bias = None
            premium_paid = 0.0
            net_credit = 0.0
            trades_logged = []
            
            for idx in range(1, len(df)):
                row = df.iloc[idx]
                prev_row = df.iloc[idx-1]
                current_time = row['IST_Time']
                current_price = row['close']
                is_eod = (current_time.hour == 15 and current_time.minute >= 15) or (current_time.hour > 15)
                
                if in_trade:
                    hours_elapsed = (current_time - entry_time).total_seconds() / 3600.0
                    price_change = current_price - entry_price
                    exit_triggered = False
                    exit_reason = ""
                    pnl_rupees = 0.0
                    
                    if strategy == "CALENDAR_SPREAD":
                        delta = 0.05
                        appreciation = premium_paid * 0.08 * hours_elapsed
                        loss_from_delta = abs(price_change) * delta * lot_size
                        pnl_rupees = (appreciation - loss_from_delta)
                        is_tp = pnl_rupees >= premium_paid * 0.35 * lot_size
                        is_sl = pnl_rupees <= -premium_paid * 0.25 * lot_size
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+35%)"
                            pnl_rupees = premium_paid * 0.35 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-25%)"
                            pnl_rupees = -premium_paid * 0.25 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "CREDIT_SPREAD":
                        delta = 0.15
                        current_spread = net_credit - (price_change * delta) if trade_bias == "BULLISH" else net_credit + (price_change * delta)
                        pnl_rupees = (net_credit - current_spread) * lot_size
                        is_tp = current_spread <= net_credit * 0.20
                        is_sl = current_spread >= net_credit * 1.80
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+80%)"
                            pnl_rupees = net_credit * 0.80 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-80%)"
                            pnl_rupees = -net_credit * 0.80 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "DEBIT_SPREAD":
                        delta = 0.30
                        current_spread = premium_paid + (price_change * delta) if trade_bias == "BULLISH" else premium_paid - (price_change * delta)
                        pnl_rupees = (current_spread - premium_paid) * lot_size
                        is_tp = current_spread >= premium_paid * 1.50
                        is_sl = current_spread <= premium_paid * 0.60
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+50%)"
                            pnl_rupees = premium_paid * 0.50 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-40%)"
                            pnl_rupees = -premium_paid * 0.40 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "DIAGONAL_SPREAD":
                        delta = 0.20
                        appreciation = premium_paid * 0.05 * hours_elapsed
                        loss_from_delta = abs(price_change) * delta * lot_size
                        pnl_rupees = (appreciation - loss_from_delta)
                        is_tp = pnl_rupees >= premium_paid * 0.40 * lot_size
                        is_sl = pnl_rupees <= -premium_paid * 0.25 * lot_size
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+40%)"
                            pnl_rupees = premium_paid * 0.40 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-25%)"
                            pnl_rupees = -premium_paid * 0.25 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "DELTA_NEUTRAL":
                        delta = 0.02
                        appreciation = net_credit * 0.04 * hours_elapsed
                        loss_from_delta = abs(price_change) * delta * lot_size
                        pnl_rupees = (appreciation - loss_from_delta)
                        is_tp = pnl_rupees >= net_credit * 0.50 * lot_size
                        is_sl = pnl_rupees <= -net_credit * 0.80 * lot_size
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+50%)"
                            pnl_rupees = net_credit * 0.50 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-80%)"
                            pnl_rupees = -net_credit * 0.80 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "COVERED_CALL":
                        delta = 0.30
                        decay = net_credit * 0.05 * hours_elapsed
                        current_call = net_credit + (price_change * delta) - decay
                        pnl_rupees = (price_change - (current_call - net_credit)) * lot_size
                        max_profit = 100.0 + net_credit
                        is_tp = pnl_rupees >= max_profit * 0.95 * lot_size
                        is_sl = pnl_rupees <= -200.0 * lot_size
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (Max)"
                            pnl_rupees = max_profit * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (200pt Spot)"
                            pnl_rupees = -200.0 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    elif strategy == "CASH_SECURED_PUT":
                        delta = 0.30
                        decay = net_credit * 0.05 * hours_elapsed
                        current_put = net_credit - (price_change * delta) - decay
                        pnl_rupees = (net_credit - current_put) * lot_size
                        is_tp = current_put <= net_credit * 0.20
                        is_sl = current_put >= net_credit * 2.0
                        if is_tp:
                            exit_triggered = True
                            exit_reason = "🎯 TAKE PROFIT (+80%)"
                            pnl_rupees = net_credit * 0.80 * lot_size
                        elif is_sl:
                            exit_triggered = True
                            exit_reason = "🛑 STOP LOSS (-100%)"
                            pnl_rupees = -net_credit * 1.0 * lot_size
                        elif is_eod:
                            exit_triggered = True
                            exit_reason = "⏰ EOD TIME STOP"

                    if exit_triggered:
                        charges = 120.0 if strategy in ["CREDIT_SPREAD", "DEBIT_SPREAD", "DIAGONAL_SPREAD", "CALENDAR_SPREAD", "DELTA_NEUTRAL"] else 60.0
                        trades_logged.append({
                            "bias": trade_bias,
                            "entry_time": entry_time.strftime("%H:%M:%S"),
                            "exit_time": current_time.strftime("%H:%M:%S"),
                            "entry_price": entry_price,
                            "exit_price": current_price,
                            "exit_reason": exit_reason,
                            "gross_pnl": pnl_rupees,
                            "charges": charges,
                            "net_pnl": pnl_rupees - charges
                        })
                        in_trade = False
                    continue

                if is_eod:
                    continue

                # Signal scan
                pa_status = "CHOP_ZONE"
                is_bullish_sweep = (row['session_low'] * 0.9985 <= row['low'] < row['session_low']) and current_price > row['session_low']
                is_bearish_sweep = (row['session_high'] < row['high'] <= row['session_high'] * 1.0015) and current_price < row['session_high']
                
                if is_bullish_sweep:
                    pa_status = "LIQUIDITY_SWEEP_LONG"
                elif is_bearish_sweep:
                    pa_status = "LIQUIDITY_SWEEP_SHORT"
                elif current_price >= row['Upper_Band'] and prev_row['close'] < prev_row['Upper_Band']:
                    pa_status = "VWAP_DEVIATION_SHORT"
                elif current_price <= row['Lower_Band'] and prev_row['close'] > prev_row['Lower_Band']:
                    pa_status = "VWAP_DEVIATION_LONG"
                elif current_price <= row['session_low']:
                    pa_status = "SESSION_LOW_BREAKDOWN"
                elif current_price >= row['session_high']:
                    pa_status = "SESSION_HIGH_BREAKOUT"
                elif current_price > row['VWAP'] and prev_row['close'] <= prev_row['VWAP']:
                    pa_status = "VWAP_BULL_CROSS"
                elif current_price < row['VWAP'] and prev_row['close'] >= prev_row['VWAP']:
                    pa_status = "VWAP_BEAR_CROSS"
                
                direction = None
                bullish_triggers = ["VWAP_BULL_CROSS", "LIQUIDITY_SWEEP_LONG", "VWAP_DEVIATION_LONG", "SESSION_HIGH_BREAKOUT"]
                bearish_triggers = ["VWAP_BEAR_CROSS", "LIQUIDITY_SWEEP_SHORT", "VWAP_DEVIATION_SHORT", "SESSION_LOW_BREAKDOWN"]
                
                if pa_status in bullish_triggers: direction = "BULLISH"
                elif pa_status in bearish_triggers: direction = "BEARISH"
                
                if strategy == "DELTA_NEUTRAL":
                    if current_time.hour == 9 and current_time.minute == 15:
                        in_trade = True
                        entry_price = current_price
                        entry_time = current_time
                        trade_bias = "NEUTRAL"
                        net_credit = 150.0
                else:
                    if direction:
                        if current_time.hour == 9 and current_time.minute < 30:
                            continue
                        if strategy in ["COVERED_CALL", "CASH_SECURED_PUT"] and direction != "BULLISH":
                            continue
                            
                        in_trade = True
                        entry_price = current_price
                        entry_time = current_time
                        trade_bias = direction
                        
                        if strategy == "CALENDAR_SPREAD":
                            premium_paid = 200.0
                        elif strategy == "CREDIT_SPREAD":
                            net_credit = 60.0
                        elif strategy == "DEBIT_SPREAD":
                            premium_paid = 100.0
                        elif strategy == "DIAGONAL_SPREAD":
                            premium_paid = 120.0
                        elif strategy in ["COVERED_CALL", "CASH_SECURED_PUT"]:
                            net_credit = 291.47

            results[strategy] = trades_logged
            
        return results

    async def generate_learning_report(self):
        print("🕵️ Starting Session Alpha & Strategy Attribution Audit...")
        
        # 1. Fetch NIFTY candles and run backtests for all 7 strategies
        df = self.load_clean_data()
        sim_results = self.run_attribution_simulations(df)
        
        # 2. Get today's actual live trades from database
        actual_trades_data = []
        try:
            db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://trader@127.0.0.1:5432/agentic_trader")
            engine_async = sessionmaker(
                db_engine(), class_=AsyncSession, expire_on_commit=False
            )
            async with engine_async() as session:
                today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                stmt = select(Trade).where(Trade.entry_date >= today).order_by(Trade.entry_date.asc())
                res = await session.execute(stmt)
                trades = res.scalars().all()
                for t in trades:
                    actual_trades_data.append({
                        "id": t.id,
                        "strategy": t.strategy_type,
                        "bias": t.bias,
                        "entry_time": t.entry_date.strftime("%H:%M:%S") if t.entry_date else "N/A",
                        "exit_time": t.exit_date.strftime("%H:%M:%S") if t.exit_date else "N/A",
                        "entry_spot": float(t.entry_spot_price or 0.0),
                        "exit_spot": float(t.exit_price or 0.0),
                        "exit_reason": t.exit_reason or "N/A",
                        "net_pnl": float(t.realized_pnl or 0.0)
                    })
        except Exception as ex:
            print(f"Failed to fetch actual trades: {ex}")

        # 3. Construct Strategy Comparison Table
        capital_mapping = {
            "CASH_SECURED_PUT": 140000,
            "COVERED_CALL": 160000,
            "DELTA_NEUTRAL": 120000,
            "CALENDAR_SPREAD": 35000,
            "CREDIT_SPREAD": 35000,
            "DEBIT_SPREAD": 15000,
            "DIAGONAL_SPREAD": 35000
        }
        
        matrix_rows = []
        for strat, trades in sim_results.items():
            trade_count = len(trades)
            net_pnl = sum([t['net_pnl'] for t in trades])
            cap = capital_mapping.get(strat, 100000)
            roi = (net_pnl / cap) * 100.0 if cap > 0 else 0.0
            win_rate = (sum([1 for t in trades if t['net_pnl'] > 0]) / trade_count * 100.0) if trade_count > 0 else 0.0
            
            matrix_rows.append(
                f"| **{strat.replace('_', ' ')}** | {trade_count} | {win_rate:.1f}% | ₹{cap:,} | **₹{net_pnl:+,.2f}** | **{roi:+.2f}%** |"
            )
        
        matrix_md = "\n".join(matrix_rows)
        
        # 4. Construct Actual Execution Table
        actual_rows = []
        if actual_trades_data:
            for t in actual_trades_data:
                actual_rows.append(
                    f"| Trade #{t['id']} | **{t['strategy']}** | {t['bias']} | {t['entry_time']} | {t['exit_time']} | {t['entry_spot']} | {t['exit_spot']} | {t['exit_reason']} | **₹{t['net_pnl']:+,.2f}** |"
                )
        else:
            actual_rows.append("| - | - | - | - | - | - | - | - | **No active execution recorded today.** |")
            
        actual_md = "\n".join(actual_rows)

        # 5. Formulate final report
        base_md = f"""# Post-Market Strategy Attribution & Session Review

## 🟢 Today's Actual Live Execution
| ID | Strategy | Bias | Entry | Exit | Entry Spot | Exit Spot | Reason | Net PnL |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
{actual_md}

---

## 📊 Session Alpha Comparison Matrix (All 7 Playbooks)
| Strategy | Trades | Win Rate | Margin/Capital | Net PnL | Net ROI |
| :--- | :---: | :---: | :---: | :---: | :---: |
{matrix_md}

"""

        # 6. Ask LLM to add Executive Critique if API key exists
        if not self.model_client:
            base_md += """
---

## 🔬 Quant Strategy Critique
> [!NOTE]
> AI executive critique is currently unavailable. To enable automated critiques, configure a valid `GROQ_API_KEY` in your `.env` file.
"""
            return base_md

        prompt = f"""
        Analyze today's options session data:
        
        Actual Trades:
        {json.dumps(actual_trades_data, indent=2)}
        
        Simulated Performance of all 7 strategies:
        {json.dumps({s: [{"net_pnl": t["net_pnl"], "entry_time": t["entry_time"], "exit_time": t["exit_time"]} for t in trades] for s, trades in sim_results.items()}, indent=2)}
        
        Write a professional 4-paragraph Hedge Fund Session Review:
        1. **Market Structure Critique**: Analyze the NIFTY session profile (e.g., did the gap-up fade, and what was the intraday volume distribution?).
        2. **Attribution Analysis**: Contrast the selected/executed strategy with the other 6. Explain why the highest ROI strategy outperformed (referencing option Greeks, delta, and theta).
        3. **Selector and Pivot Assessment**: Critique the pre-market regime classification and mid-market pivot performance. Did the timing logic act correctly?
        4. **Tactical Directives**: Provide 3 concrete parameters or rules to improve execution edge in the next session.
        
        Format the response in pure markdown starting directly with '## 🔬 Quant Strategy Critique'.
        """
        try:
            response = await self.agent.on_messages([TextMessage(content=prompt, source="user")], cancellation_token=None)
            base_md += "\n" + response.chat_message.content
        except Exception as e:
            base_md += f"\n\n## 🔬 Quant Strategy Critique\nFailed to generate AI critique: {e}"
            
        return base_md

if __name__ == "__main__":
    analyst = AIMarketAnalyst()
    async def test():
        print(await analyst.generate_learning_report())
    asyncio.run(test())
