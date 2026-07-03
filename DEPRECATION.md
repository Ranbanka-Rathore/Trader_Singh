# Deprecation Map: Trader Singh v7.0 Transition

The following files and components are scheduled for deprecation as we move to the Microservices architecture (v7.0).
 These files should no longer be used for primary trading operations.

## 1. Legacy User Interfaces
- **`master_terminal.py`**: [DEPRECATED] This Streamlit-based "Glass Cockpit" is replaced by the React-based Frontend in `/frontend`.
- **`ai_market_analyst.py` (Peewee version)**: [REPLACED] The logic has been ported to a v7-compatible async class within the same file (or will be moved to `backend/app/services/ai_analyst.py`).

## 2. Legacy Logic & Scripts
- **`database_manager.py`**: [DEPRECATED] Replaced by SQLAlchemy/SQLModel implementation in `backend/app/db/`.
- **`quant_engine.py` (Root version)**: [DEPRECATED] Use `backend/app/core/quant_engine.py`.
- **`risk_committee.py`**: [DEPRECATED] Reasoning logic is being integrated into the Backend service layers.
- **`oi_autotrender_v2.py`**: [DEPRECATED] Logic moved to `backend/app/services/data_service.py`.

## 3. Legacy Startup Scripts
- **`start_institutional.bat`**: [DEPRECATED] Use `start_v7.bat` to launch the full Microservices stack.
- **`backtester.py`**: [TO BE REPLACED] v7 will feature an integrated backtester service.

## 4. Immediate Cleanup Actions
- [ ] Move `ai_market_analyst.py` into `backend/app/services/`.
- [ ] Archive root-level `.py` files that have been successfully ported to `backend/app/`.
- [ ] Delete or archive legacy JSON database files (`paper_trades_db.json`, `oi_memory_bank.json`) once data is migrated to PostgreSQL.
