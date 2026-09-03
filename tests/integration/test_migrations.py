from pathlib import Path

import pymysql
import pytest

from app.repositories.mysql import (
    MySqlDatabase,
    _split_sql_statements,
    _verify_migration_002_schema,
    _verify_migration_003_schema,
    apply_migrations,
    migration_files,
)


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
        "003_deepseek_usage.sql",
    ]

    sql = files[1].read_text(encoding="utf-8")
    assert "ADD COLUMN private_link_token CHAR(32) NULL" in sql
    assert "MODIFY private_link_token CHAR(32) NOT NULL" in sql
    assert "UNIQUE KEY uq_tracked_chats_private_link" in sql
    assert "CREATE TABLE chat_share_requests" in sql


def test_deepseek_usage_migration_is_additive_and_preserves_old_rows() -> None:
    files = migration_files(Path("migrations"))
    sql = files[2].read_text(encoding="utf-8")

    statements = _split_sql_statements(sql)
    assert statements[:4] == [
        "ALTER TABLE prepared_summaries ADD COLUMN cache_hit_input_tokens INT NULL",
        "ALTER TABLE prepared_summaries ADD COLUMN cache_miss_input_tokens INT NULL",
        "ALTER TABLE prepared_summaries ADD COLUMN llm_call_count INT NULL",
        "ALTER TABLE prepared_summaries ADD COLUMN estimated_cost_microusd BIGINT NULL",
    ]
    assert len(statements) == 5
    snapshot_sql = statements[4]
    assert snapshot_sql.startswith("CREATE TABLE IF NOT EXISTS llm_balance_snapshots")
    assert "id BIGINT AUTO_INCREMENT PRIMARY KEY" in snapshot_sql
    assert "snapshot_id" not in snapshot_sql
    assert "provider VARCHAR(32) NOT NULL" in snapshot_sql
    assert "currency VARCHAR(8) NOT NULL" in snapshot_sql
    assert "granted_balance DECIMAL(18, 6) NOT NULL" in snapshot_sql
    assert "topped_up_balance DECIMAL(18, 6) NOT NULL" in snapshot_sql
    assert "is_available TINYINT(1) NOT NULL" in snapshot_sql
    assert "captured_at DATETIME(6) NOT NULL" in snapshot_sql
    # Moscow Monday midnight is persisted as a precise UTC Sunday 21:00 instant;
    # DATE would lose the local-week identity used by repository equality reads.
    assert "report_week_start DATETIME(6) NOT NULL" in snapshot_sql
    assert "report_text TEXT NOT NULL" in snapshot_sql
    assert (
        "UNIQUE KEY uq_llm_balance_report_week "
        "(provider, report_week_start)"
    ) in snapshot_sql
    assert "CHARSET=utf8mb4" in snapshot_sql
    assert "UPDATE " not in sql.upper()
    assert "DELETE " not in sql.upper()


def test_deepseek_schema_design_documents_precise_local_week_identity() -> None:
    design = Path(
        "docs/superpowers/specs/2026-09-02-deepseek-integration-0.3.0-design.md"
    ).read_text(encoding="utf-8")
    snapshot_schema = design.split("Добавляется таблица `llm_balance_snapshots`:", 1)[
        1
    ].split("```", 2)[1]

    assert "report_week_start DATETIME(6)" in snapshot_schema
    assert "UNIQUE KEY (provider, report_week_start)" in snapshot_schema
    assert "понедельник 00:00 по Москве" in design
    assert "воскресенье 21:00 UTC" in design
    assert "DATE" in design


def test_migration_files_are_sorted_and_only_include_sql(tmp_path: Path) -> None:
    (tmp_path / "002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignore", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in migration_files(tmp_path)] == [
        "001_first.sql",
        "002_second.sql",
    ]


class RestartCursor:
    def __init__(self, completed: set[str], interrupt_after: int | None) -> None:
        self.completed = completed
        self.interrupt_after = interrupt_after
        self.new_boundaries = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement: str) -> None:
        normalized = " ".join(statement.split())
        if normalized in self.completed:
            if "ADD COLUMN" in normalized:
                raise pymysql.err.OperationalError(1060, "Duplicate column")
            if "ADD UNIQUE KEY" in normalized or "ADD KEY" in normalized:
                raise pymysql.err.OperationalError(1061, "Duplicate key")
            if normalized.startswith("CREATE TABLE"):
                raise pymysql.err.OperationalError(1050, "Table exists")
            return
        self.completed.add(normalized)
        self.new_boundaries += 1
        if self.interrupt_after is not None and self.new_boundaries == self.interrupt_after:
            raise ConnectionError("simulated migration interruption")


