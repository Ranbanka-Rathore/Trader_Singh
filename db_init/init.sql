-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 1. PostgreSQL Tables (Standard)

-- Trade History
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    strategy_type VARCHAR(50),
    spot_price DECIMAL(15, 2),
    leg_1_sell DECIMAL(15, 2),
    leg_2_buy DECIMAL(15, 2),
    net_credit_per_share DECIMAL(15, 2),
    max_risk_per_share DECIMAL(15, 2),
    risk_reward_ratio VARCHAR(20),
    win_probability DECIMAL(5, 2),
    learning_context JSONB,
    vol_surge_multiplier DECIMAL(15, 4),
    coi_pcr DECIMAL(15, 4),
    bias VARCHAR(10),
    execution_time TIME,
    mode VARCHAR(10),
    lots_sized INTEGER,
    entry_date TIMESTAMP,
    entry_spot_price DECIMAL(15, 2),
    highest_seen DECIMAL(15, 2),
    lowest_seen DECIMAL(15, 2),
    dynamic_sl DECIMAL(15, 2),
    exit_date TIMESTAMP,
    exit_price DECIMAL(15, 2),
    exit_reason TEXT,
    realized_pnl DECIMAL(15, 2),
    net_delta DECIMAL(10, 4),
    net_gamma DECIMAL(10, 4),
    net_theta DECIMAL(10, 4),
    net_vega DECIMAL(10, 4)
);

-- Open Positions (similar to trades but active)
CREATE TABLE IF NOT EXISTS open_positions (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    strategy_type VARCHAR(50),
    spot_price DECIMAL(15, 2),
    leg_1_sell DECIMAL(15, 2),
    leg_2_buy DECIMAL(15, 2),
    net_credit_per_share DECIMAL(15, 2),
    max_risk_per_share DECIMAL(15, 2),
    risk_reward_ratio VARCHAR(20),
    win_probability DECIMAL(5, 2),
    learning_context JSONB,
    vol_surge_multiplier DECIMAL(15, 4),
    coi_pcr DECIMAL(15, 4),
    bias VARCHAR(10),
    execution_time TIME,
    mode VARCHAR(10),
    lots_sized INTEGER,
    entry_date TIMESTAMP,
    entry_spot_price DECIMAL(15, 2),
    highest_seen DECIMAL(15, 2),
    lowest_seen DECIMAL(15, 2),
    dynamic_sl DECIMAL(15, 2),
    net_delta DECIMAL(10, 4),
    net_gamma DECIMAL(10, 4),
    net_theta DECIMAL(10, 4),
    net_vega DECIMAL(10, 4)
);

-- 2. TimescaleDB Hypertables (Time-Series)

-- Market Indicators (e.g., historical_5min.json)
CREATE TABLE IF NOT EXISTS market_indicators (
    timestamp TIMESTAMP NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    timeframe INTEGER NOT NULL, -- in minutes
    call_oi BIGINT,
    put_oi BIGINT,
    oi_diff BIGINT,
    pcr DECIMAL(15, 4),
    vwap DECIMAL(15, 2),
    price DECIMAL(15, 2)
);

-- Check if hypertable already exists before creating
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'market_indicators') THEN
        PERFORM create_hypertable('market_indicators', 'timestamp');
    END IF;
END $$;

-- Option Chain Snapshots (e.g., oi_memory_bank.json)
CREATE TABLE IF NOT EXISTS option_chain_data (
    timestamp TIMESTAMP NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    strike DECIMAL(15, 2) NOT NULL,
    call_coi BIGINT,
    put_coi BIGINT,
    call_oi_chg BIGINT,
    put_oi_chg BIGINT
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = 'option_chain_data') THEN
        PERFORM create_hypertable('option_chain_data', 'timestamp');
    END IF;
END $$;
