from __future__ import annotations

from typing import Any

from .models import ProcessedPost


class TelegramPoster:
    def __init__(self, client: Any, destination: str | int | None) -> None:
        self.client = client
        self.destination = destination

    @staticmethod
    def split_text(text: str, limit: int = 4096) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at <= 0:
                split_at = remaining.rfind(" ", 0, limit + 1)
            if split_at <= 0:
                split_at = limit
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        return [chunk for chunk in chunks if chunk]

    async def publish(self, post: ProcessedPost) -> None:
        if self.destination is None:
            return
        if post.attachment_paths:
            caption = post.text if len(post.text) <= 1024 else None
            await self.client.send_file(
                self.destination,
                post.attachment_paths,
                caption=caption,
            )
            if caption is not None:
                return
        for chunk in self.split_text(post.text):
            await self.client.send_message(self.destination, chunk)
