# MIGRATION PLAN: TRADER SINGH v7.0 (MICROSERVICES)

## 1. Architectural Overview
Transitioning from a monolithic Streamlit application to a decoupled Microservices architecture to achieve institutional-grade performance, low latency, and vertical scalability.

### Core Stack
*   **Backend**: FastAPI (Python 3.12+)
*   **Frontend**: React.js 19 + Vite + Shadcn/UI + Tailwind CSS
*   **State Management**: TanStack Query (React Query) + Zustand
*   **Real-time**: WebSockets (FastAPI-native)
*   **Database**: PostgreSQL 16 + TimescaleDB (Existing)
*   **Caching/Tick Buffer**: Redis 7.2 (WSL2)
*   **Worker/Logic**: Async Background Tasks (Python)

---

## 2. Phase 1: Foundation (The Bridge)
Goal: Decouple the logic from the UI and create the first set of APIs.

### A. FastAPI Backend Structure
```text
/backend
├── app/
│   ├── api/          # REST Endpoints (v1)
│   ├── core/         # Shared Logic (QuantEngine, RiskShield)
│   ├── db/           # Database models & sessions
│   ├── schemas/      # Pydantic models
│   ├── services/     # Business logic (Broker, DataHarvester)
│   └── main.py       # Entry point
├── .env
└── requirements.txt
```

### B. WebSocket Strategy
*   **Market Feed**: Stream live ticks from Dhan directly to the frontend via FastAPI.
*   **Signal Feed**: Push real-time trade signals and Signal Audit logs.
*   **Heartbeat**: Monitor system health (Dead Man's Switch integration).

---

## 3. Phase 2: React Frontend (Trader Singh Terminal v7.0)
Goal: Implement the "Bloomberg-style" vertical dashboard.

### A. UI Components (Shadcn/UI)
*   **AppSidebar**: Left-hand navigation (replacing the Streamlit sidebar).
*   **AssetRadar**: GEX/VOC heatmap and multi-timeframe radar.
*   **TradeLedger**: Real-time virtualized table for high-volume trade history.
*   **CommandCenter**: Floating action buttons for "Panic Square-off" and "Force Re-calibrate".

### B. Visual Aesthetics
*   **Theme**: Cyberpunk Dark (Background: `#050505`, Accent: `#00f3ff`).
*   **Charts**: Use `lightweight-charts` (TradingView) for professional price action visualization.

---

## 4. Phase 3: High-Performance Hardening
*   **Redis Buffering**: Ingest ticks into Redis and only push to DB on candle close.
*   **Worker Decoupling**: Move `autopilot` logic to a separate service that communicates via Redis Pub/Sub.
*   **Containerization**: Dockerize all services for cloud-ready deployment (while maintaining WSL2 compatibility).

---

## 5. Completed v7.0 Milestones
- [x] **AI Forensic Integration**: Decoupled Llama 3.3 analyst from legacy Peewee and integrated into FastAPI as an async service.
- [x] **Developer Mode**: Implemented a global toggle to bypass market hour restrictions for 24/7 testing.
- [x] **Trader Singh Dashboard v7.1**: Integrated AI Audit tab and Dev Mode controls into the React UI.
- [x] **Microservices Orchestration**: Verified `start_v7.bat` as the primary entry point.

## 6. Remaining Steps
1.  **WebSocket Live Ticks**: Implement the Dhan WebSocket consumer to push live prices to the Frontend via FastAPI.
2.  **Interactive Charts**: Integrate `lightweight-charts` into the Ops Center for real-time price action.
3.  **Legacy Purge**: Gradually remove root-level `.py` files once all logic is verified in the backend microservice.
