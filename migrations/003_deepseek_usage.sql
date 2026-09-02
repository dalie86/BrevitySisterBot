ALTER TABLE prepared_summaries ADD COLUMN cache_hit_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN cache_miss_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN llm_call_count INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN estimated_cost_microusd BIGINT NULL;

CREATE TABLE IF NOT EXISTS llm_balance_snapshots (
    snapshot_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    provider VARCHAR(64) NOT NULL,
    currency VARCHAR(16) NOT NULL,
    total_balance DECIMAL(18, 6) NOT NULL,
    granted_balance DECIMAL(18, 6) NOT NULL,
    topped_up_balance DECIMAL(18, 6) NOT NULL,
    is_available TINYINT(1) NOT NULL,
    report_week_start DATETIME(6) NOT NULL,
    report_text TEXT NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_llm_balance_report_week (provider, currency, report_week_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
