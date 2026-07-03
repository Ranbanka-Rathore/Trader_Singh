# GEMINI.md - Trader Singh Project Intelligence

This document outlines the architecture, strategic standing, and future roadmap for the Trader Singh project. It serves as the foundational guide for system evolution and engineering standards.

## 1. Project Overview & Architecture
Trader Singh is an autonomous trading system for Indian markets (NSE/BSE), combining Classical Quantitative Analysis with Generative AI (Multi-Agent LLM reasoning).

### Core Components
*   **Command Center (`master_terminal.py`)**: Streamlit-based "Glass Cockpit" for real-time monitoring and SMC-style Multi-Timeframe Radar.
*   **Logic Engine (`quant_engine.py` & `oi_autotrender_v2.py`)**: Scanner using order flow, structure, and institutional delta (OI rate of change).
*   **Strategy Architect (`options_desk.py`)**: Focuses on defined-risk Credit Spreads (Theta-positive strategies).
*   **Governance Layer (`risk_committee.py`)**: AutoGen-powered 3-agent committee (Analyst, Risk Manager, Head Trader) utilizing Groq/Llama 3.1 for trade approval.
*   **Execution & Safety (`paper_broker.py` & `dhan_integration.py`)**: Professional-grade API wrapper with sophisticated trailing stop protocols and EOD square-offs.
*   **Learning Loop (`data_harvester.py`, `ml_optimizer.py`, `ai_market_analyst.py`)**: Continuous refinement through historical harvesting and post-market AI analysis.

---

## 2. Competitive Standing
*   **Retail/Open-Source Peer Group**: **9/10 (Elite)**. Conceptually superior due to AI risk management and theta-positive strategies.
*   **Mid-Tier Proprietary Desks**: **5.5/10**. Strong alpha/logic, but infrastructure (JSON-based, REST polling) requires hardening for institutional grade.

---

## 3. Roadmap to Top-Tier Status

### Phase 1: Institutional Infrastructure (Resilience)
*   **Transition to Microservices**: Decouple Data Ingestion, Quant Logic, Execution, and UI into independent services.
*   **Database Upgrade**: Replace JSON with **TimescaleDB** or **PostgreSQL** for historical data and **Redis** for live state/tick buffering.
*   **Message Orchestration**: Implement **RabbitMQ** or **ZeroMQ** for low-latency inter-service communication.

### Phase 2: Data & Execution Speed (Edge)
*   **WebSocket Integration**: Shift from REST polling to full WebSocket streams for tick-by-tick precision.
*   **Advanced OMS**: Implement **Iceberg/TWAP** algorithms and intelligent Limit Order management to minimize slippage.

### Phase 3: Alpha Evolution (Intelligence)
*   **Asynchronous AI**: Move LLMs to macro-sentiment analysis and post-market tuning.
*   **Real-time Neural Networks**: Utilize **XGBoost** or **LSTMs** for millisecond-level trade approval.
*   **Alternative Data**: Integrate news, regulatory announcements, and social sentiment into the signal matrix.

### Phase 4: Frontend Restoration & Stabilization (COMPLETED)
*   **Vite/React 19 Recovery**: Successfully diagnosed and fixed the "white page" crash by identifying breaking changes in `lightweight-charts` v5.
*   **Unified Series API**: Refactored `RealtimeChart.tsx` to use the new `addSeries(AreaSeries, ...)` pattern required by v5.
*   **Institutional Dashboard**: Restored the full "Trader Singh v7.0" UI with Sidebar, Top Stats, and SMC Radar.
*   **Dev Persistence**: Implemented `simulate_market.py` to pump Redis and PostgreSQL with dummy ticks/signals, enabling 24/7 frontend development and stress testing.
*   **WebSocket Resiliency**: Verified the FastAPI-to-React WebSocket bridge for live tick streaming.

### Phase 6: Unified Experience (Single Click)
*   **Orchestration**: Created `start.bat` to launch the full stack (WSL, Backend, Worker, Frontend) in one click.
*   **Interface Consolidation**: Retired the legacy Streamlit terminal in favor of the React 19 "Neon" Dashboard (`http://localhost:5173`).
*   **Infrastructure Automation**: Added auto-clearing of PostgreSQL lock files in the startup sequence.

### Phase 7: Neural Edge & Distributed Scale (UPCOMING)
*   **Distributed Microservices**: Transition to Docker-orchestrated services (Data, Logic, Risk, Execution).
*   **Neural Pre-Filtering**: Implement XGBoost/LSTM fast-approval layers for the Quant Engine.
*   **RL-based OMS**: Develop a Reinforcement Learning agent for intelligent Limit Order management.
*   **Alternative Data Ingestion**: Integrate real-time news and social sentiment matrices.

---

## 4. Operational Instructions (V8 Architecture)
*   **System Requirement**: The system utilizes a hybrid microservice architecture. Python microservices run natively on Windows, while PostgreSQL and Redis run inside WSL (Ubuntu).
*   **Standard Startup**: Run `start_v8.bat` from the root directory. This will automatically start WSL services and launch the API, Harvester, Logic, Committee, OMS, and Frontend as separate minimized processes.
*   **Dashboard**: Access the real-time UI at `http://localhost:5173`.
*   **System Shutdown**: To cleanly stop all microservices, run `taskkill /F /IM python.exe /T` and `taskkill /F /IM node.exe /T` in your terminal.
*   **Legacy Access**: Old monolithic scripts (`start.bat`) are archived with `.DEPRECATED` extensions.
*   **Contextual Precedence**: This file (`GEMINI.md`) defines the foundational mandates for this project.
*   **Safety First**: Never commit `.env` files or API secrets.
*   **Validation**: Every strategy change must be empirically validated via the `ml_pipeline.py` before live deployment.
