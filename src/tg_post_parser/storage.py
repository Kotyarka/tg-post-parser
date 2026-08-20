"""SQLite-хранилище истории обработки и исходных текстов постов."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class PostStore:
    """Управляет состоянием обработанных постов в локальной базе SQLite."""

    def __init__(self, path: Path) -> None:
        """Открывает базу данных и выполняет необходимые миграции схемы."""
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
        columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(processed_posts)").fetchall()
        }
        if "original_text" not in columns:
            self._connection.execute("ALTER TABLE processed_posts ADD COLUMN original_text TEXT")
        if "status" not in columns:
            self._connection.execute("ALTER TABLE processed_posts ADD COLUMN status TEXT")
        if "filter_reason" not in columns:
            self._connection.execute("ALTER TABLE processed_posts ADD COLUMN filter_reason TEXT")
        self._connection.commit()

    def contains(self, chat_id: int, message_id: int) -> bool:
        """Проверяет, был ли Telegram-пост уже обработан."""
        row = self._connection.execute(
            "SELECT 1 FROM processed_posts WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return row is not None

    def recent_published_texts(self, hours: int) -> list[str]:
        """Возвращает исходные тексты опубликованных постов за заданный период."""
        rows = self._connection.execute(
            """SELECT original_text FROM processed_posts
               WHERE status = 'published'
                 AND original_text IS NOT NULL
                 AND original_text != ''
                 AND processed_at >= datetime('now', ?)
               ORDER BY processed_at DESC""",
            (f"-{hours} hours",),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def mark_processed(
        self,
        chat_id: int,
        message_id: int,
        original_text: str | None = None,
        status: str | None = None,
        filter_reason: str | None = None,
    ) -> None:
        """Фиксирует результат обработки поста и причину возможной фильтрации."""
        self._connection.execute(
            """INSERT OR IGNORE INTO processed_posts(
                   chat_id, message_id, original_text, status, filter_reason
               ) VALUES (?, ?, ?, ?, ?)""",
            (chat_id, message_id, original_text, status, filter_reason),
        )
        self._connection.commit()

    def close(self) -> None:
        """Закрывает соединение с SQLite."""
        self._connection.close()

    def __enter__(self) -> "PostStore":
        """Возвращает хранилище при входе в контекстный менеджер."""
        return self

    def __exit__(self, *_: object) -> None:
        """Закрывает базу при выходе из контекстного менеджера."""
        self.close()
