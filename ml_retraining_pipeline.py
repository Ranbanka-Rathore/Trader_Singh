import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
import datetime
from sqlalchemy import create_engine
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score
from dotenv import load_dotenv

load_dotenv()

class MLRetrainingPipeline:
    def __init__(self, ticker="NIFTY", timeframe=1):
        self.ticker = ticker
        self.timeframe = timeframe
        self.model_dir = "models"
        os.makedirs(self.model_dir, exist_ok=True)
        self.model_path = os.path.join(self.model_dir, f"xgboost_{ticker}_{timeframe}m.joblib")
        
        # Load DB credentials
        db_user = os.getenv("DB_USER", "trader")
        db_pass = os.getenv("DB_PASSWORD", "institutional_grade_password")
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "agentic_trader")
        
        self.db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    def fetch_training_data(self, days=365):
        """Extracts historical data from the relational database."""
        print(f"📡 Extracting last {days} days of data for {self.ticker}...")
        
        start_date = datetime.datetime.now() - datetime.timedelta(days=days)
        
        try:
            engine = create_engine(self.db_url)
            query = f"""
                SELECT timestamp, pcr, price, vwap, oi_diff FROM market_indicators
                WHERE ticker = '{self.ticker}' AND timeframe = {self.timeframe}
                  AND timestamp >= '{start_date.strftime("%Y-%m-%d %H:%M:%S")}'
                ORDER BY timestamp ASC
            """
            df = pd.read_sql(query, engine)
            
            if df.empty or len(df) < 50:
                print(f"⚠️ Insufficient data for retraining ({len(df)} rows). Harvest more data first.")
                return None
                
            # Convert to float types
            df['pcr'] = df['pcr'].astype(float).fillna(1.0)
            df['price'] = df['price'].astype(float).fillna(0.0)
            df['vwap'] = df['vwap'].astype(float).fillna(0.0)
            df['oi_diff'] = df['oi_diff'].astype(float).fillna(0.0)
            
            return df
        except Exception as e:
            print(f"❌ DB Retraining Extraction Error: {e}")
            return None

    def engineer_features(self, df):
        """Advanced feature engineering for the institutional model."""
        print("🛠️ Engineering features for model evolution...")
        
        # 1. Price Momentum
        df['returns'] = df['price'].pct_change()
        df['volatility'] = df['returns'].rolling(window=10).std()
        
        # 2. Institutional Indicators
        df['price_vs_vwap'] = (df['price'] - df['vwap']) / df['vwap']
        df['pcr_ma'] = df['pcr'].rolling(window=5).mean()
        df['pcr_velocity'] = df['pcr'].diff()
        
        # 3. Structural Z-Scores (Standardizing Market Regimes)
        pcr_std = df['pcr'].rolling(window=20).std()
        pcr_std = np.where(pcr_std == 0, 1.0, pcr_std) # avoid div by zero
        df['pcr_zscore'] = (df['pcr'] - df['pcr'].rolling(window=20).mean()) / pcr_std
        df['pcr_zscore'] = df['pcr_zscore'].fillna(0)
        
        # 4. Target Generation: Predict price increase of >0.1% in next 5 candles
        look_ahead = 5
        df['future_price'] = df['price'].shift(-look_ahead)
        df['target'] = (df['future_price'] > df['price'] * 1.001).astype(int)
        
        # Cleanse inf and nan values to prevent XGBoost training crashes
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.bfill().ffill()
        df = df.fillna(0.0)
        return df.dropna()

    def train_and_optimize(self, df):
        """Trains an optimized XGBoost model using TimeSeriesSplit."""
        features = ['pcr', 'price_vs_vwap', 'pcr_ma', 'pcr_velocity', 'pcr_zscore', 'volatility']
        X = df[features]
        y = df['target']

        # Reduced splits for small datasets in development
        n_splits = min(5, len(X) // 20)
        if n_splits < 2:
            n_splits = 2
            
        tscv = TimeSeriesSplit(n_splits=n_splits)
        
        best_acc = 0
        best_model = None

        print(f"🧠 Retraining XGBoost with {len(X)} samples using TimeSeries CV...")

        for train_index, test_index in tscv.split(X):
            X_train, X_test = X.iloc[train_index], X.iloc[test_index]
            y_train, y_test = y.iloc[train_index], y.iloc[test_index]

            model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                objective='binary:logistic'
            )
            
            model.fit(X_train, y_train)
            
            preds = model.predict(X_test)
            acc = accuracy_score(y_test, preds)
            
            if acc > best_acc:
                best_acc = acc
                best_model = model

        print(f"✅ Retraining Complete. Optimized Accuracy: {best_acc:.2%}")
        
        # Save the best model
        model_data = {
            "model": best_model,
            "features": features,
            "trained_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "accuracy": best_acc
        }
        
        joblib.dump(model_data, self.model_path)
        print(f"💾 Evolution Complete: Model saved to {self.model_path}")
        return model_data

if __name__ == "__main__":
    for ticker in ["NIFTY", "BANKNIFTY"]:
        pipeline = MLRetrainingPipeline(ticker=ticker, timeframe=1)
        raw_data = pipeline.fetch_training_data(days=365)
        if raw_data is not None:
            engineered_data = pipeline.engineer_features(raw_data)
            pipeline.train_and_optimize(engineered_data)
