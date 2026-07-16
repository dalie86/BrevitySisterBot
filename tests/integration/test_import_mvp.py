import hashlib
import sqlite3
from pathlib import Path

from scripts.import_mvp import read_snapshot


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_snapshot_opens_mvp_database_without_modifying_it(tmp_path) -> None:
    path = tmp_path / "bot.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE tracked_chats (
            chat_id INTEGER PRIMARY KEY, title TEXT, enabled INTEGER, created_at TEXT
        );
        CREATE TABLE messages (
            telegram_message_id INTEGER, chat_id INTEGER, user_id INTEGER,
            username TEXT, full_name TEXT, text TEXT, sent_at TEXT
        );
        CREATE TABLE prepared_summaries (
            chat_id INTEGER, summary_date TEXT, summary_text TEXT, prepared_at TEXT
        );
        INSERT INTO tracked_chats VALUES (-1001, 'Group', 1, '2026-07-01T10:00:00');
        INSERT INTO messages VALUES (
            7, -1001, 42, '@pavel', 'Pavel', 'Привет', '2026-07-16T12:00:00'
        );
        INSERT INTO prepared_summaries VALUES (
            -1001, '2026-07-15', 'Сводка', '2026-07-15T20:45:00'
        );
        """
    )
    connection.commit()
    connection.close()
    before = digest(path)

    snapshot = read_snapshot(path)

    assert digest(path) == before
    assert snapshot.chats[0]["chat_id"] == -1001
    assert snapshot.messages[0]["telegram_message_id"] == 7
    assert snapshot.summaries[0]["summary_text"] == "Сводка"
