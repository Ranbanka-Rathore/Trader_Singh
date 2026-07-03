import os
import datetime
from peewee import (
    SqliteDatabase, Model, CharField, DecimalField, 
    DateTimeField, IntegerField, TextField, TimeField, Proxy
)
from playhouse.sqlite_ext import JSONField as SqliteJSONField
from playhouse.postgres_ext import PostgresqlExtDatabase, JSONField as PostgresJSONField
from dotenv import load_dotenv

load_dotenv()

# Proxy for late binding
db_proxy = Proxy()

class BaseModel(Model):
    class Meta:
        database = db_proxy

# Define which JSON field to use based on database type
def get_json_field():
    if isinstance(db_proxy.obj, PostgresqlExtDatabase):
        return PostgresJSONField()
    return SqliteJSONField()

class Trade(BaseModel):
    ticker = CharField(max_length=20)
    strategy_type = CharField(max_length=50, null=True)
    spot_price = DecimalField(max_digits=15, decimal_places=2, null=True)
    leg_1_sell = DecimalField(max_digits=15, decimal_places=2, null=True)
    leg_2_buy = DecimalField(max_digits=15, decimal_places=2, null=True)
    net_credit_per_share = DecimalField(max_digits=15, decimal_places=2, null=True)
    max_risk_per_share = DecimalField(max_digits=15, decimal_places=2, null=True)
    risk_reward_ratio = CharField(max_length=20, null=True)
    win_probability = DecimalField(max_digits=5, decimal_places=2, null=True)
    learning_context = PostgresJSONField(null=True) # Default to Postgres style
    vol_surge_multiplier = DecimalField(max_digits=15, decimal_places=4, null=True)
    coi_pcr = DecimalField(max_digits=15, decimal_places=4, null=True)
    bias = CharField(max_length=10, null=True)
    execution_time = TimeField(null=True)
    mode = CharField(max_length=10, null=True)
    lots_sized = IntegerField(null=True)
    entry_date = DateTimeField(null=True)
    entry_spot_price = DecimalField(max_digits=15, decimal_places=2, null=True)
    highest_seen = DecimalField(max_digits=15, decimal_places=2, null=True)
    lowest_seen = DecimalField(max_digits=15, decimal_places=2, null=True)
    dynamic_sl = DecimalField(max_digits=15, decimal_places=2, null=True)
    
    # Greeks
    net_delta = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_gamma = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_theta = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_vega = DecimalField(max_digits=10, decimal_places=4, null=True)

    exit_date = DateTimeField(null=True)
    exit_price = DecimalField(max_digits=15, decimal_places=2, null=True)
    exit_reason = TextField(null=True)
    realized_pnl = DecimalField(max_digits=15, decimal_places=2, null=True)

    class Meta:
        table_name = 'trades'

class OpenPosition(BaseModel):
    ticker = CharField(max_length=20)
    strategy_type = CharField(max_length=50, null=True)
    spot_price = DecimalField(max_digits=15, decimal_places=2, null=True)
    leg_1_sell = DecimalField(max_digits=15, decimal_places=2, null=True)
    leg_2_buy = DecimalField(max_digits=15, decimal_places=2, null=True)
    net_credit_per_share = DecimalField(max_digits=15, decimal_places=2, null=True)
    max_risk_per_share = DecimalField(max_digits=15, decimal_places=2, null=True)
    risk_reward_ratio = CharField(max_length=20, null=True)
    win_probability = DecimalField(max_digits=5, decimal_places=2, null=True)
    learning_context = PostgresJSONField(null=True)
    vol_surge_multiplier = DecimalField(max_digits=15, decimal_places=4, null=True)
    coi_pcr = DecimalField(max_digits=15, decimal_places=4, null=True)
    bias = CharField(max_length=10, null=True)
    execution_time = TimeField(null=True)
    mode = CharField(max_length=10, null=True)
    lots_sized = IntegerField(null=True)
    entry_date = DateTimeField(null=True)
    entry_spot_price = DecimalField(max_digits=15, decimal_places=2, null=True)
    highest_seen = DecimalField(max_digits=15, decimal_places=2, null=True)
    lowest_seen = DecimalField(max_digits=15, decimal_places=2, null=True)
    dynamic_sl = DecimalField(max_digits=15, decimal_places=2, null=True)
    
    # Greeks
    net_delta = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_gamma = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_theta = DecimalField(max_digits=10, decimal_places=4, null=True)
    net_vega = DecimalField(max_digits=10, decimal_places=4, null=True)

    class Meta:
        table_name = 'open_positions'

