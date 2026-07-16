from datetime import datetime, timezone

from app.repositories.mysql import MySqlLifecycleStore
from tests.integration.test_mysql_repositories import RecordingConnection


def test_cleanup_deletes_only_rows_joined_to_verified_archive() -> None:
    connection = RecordingConnection(rowcount=5)
    store = MySqlLifecycleStore(lambda: connection)

    deleted = store.cleanup_verified(
        "2026-07-16",
        datetime(2026, 7, 16, 18, 30, tzinfo=timezone.utc),
    )

    delete_sql = connection.cursor_instance.executions[0][0]
    summary_delete_sql = connection.cursor_instance.executions[1][0]
    assert deleted == 5
    assert "JOIN archived_messages" in delete_sql
    assert "lifecycle.archive_verified = 1" in delete_sql
    assert "automatic_summary_skipped" in delete_sql
    assert "JOIN archived_summaries" in summary_delete_sql
    assert connection.commits == 1
    assert connection.closed is True
