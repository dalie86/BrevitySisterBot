CREATE TABLE IF NOT EXISTS schema_migrations (
    filename VARCHAR(255) PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id BIGINT PRIMARY KEY,
    payload_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    available_at DATETIME(6) NOT NULL,
    claimed_at DATETIME(6),
    completed_at DATETIME(6),
    last_error_code VARCHAR(64),
    last_error_message VARCHAR(1000),
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    KEY idx_updates_queue (status, available_at, claimed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tracked_chats (
    chat_id BIGINT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow',
    scheduled_publish_time TIME NOT NULL DEFAULT '21:00:00',
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_members (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    full_name VARCHAR(255) NOT NULL DEFAULT '',
    first_seen_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    summary_linked_at DATETIME(6),
    last_membership_check_at DATETIME(6),
    last_membership_status VARCHAR(32),
    PRIMARY KEY (chat_id, user_id),
    KEY idx_chat_members_user (user_id, last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    user_id BIGINT,
    username VARCHAR(255) NOT NULL DEFAULT '',
    full_name VARCHAR(255) NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    sent_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_messages_chat_message (chat_id, telegram_message_id),
    KEY idx_messages_chat_sent (chat_id, sent_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS prepared_summaries (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    summary_date DATE NOT NULL,
    summary_kind VARCHAR(32) NOT NULL,
    requester_user_id BIGINT,
    source_watermark BIGINT,
    source_hash CHAR(64) NOT NULL,
    summary_text TEXT,
    status VARCHAR(32) NOT NULL,
    provider VARCHAR(64),
    model VARCHAR(255),
    latency_ms INT,
    input_tokens INT,
    output_tokens INT,
    input_characters INT,
    fallback_type VARCHAR(64),
    prepared_at DATETIME(6),
    sent_at DATETIME(6),
    last_error_code VARCHAR(64),
    last_error_message VARCHAR(1000),
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_summary_source (chat_id, summary_date, summary_kind, source_hash),
    KEY idx_summary_schedule (summary_date, summary_kind, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS summary_selections (
    selection_token CHAR(32) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    KEY idx_summary_selections_user (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scheduled_publications (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    summary_date DATE NOT NULL,
    prepared_summary_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    telegram_message_id BIGINT,
    available_at DATETIME(6) NOT NULL,
    claimed_at DATETIME(6),
    sent_at DATETIME(6),
    last_error_message VARCHAR(1000),
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_scheduled_publication_day (chat_id, summary_date),
    KEY idx_publications_queue (status, available_at, claimed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS archived_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    user_id BIGINT,
    username VARCHAR(255) NOT NULL DEFAULT '',
    full_name VARCHAR(255) NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    sent_at DATETIME(6) NOT NULL,
    archived_date DATE NOT NULL,
    archived_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_archived_messages_chat_message (chat_id, telegram_message_id),
    KEY idx_archived_messages_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS archived_summaries (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_summary_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    summary_date DATE NOT NULL,
    summary_kind VARCHAR(32) NOT NULL,
    summary_text TEXT NOT NULL,
    provider VARCHAR(64),
    model VARCHAR(255),
    fallback_type VARCHAR(64),
    prepared_at DATETIME(6),
    sent_at DATETIME(6),
    archived_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_archived_summary_source (source_summary_id),
    KEY idx_archived_summaries_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_lifecycle (
    chat_id BIGINT NOT NULL,
    summary_date DATE NOT NULL,
    source_message_count INT NOT NULL DEFAULT 0,
    source_watermark BIGINT,
    source_checksum CHAR(64),
    automatic_outcome VARCHAR(64),
    summary_prepared_at DATETIME(6),
    summary_sent_at DATETIME(6),
    archive_completed_at DATETIME(6),
    archived_message_count INT,
    archive_verified TINYINT(1) NOT NULL DEFAULT 0,
    working_cleanup_completed_at DATETIME(6),
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (chat_id, summary_date),
    KEY idx_daily_lifecycle_cleanup (archive_verified, working_cleanup_completed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS job_locks (
    lock_name VARCHAR(128) PRIMARY KEY,
    owner_token CHAR(36) NOT NULL,
    acquired_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    KEY idx_job_locks_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
