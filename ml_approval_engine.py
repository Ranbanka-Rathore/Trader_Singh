import os
import joblib
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

# --- FAIL-CLOSED DEFAULTS ---
# Previously these paths returned 1.0, which is >= the risk committee's 0.85
# fast-track threshold — so a missing model file or thin data silently
# auto-approved every trade. Fail closed instead:
#   NEUTRAL: no model / not enough data -> send to committee, never bypass it.
#   VETO:    inference error -> below the 0.40 auto-veto line, reject the trade.
FAILSAFE_NEUTRAL_SCORE = 0.5
FAILSAFE_VETO_SCORE = 0.0


class MLApprovalEngine:
    def __init__(self, ticker="NIFTY", timeframe=1):
        self.ticker = ticker
        self.timeframe = timeframe
        self.model_path = f"models/xgboost_{ticker}_{timeframe}m.joblib"
        self.model_data = None
        
        # Load DB credentials
        db_user = os.getenv("DB_USER", "trader")
        db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "agentic_trader")
        
        self.db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        self._load_model()

    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                self.model_data = joblib.load(self.model_path)
                print(f"✅ ML Approval Engine: Loaded model for {self.ticker} ({self.timeframe}m)")
            except Exception as e:
                print(f"⚠️ Failed to load ML model: {e}")
        else:
            print(f"ℹ️ ML Approval Engine: No model found at {self.model_path}. ML validation disabled.")

    async def get_approval_score_async(self, session, as_of_timestamp=None) -> float:
        """
        Asynchronously fetches data and predicts score.
        Accepts an AsyncSession to reuse the existing connection pool.
        Optional as_of_timestamp parameter filters for indicators at or before that time (for backtesting).
        """
        if not self.model_data:
            return FAILSAFE_NEUTRAL_SCORE

        try:
            from sqlalchemy import text
            time_filter = f"AND timestamp <= '{as_of_timestamp.strftime('%Y-%m-%d %H:%M:%S')}'" if as_of_timestamp else ""
            query = text(f"""
                SELECT price, vwap, pcr, total_gex, timestamp FROM market_indicators
                WHERE ticker = '{self.ticker}' AND timeframe = {self.timeframe} {time_filter}
                ORDER BY timestamp DESC LIMIT 250
            """)
            result = await session.execute(query)
            rows = result.fetchall()

            if not rows or len(rows) < 100:
                return FAILSAFE_NEUTRAL_SCORE
            
            # Convert to DataFrame
            df = pd.DataFrame(rows, columns=['price', 'vwap', 'pcr', 'total_gex', 'timestamp'])
            
            # Sort chronologically
            df = df.sort_values(by="timestamp", ascending=True).reset_index(drop=True)
            
            # Ensure proper float types
            df['price'] = df['price'].astype(float)
            df['vwap'] = df['vwap'].astype(float)
            df['pcr'] = df['pcr'].astype(float)
            df['total_gex'] = df['total_gex'].astype(float)

            # Fill missing starting values to prevent NaN propagation
            df['vwap'] = df['vwap'].fillna(df['price'].rolling(window=20, min_periods=1).mean())
            df['pcr'] = df['pcr'].fillna(1.0)
            df['total_gex'] = df['total_gex'].fillna(0.0)

            # --- FEATURE ENGINEERING ---
            df['price_vs_vwap'] = (df['price'] - df['vwap']) / df['vwap']
            df['gex_volatility'] = df['total_gex'].rolling(window=10).std()
            df['pcr_change'] = df['pcr'].diff()
            
            df['returns_1m'] = df['price'].pct_change()
            df['returns_5m'] = df['price'].pct_change(5)
            df['volatility_20'] = df['returns_1m'].rolling(window=20).std()
            df['ma_20'] = df['price'].rolling(window=20).mean()
            df['ma_50'] = df['price'].rolling(window=50).mean()
            df['trend_signal'] = (df['ma_20'] > df['ma_50']).astype(int)
            
            # Additional features from ml_retraining_pipeline
            df['pcr_ma'] = df['pcr'].rolling(window=5).mean()
            df['pcr_velocity'] = df['pcr'].diff()
            
            pcr_std = df['pcr'].rolling(window=20).std()
            pcr_std = np.where(pcr_std == 0, 1.0, pcr_std)
            df['pcr_zscore'] = (df['pcr'] - df['pcr'].rolling(window=20).mean()) / pcr_std
            
            df['volatility'] = df['returns_1m'].rolling(window=10).std()

            # Cleanse inf and nan values to prevent inference crashes
            df = df.replace([np.inf, -np.inf], np.nan)
            df = df.bfill().ffill()
            df = df.fillna(0.0)

            # Get features list
            features = self.model_data['features']
            latest_features = df.iloc[-1][features]
            
            # Predict (keep model inference sync as it is CPU bound and fast)
            raw_prob = self.model_data['model'].predict_proba(pd.DataFrame([latest_features]))[0][1]
            
            # Confidence calibration over last 5 candles
            history_probs = []
            for i in range(-5, 0):
                feat_slice = df.iloc[i][features]
                p = self.model_data['model'].predict_proba(pd.DataFrame([feat_slice]))[0][1]
                history_probs.append(p)
            
            calibrated_prob = sum(history_probs) / len(history_probs)
            return round(float(calibrated_prob), 4)

        except Exception as e:
            print(f"⚠️ ML Async Inference Error: {e}")
            return FAILSAFE_VETO_SCORE

    def get_approval_score_sync(self, conn, as_of_timestamp=None) -> float:
        """
        Synchronously fetches data and predicts score.
        Accepts a synchronous connection/engine to query the DB.
        """
        if not self.model_data:
            return FAILSAFE_NEUTRAL_SCORE

        try:
            time_filter = f"AND timestamp <= '{as_of_timestamp.strftime('%Y-%m-%d %H:%M:%S')}'" if as_of_timestamp else ""
            query = f"""
                SELECT price, vwap, pcr, total_gex, timestamp FROM market_indicators
                WHERE ticker = '{self.ticker}' AND timeframe = {self.timeframe} {time_filter}
                ORDER BY timestamp DESC LIMIT 250
            """
            df = pd.read_sql(query, conn)

            if df.empty or len(df) < 100:
                return FAILSAFE_NEUTRAL_SCORE
            
            # Sort chronologically
            df = df.sort_values(by="timestamp", ascending=True).reset_index(drop=True)
            
            # Ensure proper float types
            df['price'] = df['price'].astype(float)
            df['vwap'] = df['vwap'].astype(float)
            df['pcr'] = df['pcr'].astype(float)
            df['total_gex'] = df['total_gex'].astype(float)

            # Fill missing starting values to prevent NaN propagation
            df['vwap'] = df['vwap'].fillna(df['price'].rolling(window=20, min_periods=1).mean())
            df['pcr'] = df['pcr'].fillna(1.0)
            df['total_gex'] = df['total_gex'].fillna(0.0)

            # --- FEATURE ENGINEERING ---
            df['price_vs_vwap'] = (df['price'] - df['vwap']) / df['vwap']
            df['gex_volatility'] = df['total_gex'].rolling(window=10).std()
            df['pcr_change'] = df['pcr'].diff()
            
            df['returns_1m'] = df['price'].pct_change()
            df['returns_5m'] = df['price'].pct_change(5)
            df['volatility_20'] = df['returns_1m'].rolling(window=20).std()
            df['ma_20'] = df['price'].rolling(window=20).mean()
            df['ma_50'] = df['price'].rolling(window=50).mean()
            df['trend_signal'] = (df['ma_20'] > df['ma_50']).astype(int)
            
            # Additional features from ml_retraining_pipeline
            df['pcr_ma'] = df['pcr'].rolling(window=5).mean()
            df['pcr_velocity'] = df['pcr'].diff()
            
            pcr_std = df['pcr'].rolling(window=20).std()
            pcr_std = np.where(pcr_std == 0, 1.0, pcr_std)
            df['pcr_zscore'] = (df['pcr'] - df['pcr'].rolling(window=20).mean()) / pcr_std
            
            df['volatility'] = df['returns_1m'].rolling(window=10).std()

            # Cleanse inf and nan values
            df = df.replace([np.inf, -np.inf], np.nan)
            df = df.bfill().ffill()
            df = df.fillna(0.0)

            # Get features list
            features = self.model_data['features']
            latest_features = df.iloc[-1][features]
            
            # Predict
            raw_prob = self.model_data['model'].predict_proba(pd.DataFrame([latest_features]))[0][1]
            
            # Confidence calibration over last 5 candles
            history_probs = []
            for i in range(-5, 0):
                feat_slice = df.iloc[i][features]
                p = self.model_data['model'].predict_proba(pd.DataFrame([feat_slice]))[0][1]
                history_probs.append(p)
            
            calibrated_prob = sum(history_probs) / len(history_probs)
            return round(float(calibrated_prob), 4)

        except Exception as e:
            print(f"⚠️ ML Sync Inference Error: {e}")
            return FAILSAFE_VETO_SCORE

    def get_approval_score(self) -> float:
        """DEPRECATED: Use get_approval_score_sync or get_approval_score_async instead."""
        return FAILSAFE_NEUTRAL_SCORE


if __name__ == "__main__":
    from sqlalchemy import create_engine
    # Simple test with local db connection
    db_user = os.getenv("DB_USER", "trader")
    db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "agentic_trader")
    
    # Try dynamic resolution for test if native localhost is blocked
    def _get_wsl_ip():
        import subprocess
        try:
            r = subprocess.run(["wsl", "-d", "Ubuntu", "hostname", "-I"], capture_output=True, text=True, timeout=5)
            return r.stdout.strip().split()[0]
        except Exception:
            return "127.0.0.1"
            
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", int(db_port)), timeout=1)
        s.close()
        host = "127.0.0.1"
    except Exception:
        host = _get_wsl_ip()
        
    engine_url = f"postgresql://{db_user}:{db_pass}@{host}:{db_port}/{db_name}"
    db_engine = create_engine(engine_url)
    
    ml_engine = MLApprovalEngine(ticker="NIFTY", timeframe=1)
    with db_engine.connect() as conn:
        score = ml_engine.get_approval_score_sync(conn)
        print(f"ML Approval Score (Sync): {score}")
    print(f"ML Approval Score: {score}")