class RestartConnection:
    def __init__(self, completed: set[str], interrupt_after: int | None) -> None:
        self.cursor_instance = RestartCursor(completed, interrupt_after)

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


MIGRATION_002_SQL = Path("migrations/002_private_summary_onboarding.sql").read_text(
    encoding="utf-8"
)


MIGRATION_003_SQL = Path("migrations/003_deepseek_usage.sql").read_text(
    encoding="utf-8"
)

MIGRATION_003_COLUMNS = {
    ("prepared_summaries", "cache_hit_input_tokens"): "YES",
    ("prepared_summaries", "cache_miss_input_tokens"): "YES",
    ("prepared_summaries", "llm_call_count"): "YES",
    ("prepared_summaries", "estimated_cost_microusd"): "YES",
    ("llm_balance_snapshots", "id"): "NO",
    ("llm_balance_snapshots", "provider"): "NO",
    ("llm_balance_snapshots", "currency"): "NO",
    ("llm_balance_snapshots", "total_balance"): "NO",
    ("llm_balance_snapshots", "report_week_start"): "NO",
    ("llm_balance_snapshots", "report_text"): "NO",
    ("llm_balance_snapshots", "granted_balance"): "NO",
    ("llm_balance_snapshots", "topped_up_balance"): "NO",
    ("llm_balance_snapshots", "is_available"): "NO",
    ("llm_balance_snapshots", "captured_at"): "NO",
}
MIGRATION_003_COLUMN_DETAILS = {
    ("prepared_summaries", "cache_hit_input_tokens"): {"data_type": "int"},
    ("prepared_summaries", "cache_miss_input_tokens"): {"data_type": "int"},
    ("prepared_summaries", "llm_call_count"): {"data_type": "int"},
    ("prepared_summaries", "estimated_cost_microusd"): {"data_type": "bigint"},
    ("llm_balance_snapshots", "id"): {
        "data_type": "bigint",
        "extra": "auto_increment",
    },
    ("llm_balance_snapshots", "provider"): {
        "data_type": "varchar",
        "character_maximum_length": 32,
    },
    ("llm_balance_snapshots", "currency"): {
        "data_type": "varchar",
        "character_maximum_length": 8,
    },
    ("llm_balance_snapshots", "total_balance"): {
        "data_type": "decimal",
        "numeric_precision": 18,
        "numeric_scale": 6,
    },
    ("llm_balance_snapshots", "report_week_start"): {
        "data_type": "datetime",
        "datetime_precision": 6,
    },
    ("llm_balance_snapshots", "report_text"): {"data_type": "text"},
    ("llm_balance_snapshots", "granted_balance"): {
        "data_type": "decimal",
        "numeric_precision": 18,
        "numeric_scale": 6,
    },
    ("llm_balance_snapshots", "topped_up_balance"): {
        "data_type": "decimal",
        "numeric_precision": 18,
        "numeric_scale": 6,
    },
    ("llm_balance_snapshots", "is_available"): {"data_type": "tinyint"},
    ("llm_balance_snapshots", "captured_at"): {
        "data_type": "datetime",
        "datetime_precision": 6,
    },
}
MIGRATION_003_INDEXES = {
    ("llm_balance_snapshots", "PRIMARY"): 0,
    ("llm_balance_snapshots", "uq_llm_balance_report_week"): 0,
}
MIGRATION_003_INDEX_COLUMNS = {
    ("llm_balance_snapshots", "PRIMARY"): ("id",),
    ("llm_balance_snapshots", "uq_llm_balance_report_week"): (
        "provider",
        "report_week_start",
    ),
}


