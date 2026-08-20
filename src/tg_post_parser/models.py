"""Модели данных для входящих, проанализированных и обработанных постов."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PostAnalysis:
    """Результат проверки поста на дубликат и рекламный характер."""
    is_duplicate: bool
    is_advertisement: bool
    reason: str


@dataclass(slots=True)
class IncomingPost:
    """Пост, полученный из Telegram вместе с локальными путями вложений."""
    source: str
    chat_id: int
    message_id: int
    text: str
    image_paths: list[Path] = field(default_factory=list)
    attachment_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class ProcessedPost:
    """Готовый к сохранению и публикации результат обработки поста."""
    text: str
    source: str
    chat_id: int
    message_id: int
    image_paths: list[Path] = field(default_factory=list)
    attachment_paths: list[Path] = field(default_factory=list)
