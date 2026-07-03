# Trader Singh v8.0: Institutional Trading Manual

## 1. System Overview
Trader Singh v8.0 is a distributed, multi-agent algorithmic trading system designed for Indian Markets (NSE). It combines classical quantitative analysis with modern Neural Pre-Filtering (XGBoost) and Large Language Model (LLM) reasoning to achieve institutional-grade alpha.

---

## 2. Architecture: Distributed Microservices
The system has moved away from a monolithic structure to a native microservice architecture. Each component runs as an independent process for maximum resilience.

### Backend Infrastructure
- **API Gateway (`run_api.py`)**: FastAPI-based hub. Handles WebSocket tick streaming to the frontend and provides REST endpoints for system control.
- **System Control (`run_system_control.py`)**: The "Brain" that manages global state (ACTIVE/PAUSED) and enforces Indian Standard Time (IST) market hours (09:15 - 15:30).
- **Data Harvester (`run_harvester.py`)**: Connects to Dhan WebSockets for live ticks. Calculates real-time GEX (Gamma Exposure) and **Strike-Sensitive PCR**.
- **Quant Engine (`run_quant.py`)**: Scans the universe for Technical Patterns. Uses **Windows Selector Loop** enforcement for robust database connectivity.
- **Risk Committee (`run_risk_committee.py`)**: An LLM-powered 3-agent committee that audits trade setups against live Macro Sentiment (Llama 3.3).
- **Execution OMS (`run_oms.py`)**: Manages order lifecycles using **RL-based Limit Hunting**. Instead of hitting market prices, it sits on the bid/ask and dynamically adjusts to minimize slippage.

### Data Layer
- **Redis**: Low-latency message broker for inter-service communication (Pub/Sub) and live tick buffering.
- **PostgreSQL (TimescaleDB)**: Reliable storage for historical candles, trade ledgers, and signal audits.

---

## 3. The Trade Lifecycle (How it Functions)
### Institutional PCR Logic
Unlike retail systems, Trader Singh v8.0 uses **Real-time Strike-Sensitive PCR**:
1.  **Dynamic ATM Detection**: Automatically identifies the At-The-Money strike every 60 seconds.
2.  **Smart Money Indexing**: Filters the option chain to only the **+/- 10 strikes** around the ATM.
3.  **High-Frequency Calculation**: Updates `Total Put Change-in-OI / Total Call Change-in-OI` within this active range to capture institutional positioning.

### Execution Flow
1.  **Scanning**: The Quant Engine identifies a technical breakout (e.g., NIFTY crosses VWAP with high volume).
2.  **Neural Filter**: The XGBoost model checks the breakout against historical win probabilities. If confidence < 70%, the trade is discarded.
3.  **Macro Overlay**: The Sentiment Service fetches live news. If the market is bullish but news is bearish, the trade is vetoed.
4.  **AI Committee Audit**: The LLM agents debate the trade's risk/reward.
5.  **Execution**: If approved, the RL-OMS executes a "Limit Hunting" order to enter the position with minimal impact.
6.  **Management**: The OMS monitors the trade 24/7, adjusting trailing stops based on real-time volatility.

---

## 4. Frontend: The Glass Cockpit
Access at: `http://localhost:5173`

- **SMC Radar**: Real-time charts and technical signals.
- **War Room**: View active positions, live PnL, and Greeks (Delta/Theta).
- **Intelligence**: View the "Reasoning" behind why the AI approved or rejected a trade.
- **Autopilot Core**: Single toggle to enable/disable live trading.
- **Off-Market Mode**: Allows testing and development when the actual market is closed.

---

## 6. Technical Stability & Performance
- **Async Resilience**: All Python microservices explicitly enforce `asyncio.WindowsSelectorEventLoopPolicy` to ensure compatibility with `psycopg3` and PostgreSQL on Windows.
- **Automated Health Checks**: `start_v8.bat` executes an automated diagnostics script (`check_health.py`) on every launch to verify Redis connectivity and service heartbeats.
- **State Recovery**: Use `reset_db.py` to perform a surgical purge of stale signals/audits while preserving historical price data.

---
*Manual Version: 8.0.5*
*Last Updated: May 27, 2026*
