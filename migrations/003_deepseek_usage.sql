ALTER TABLE prepared_summaries ADD COLUMN cache_hit_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN cache_miss_input_tokens INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN llm_call_count INT NULL;
ALTER TABLE prepared_summaries ADD COLUMN estimated_cost_microusd BIGINT NULL;
