import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
import joblib
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

class MLAlphaPipeline:
    def __init__(self, ticker="NIFTY", timeframe=1):
        self.ticker = ticker
        self.timeframe = timeframe
        self.model_path = f"models/xgboost_{ticker}_{timeframe}m.joblib"
        os.makedirs("models", exist_ok=True)
        
        # Load DB credentials
        db_user = os.getenv("DB_USER", "trader")
        db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "agentic_trader")
        
        self.db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    def extract_and_label_data(self):
        """
        Extracts high-volume market indicators from PostgreSQL/TimescaleDB.
        """
        print(f"📡 Extracting institutional dataset for {self.ticker} ({self.timeframe}m)...")
        
        try:
            engine = create_engine(self.db_url)
            query = f"""
                SELECT timestamp, price, vwap, pcr, total_gex FROM market_indicators
                WHERE ticker = '{self.ticker}' AND timeframe = {self.timeframe}
                ORDER BY timestamp ASC
            """
            df = pd.read_sql(query, engine)
            
            if df.empty:
                print(f"❌ No data found in database for {self.ticker}.")
                return None

            # Convert to floats
            df['price'] = df['price'].astype(float)
            df['vwap'] = df['vwap'].astype(float)
            df['pcr'] = df['pcr'].astype(float)
            df['total_gex'] = df['total_gex'].astype(float)
            
            # Fill missing VWAP with 20-period moving average of price
            df['vwap'] = df['vwap'].fillna(df['price'].rolling(window=20, min_periods=1).mean())
            
            print(f"✅ Loaded {len(df)} candles.")
            
            # --- FEATURE ENGINEERING ---
            print("🛠️ Engineering institutional features...")
            df['price_vs_vwap'] = (df['price'] - df['vwap']) / df['vwap']
            
            # Institutional Flow Features
            df['gex_volatility'] = df['total_gex'].rolling(window=10).std()
            df['pcr_change'] = df['pcr'].diff()
            
            # Momentum Features
            df['returns_1m'] = df['price'].pct_change()
            df['returns_5m'] = df['price'].pct_change(5)
            df['volatility_20'] = df['returns_1m'].rolling(window=20).std()
            
            # Trend (Moving Averages)
            df['ma_20'] = df['price'].rolling(window=20).mean()
            df['ma_50'] = df['price'].rolling(window=50).mean()
            df['trend_signal'] = (df['ma_20'] > df['ma_50']).astype(int)
            
            # --- LABELING ---
            # Predict if price moves > 0.15% in the next 30 minutes (30 candles)
            look_ahead = 30
            df['future_price'] = df['price'].shift(-look_ahead)
            df['target'] = (df['future_price'] > df['price'] * 1.0015).astype(int)
            
            df = df.dropna()
            return df
        except Exception as e:
            print(f"❌ DB Extraction Error: {e}")
            return None

    def train_model(self):
        df = self.extract_and_label_data()
        if df is None or len(df) < 200:
            print(f"⚠️ Insufficient data for {self.ticker} training. Need at least 200 samples.")
            return

        features = [
            'price_vs_vwap', 'returns_1m', 'returns_5m', 'volatility_20', 
            'trend_signal', 'pcr', 'total_gex', 'gex_volatility', 'pcr_change'
        ]
        X = df[features]
        y = df['target']

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        print(f"🧠 Training High-Alpha XGBoost on {len(X_train)} samples...")
        
        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective='binary:logistic',
            tree_method='hist',
            random_state=42
        )
        
        model.fit(X_train, y_train)

        # Evaluate
        preds = model.predict(X_test)
        acc = accuracy_score(y_test, preds)
        print(f"✅ Training Complete. Test Accuracy: {acc:.2%}")
        
        # Save model
        model_data = {
            "model": model,
            "features": features,
            "trained_at": str(pd.Timestamp.now()),
            "samples": len(df)
        }
        joblib.dump(model_data, self.model_path)
        print(f"💾 Institutional Model saved to {self.model_path}")

if __name__ == "__main__":
    for ticker in ["NIFTY", "BANKNIFTY"]:
        pipeline = MLAlphaPipeline(ticker=ticker, timeframe=1)
        pipeline.train_model()
