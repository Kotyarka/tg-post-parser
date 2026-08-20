from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PostAnalysis:
    is_duplicate: bool
    is_advertisement: bool
    reason: str


@dataclass(slots=True)
class IncomingPost:
    source: str
    chat_id: int
    message_id: int
    text: str
    image_paths: list[Path] = field(default_factory=list)
    attachment_paths: list[Path] = field(default_factory=list)


@dataclass(slots=True)
class ProcessedPost:
    text: str
    source: str
    chat_id: int
    message_id: int
    image_paths: list[Path] = field(default_factory=list)
    attachment_paths: list[Path] = field(default_factory=list)
