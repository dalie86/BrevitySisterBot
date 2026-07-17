ALTER TABLE tracked_chats ADD COLUMN private_link_token CHAR(32) NULL;

UPDATE tracked_chats
SET private_link_token = REPLACE(UUID(), '-', '')
WHERE private_link_token IS NULL;

ALTER TABLE tracked_chats MODIFY private_link_token CHAR(32) NOT NULL;

ALTER TABLE tracked_chats
ADD UNIQUE KEY uq_tracked_chats_private_link (private_link_token);

CREATE TABLE chat_share_requests (
    request_id INT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    used_at DATETIME(6),
    created_at DATETIME(6) NOT NULL,
    KEY idx_chat_share_requests_user (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE chat_share_requests
ADD COLUMN idempotency_key VARCHAR(191) NULL;

UPDATE chat_share_requests
SET idempotency_key = CONCAT('legacy:', user_id, ':', request_id)
WHERE idempotency_key IS NULL;

ALTER TABLE chat_share_requests
MODIFY idempotency_key VARCHAR(191) NOT NULL;

ALTER TABLE chat_share_requests
ADD UNIQUE KEY uq_chat_share_requests_idempotency (idempotency_key);

ALTER TABLE chat_share_requests
ADD COLUMN reserved_update_id BIGINT NULL;

ALTER TABLE chat_share_requests
ADD COLUMN reserved_chat_id BIGINT NULL;

ALTER TABLE chat_share_requests
ADD KEY idx_chat_share_requests_reservation (reserved_update_id, used_at);

ALTER TABLE summary_selections
ADD COLUMN idempotency_key VARCHAR(191) NULL;

UPDATE summary_selections
SET idempotency_key = CONCAT('legacy:', selection_token)
WHERE idempotency_key IS NULL;

ALTER TABLE summary_selections
MODIFY idempotency_key VARCHAR(191) NOT NULL;

ALTER TABLE summary_selections
ADD UNIQUE KEY uq_summary_selections_idempotency (idempotency_key);

ALTER TABLE summary_selections
ADD COLUMN reserved_update_id BIGINT NULL;

ALTER TABLE summary_selections
ADD COLUMN consumed_at DATETIME(6) NULL;

ALTER TABLE summary_selections
ADD KEY idx_summary_selections_reservation (reserved_update_id, consumed_at);
