import sqlite3
from pathlib import Path

from tg_post_parser.storage import PostStore


def test_store_marks_post_only_by_chat_and_message(tmp_path: Path) -> None:
    with PostStore(tmp_path / "state.db") as store:
        assert not store.contains(10, 20)
        store.mark_processed(10, 20)
        assert store.contains(10, 20)
        assert not store.contains(11, 20)


def test_recent_history_contains_only_published_posts(tmp_path: Path) -> None:
    with PostStore(tmp_path / "state.db") as store:
        store.mark_processed(1, 1, "Опубликовано", "published")
        store.mark_processed(1, 2, "Реклама", "filtered_advertisement")

        assert store.recent_published_texts(24) == ["Опубликовано"]


def test_store_migrates_existing_database_without_losing_ids(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE processed_posts (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, message_id)
        )"""
    )
    connection.execute("INSERT INTO processed_posts(chat_id, message_id) VALUES (10, 20)")
    connection.commit()
    connection.close()

    with PostStore(database) as store:
        assert store.contains(10, 20)
        columns = {
            row[1]
            for row in store._connection.execute("PRAGMA table_info(processed_posts)").fetchall()
        }

    assert {"original_text", "status", "filter_reason"} <= columns
