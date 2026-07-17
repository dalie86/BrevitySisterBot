from pathlib import Path

from app.repositories.mysql import migration_files


def test_initial_migration_defines_required_tables_and_unique_keys() -> None:
    files = migration_files(Path("migrations"))
    sql = files[0].read_text(encoding="utf-8")
    required_tables = {
        "telegram_updates",
        "telegram_effects",
        "tracked_chats",
        "chat_members",
        "messages",
        "prepared_summaries",
        "scheduled_publications",
        "archived_messages",
        "archived_summaries",
        "daily_lifecycle",
        "job_locks",
        "schema_migrations",
    }
    for table in required_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "UNIQUE KEY uq_messages_chat_message (chat_id, telegram_message_id)" in sql
    assert "UNIQUE KEY uq_scheduled_publication_day (chat_id, summary_date)" in sql
    assert "CHARSET=utf8mb4" in sql


def test_private_onboarding_migration_is_additive() -> None:
    files = migration_files(Path("migrations"))
    assert [path.name for path in files] == [
        "001_initial.sql",
        "002_private_summary_onboarding.sql",
    ]

    sql = files[1].read_text(encoding="utf-8")
    assert "ADD COLUMN private_link_token CHAR(32) NULL" in sql
    assert "MODIFY private_link_token CHAR(32) NOT NULL" in sql
    assert "UNIQUE KEY uq_tracked_chats_private_link" in sql
    assert "CREATE TABLE chat_share_requests" in sql


def test_migration_files_are_sorted_and_only_include_sql(tmp_path: Path) -> None:
    (tmp_path / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignore", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in migration_files(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
    ]
