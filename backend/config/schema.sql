-- Pipeline DataOps - Credit Card Fraud Detection
-- Database Schema: PostgreSQL
-- Reference DDL — actual table creation is in backend/src/loader.py create_tables()

-- Customers table (deduplicated by cc_num_hashed)
CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR(64) PRIMARY KEY,
    gender VARCHAR(1),
    city VARCHAR(100),
    state VARCHAR(2),
    zip VARCHAR(10),
    city_pop INTEGER,
    job VARCHAR(200),
    age_at_transaction INTEGER
);

-- Merchants table (deduplicated by name + category)
CREATE TABLE IF NOT EXISTS merchants (
    merchant_id SERIAL PRIMARY KEY,
    merchant_name VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    UNIQUE(merchant_name, category)
);

-- Transactions table (main fact table)
CREATE TABLE IF NOT EXISTS transactions (
    trans_num VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(64) REFERENCES customers(customer_id),
    merchant_id INTEGER REFERENCES merchants(merchant_id),
    amt DECIMAL(12,2),
    trans_date_trans_time TIMESTAMP,
    trans_hour INTEGER,
    trans_day_of_week INTEGER,
    trans_month INTEGER,
    distance_km DECIMAL(10,2),
    is_fraud INTEGER CHECK (is_fraud IN (0, 1)),
    unix_time BIGINT,
    merch_lat DECIMAL(10,6),
    merch_long DECIMAL(10,6),
    category VARCHAR(50),
    city VARCHAR(100),
    state VARCHAR(2)
);

-- Pipeline execution logs
CREATE TABLE IF NOT EXISTS pipeline_logs (
    log_id SERIAL PRIMARY KEY,
    run_id VARCHAR(36),
    stage VARCHAR(20),
    status VARCHAR(20),
    records_in INTEGER,
    records_out INTEGER,
    records_rejected INTEGER,
    duration_seconds DECIMAL(10,2),
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Rejected records with reasons
CREATE TABLE IF NOT EXISTS rejected_records (
    reject_id SERIAL PRIMARY KEY,
    run_id VARCHAR(36),
    trans_num VARCHAR(64),
    original_data JSONB,
    rejection_reason TEXT,
    stage VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model predictions
CREATE TABLE IF NOT EXISTS model_predictions (
    prediction_id SERIAL PRIMARY KEY,
    trans_num VARCHAR(64) REFERENCES transactions(trans_num),
    model_version VARCHAR(32),
    model_type VARCHAR(32),
    prediction INTEGER NOT NULL,
    probability DECIMAL(6,4),
    risk_level VARCHAR(16),
    features_used JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model training history
CREATE TABLE IF NOT EXISTS model_training_runs (
    training_run_id SERIAL PRIMARY KEY,
    model_type VARCHAR(32),
    hyperparameters JSONB,
    cv_scores JSONB,
    test_metrics JSONB,
    feature_importance JSONB,
    model_path VARCHAR(256),
    training_duration_seconds DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_transactions_customer ON transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions(merchant_id);
CREATE INDEX IF NOT EXISTS idx_transactions_fraud ON transactions(is_fraud);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(trans_date_trans_time);
CREATE INDEX IF NOT EXISTS idx_pipeline_logs_run ON pipeline_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_records(run_id);
CREATE INDEX IF NOT EXISTS idx_predictions_trans ON model_predictions(trans_num);