class MarketIndicator(BaseModel):
    timestamp = DateTimeField()
    ticker = CharField(max_length=20)
    timeframe = IntegerField()
    call_oi = DecimalField(max_digits=20, decimal_places=0, null=True)
    put_oi = DecimalField(max_digits=20, decimal_places=0, null=True)
    oi_diff = DecimalField(max_digits=20, decimal_places=0, null=True)
    pcr = DecimalField(max_digits=15, decimal_places=4, null=True)
    vwap = DecimalField(max_digits=15, decimal_places=2, null=True)
    price = DecimalField(max_digits=15, decimal_places=2, null=True)
    total_gex = DecimalField(max_digits=25, decimal_places=2, null=True)
    poc = DecimalField(max_digits=15, decimal_places=2, null=True)

    class Meta:
        table_name = 'market_indicators'
        primary_key = False

class OptionChainData(BaseModel):
    timestamp = DateTimeField()
    ticker = CharField(max_length=20)
    strike = DecimalField(max_digits=15, decimal_places=2)
    call_coi = DecimalField(max_digits=20, decimal_places=0, null=True)
    put_coi = DecimalField(max_digits=20, decimal_places=0, null=True)
    call_oi_chg = DecimalField(max_digits=20, decimal_places=0, null=True)
    put_oi_chg = DecimalField(max_digits=20, decimal_places=0, null=True)

    class Meta:
        table_name = 'option_chain_data'
        primary_key = False

class SignalAudit(BaseModel):
    timestamp = DateTimeField(default=datetime.datetime.now)
    ticker = CharField(max_length=20)
    pa_status = CharField(max_length=50)
    pcr = DecimalField(max_digits=10, decimal_places=2)
    gex_mn = DecimalField(max_digits=15, decimal_places=2)
    ml_score = DecimalField(max_digits=5, decimal_places=2)
    committee_verdict = CharField(max_length=20) # EXECUTE or HOLD
    committee_reasoning = TextField(null=True)
    backtester_rule_match = CharField(max_length=100, null=True)

    class Meta:
        table_name = 'signal_audit'

class DatabaseManager:
    def __init__(self):
        self.use_postgres = os.getenv("USE_POSTGRES", "true").lower() == "true"
        
        if self.use_postgres:
            print("🐘 Connecting to PostgreSQL/TimescaleDB...")
            self.db = PostgresqlExtDatabase(
                os.getenv("DB_NAME", "agentic_trader"),
                user=os.getenv("DB_USER", "trader"),
                password=os.getenv("DB_PASSWORD", "institutional_grade_password"),
                host=os.getenv("DB_HOST", "localhost"),
                port=int(os.getenv("DB_PORT", 5432))
            )
        else:
            print("📁 Connecting to SQLite...")
            db_path = os.getenv("DB_PATH", "agentic_trader.db")
            self.db = SqliteDatabase(db_path)
            # Override JSONField for SQLite
            Trade.learning_context = SqliteJSONField(null=True)
            OpenPosition.learning_context = SqliteJSONField(null=True)

        db_proxy.initialize(self.db)

    def connect(self):
        try:
            if self.db.is_closed():
                self.db.connect()
        except Exception as e:
            print(f"⚠️ DB Connection Error: {e}")

    def execute_with_retry(self, func, *args, **kwargs):
        """Wrapper to handle 'connection already closed' errors."""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "connection already closed" in str(e).lower() or "closed" in str(e).lower():
                print("🔄 DB Connection lost. Reconnecting...")
                self.db.close()
                self.db.connect()
                return func(*args, **kwargs)
            raise e

    def add_signal_audit(self, audit_data):
        return self.execute_with_retry(SignalAudit.create, **audit_data)

    def get_signal_audits(self, limit=50):
        return self.execute_with_retry(lambda: list(SignalAudit.select().order_by(SignalAudit.timestamp.desc()).limit(limit).dicts()))

    def add_open_position(self, trade_data):
        return self.execute_with_retry(OpenPosition.create, **trade_data)

    def get_open_positions(self):
        return self.execute_with_retry(lambda: list(OpenPosition.select().dicts()))

    def save_market_indicator(self, data):
        return self.execute_with_retry(MarketIndicator.create, **data)

    def save_option_chain_data(self, data_list):
        def _save():
            with self.db.atomic():
                OptionChainData.insert_many(data_list).execute()
        return self.execute_with_retry(_save)

# Global instance
db_manager = DatabaseManager()
