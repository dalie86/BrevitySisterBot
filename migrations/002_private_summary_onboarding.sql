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
