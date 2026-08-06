from __future__ import annotations

import sqlite3
from pathlib import Path


class PostStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS processed_posts (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, message_id)
            )"""
        )
        self._connection.commit()

    def contains(self, chat_id: int, message_id: int) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM processed_posts WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return row is not None

    def mark_processed(self, chat_id: int, message_id: int) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO processed_posts(chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id),
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PostStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