@pytest.mark.parametrize(
    "interrupt_after", range(1, len(_split_sql_statements(MIGRATION_002_SQL)) + 1)
)
def test_migration_002_restarts_after_each_original_ddl_boundary(interrupt_after) -> None:
    completed: set[str] = set()
    database = object.__new__(MySqlDatabase)
    database.connect = lambda: RestartConnection(completed, interrupt_after)  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="interruption"):
        database.execute_script(
            MIGRATION_002_SQL, migration_name="002_private_summary_onboarding.sql"
        )

    database.connect = lambda: RestartConnection(completed, None)  # type: ignore[method-assign]
    database.execute_script(
        MIGRATION_002_SQL, migration_name="002_private_summary_onboarding.sql"
    )


@pytest.mark.parametrize(
    "interrupt_after", range(1, len(_split_sql_statements(MIGRATION_003_SQL)) + 1)
)
def test_migration_003_restarts_and_records_only_after_verification(
    interrupt_after: int, tmp_path: Path
) -> None:
    migration_path = tmp_path / "003_deepseek_usage.sql"
    migration_path.write_text(MIGRATION_003_SQL, encoding="utf-8")
    completed: set[str] = set()
    expected = {
        " ".join(statement.split())
        for statement in _split_sql_statements(MIGRATION_003_SQL)
    }
    runner = object.__new__(MySqlDatabase)

    class RestartableDatabase:
        def __init__(self) -> None:
            self.interrupt_after: int | None = interrupt_after
            self.events: list[str] = []
            self.applied: dict[str, str] = {}

        def applied_migrations(self):
            return self.applied

        def execute_script(self, sql, migration_name=None):
            self.events.append("execute")
            runner.connect = lambda: RestartConnection(  # type: ignore[method-assign]
                completed, self.interrupt_after
            )
            runner.execute_script(sql, migration_name=migration_name)

        def verify_migration(self, migration_name):
            self.events.append("verify")
            assert completed == expected
            _verify_migration_003_schema(
                MIGRATION_003_COLUMNS,
                MIGRATION_003_INDEXES,
                MIGRATION_003_INDEX_COLUMNS,
                MIGRATION_003_COLUMN_DETAILS,
            )

        def record_migration(self, filename, checksum):
            assert self.events[-1] == "verify"
            self.events.append("record")
            self.applied[filename] = checksum

    database = RestartableDatabase()

    with pytest.raises(ConnectionError, match="interruption"):
        apply_migrations(database, tmp_path)

    assert database.events == ["execute"]
    assert database.applied == {}

    database.interrupt_after = None

    assert apply_migrations(database, tmp_path) == ["003_deepseek_usage.sql"]
    assert database.events == ["execute", "execute", "verify", "record"]
    assert set(database.applied) == {"003_deepseek_usage.sql"}


def test_migration_002_propagates_unrelated_sql_error() -> None:
    class BrokenCursor(RestartCursor):
        def execute(self, statement: str) -> None:
            raise pymysql.err.OperationalError(1142, "command denied")

    class BrokenConnection(RestartConnection):
        def cursor(self):
            return BrokenCursor(set(), None)

    database = object.__new__(MySqlDatabase)
    database.connect = lambda: BrokenConnection(set(), None)  # type: ignore[method-assign]

    with pytest.raises(pymysql.err.OperationalError) as raised:
        database.execute_script(
            "ALTER TABLE tracked_chats ADD COLUMN private_link_token CHAR(32) NULL;",
            migration_name="002_private_summary_onboarding.sql",
        )

    assert raised.value.args[0] == 1142


def test_migration_002_verifies_schema_before_recording_checksum(tmp_path: Path) -> None:
    (tmp_path / "002_private_summary_onboarding.sql").write_text(
        "SELECT 1;", encoding="utf-8"
    )

    class FakeDatabase:
        def __init__(self) -> None:
            self.calls = []

        def applied_migrations(self):
            return {}

        def execute_script(self, sql, migration_name=None):
            self.calls.append(("execute", migration_name))

        def verify_migration(self, migration_name):
            self.calls.append(("verify", migration_name))

        def record_migration(self, filename, checksum):
            self.calls.append(("record", filename))

    database = FakeDatabase()

    apply_migrations(database, tmp_path)

    assert database.calls == [
        ("execute", "002_private_summary_onboarding.sql"),
        ("verify", "002_private_summary_onboarding.sql"),
        ("record", "002_private_summary_onboarding.sql"),
    ]


def test_migration_002_schema_verification_rejects_missing_postcondition() -> None:
    with pytest.raises(RuntimeError, match="tracked_chats.private_link_token"):
        _verify_migration_002_schema({}, {})


def test_migration_003_schema_verification_rejects_missing_postcondition() -> None:
    with pytest.raises(RuntimeError, match="cache_hit_input_tokens"):
        _verify_migration_003_schema({}, {}, {}, {})


def test_migration_003_schema_verification_accepts_all_postconditions() -> None:
    _verify_migration_003_schema(
        MIGRATION_003_COLUMNS,
        MIGRATION_003_INDEXES,
        MIGRATION_003_INDEX_COLUMNS,
        MIGRATION_003_COLUMN_DETAILS,
    )


def test_migration_003_schema_verification_rejects_missing_weekly_unique_key() -> None:
    with pytest.raises(RuntimeError, match="uq_llm_balance_report_week"):
        _verify_migration_003_schema(
            MIGRATION_003_COLUMNS,
            {},
            {},
            MIGRATION_003_COLUMN_DETAILS,
        )


def test_migration_003_schema_verification_rejects_currency_in_weekly_identity() -> None:
    wrong_columns = dict(MIGRATION_003_INDEX_COLUMNS)
    wrong_columns[("llm_balance_snapshots", "uq_llm_balance_report_week")] = (
        "provider",
        "currency",
        "report_week_start",
    )

    with pytest.raises(RuntimeError, match="uq_llm_balance_report_week columns"):
        _verify_migration_003_schema(
            MIGRATION_003_COLUMNS,
            MIGRATION_003_INDEXES,
            wrong_columns,
            MIGRATION_003_COLUMN_DETAILS,
        )


@pytest.mark.parametrize(
    ("column", "attribute", "wrong_value"),
    [
        (("llm_balance_snapshots", "provider"), "character_maximum_length", 64),
        (("llm_balance_snapshots", "currency"), "character_maximum_length", 16),
        (("llm_balance_snapshots", "report_week_start"), "data_type", "date"),
        (("llm_balance_snapshots", "total_balance"), "numeric_scale", 2),
    ],
)
def test_migration_003_schema_verification_rejects_wrong_column_details(
    column, attribute, wrong_value
) -> None:
    wrong_details = {
        key: dict(details) for key, details in MIGRATION_003_COLUMN_DETAILS.items()
    }
    wrong_details[column][attribute] = wrong_value

    with pytest.raises(
        RuntimeError, match=f"{column[0]}\\.{column[1]} {attribute}"
    ):
        _verify_migration_003_schema(
            MIGRATION_003_COLUMNS,
            MIGRATION_003_INDEXES,
            MIGRATION_003_INDEX_COLUMNS,
            wrong_details,
        )


def test_migration_003_schema_verification_requires_auto_increment_id() -> None:
    wrong_details = {
        key: dict(details) for key, details in MIGRATION_003_COLUMN_DETAILS.items()
    }
    wrong_details[("llm_balance_snapshots", "id")]["extra"] = ""

    with pytest.raises(RuntimeError, match="llm_balance_snapshots.id extra"):
        _verify_migration_003_schema(
            MIGRATION_003_COLUMNS,
            MIGRATION_003_INDEXES,
            MIGRATION_003_INDEX_COLUMNS,
            wrong_details,
        )


def test_migration_003_verifies_schema_before_recording_checksum(tmp_path: Path) -> None:
    (tmp_path / "003_deepseek_usage.sql").write_text("SELECT 1;", encoding="utf-8")

    class FakeDatabase:
        def __init__(self) -> None:
            self.calls = []

        def applied_migrations(self):
            return {}

        def execute_script(self, sql, migration_name=None):
            self.calls.append(("execute", migration_name))

        def verify_migration(self, migration_name):
            self.calls.append(("verify", migration_name))

        def record_migration(self, filename, checksum):
            self.calls.append(("record", filename))

    database = FakeDatabase()

    apply_migrations(database, tmp_path)

    assert database.calls == [
        ("execute", "003_deepseek_usage.sql"),
        ("verify", "003_deepseek_usage.sql"),
        ("record", "003_deepseek_usage.sql"),
    ]
