from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, utils

from .config import AppConfig, SourceConfig
from .models import IncomingPost, ProcessedPost
from .service import PostProcessor

logger = logging.getLogger(__name__)


class TelegramMonitor:
    def __init__(self, config: AppConfig, processor: PostProcessor) -> None:
        self.config = config
        self.processor = processor
        tg = config.telegram
        self.client = TelegramClient(tg.session, tg.api_id, tg.api_hash)
        self._sources: dict[int, SourceConfig] = {}

    async def _resolve_sources(self) -> list[Any]:
        entities: list[Any] = []
        for source in self.config.sources:
            if not source.enabled:
                continue
            entity = await self.client.get_entity(source.chat)
            self._sources[int(utils.get_peer_id(entity))] = source
            entities.append(entity)
            logger.info("Monitoring %s (id=%s)", source.chat, entity.id)
        return entities

    async def _download_attachment(self, message: Any, chat_id: int) -> tuple[list[Path], list[Path]]:
        """Download any Telegram file and separately identify LLM-compatible images."""
        if not getattr(message, "media", None):
            return [], []
        directory = self.config.storage.output_dir / "media" / str(chat_id)
        directory.mkdir(parents=True, exist_ok=True)
        file_info = getattr(message, "file", None)
        original_name = Path(getattr(file_info, "name", "") or "").name
        extension = getattr(file_info, "ext", "") or ""
        if getattr(message, "photo", None):
            target = directory / f"{message.id}{extension or '.jpg'}"
        elif original_name:
            target = directory / f"{message.id}_{original_name}"
        elif file_info is not None:
            target = directory / f"{message.id}{extension or '.bin'}"
        else:
            # Web previews, polls and locations are media objects but not downloadable files.
            return [], []
        downloaded = await message.download_media(file=str(target))
        if not downloaded:
            return [], []
        path = Path(downloaded)
        mime_type = (getattr(file_info, "mime_type", "") or "").lower()
        is_image = bool(getattr(message, "photo", None)) or mime_type.startswith("image/")
        return [path], [path] if is_image else []

    @staticmethod
    def _declared_file_size(message: Any) -> int:
        file_info = getattr(message, "file", None)
        return max(0, int(getattr(file_info, "size", 0) or 0))

    async def _collect_attachments(
        self, messages: list[Any], chat_id: int
    ) -> tuple[list[Path], list[Path]] | None:
        """Return None when the aggregate post attachment limit is exceeded."""
        limit_mb = self.config.storage.max_post_download_mb
        limit_bytes = int(limit_mb * 1024 * 1024)
        declared_total = sum(self._declared_file_size(message) for message in messages)
        if declared_total > limit_bytes:
            logger.warning(
                "Skipping post in chat %s: declared attachments size %.2f MB exceeds %.2f MB",
                chat_id,
                declared_total / 1024 / 1024,
                limit_mb,
            )
            return None

        attachments: list[Path] = []
        images: list[Path] = []
        actual_total = 0
        for message in messages:
            downloaded, downloadable_images = await self._download_attachment(message, chat_id)
            attachments.extend(downloaded)
            images.extend(downloadable_images)
            for path in downloaded:
                actual_total += path.stat().st_size if path.exists() else self._declared_file_size(message)
            if actual_total > limit_bytes:
                logger.warning(
                    "Skipping post in chat %s: downloaded attachments size exceeds %.2f MB",
                    chat_id,
                    limit_mb,
                )
                for path in attachments:
                    path.unlink(missing_ok=True)
                return None
        return attachments, images

    @staticmethod
    def _split_text(text: str, limit: int = 4096) -> list[str]:
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

    async def _publish(self, post: ProcessedPost) -> None:
        destination = self.config.telegram.destination
        if destination is None:
            return
        if post.attachment_paths:
            # Telegram captions are limited to 1024 characters. Keep all text by
            # sending it as one or more messages when it does not fit the caption.
            caption = post.text if len(post.text) <= 1024 else None
            await self.client.send_file(destination, post.attachment_paths, caption=caption)
            if caption is not None:
                return
        for chunk in self._split_text(post.text):
            await self.client.send_message(destination, chunk)

    async def _handle(self, event: Any) -> None:
        message = event.message
        chat_id = int(event.chat_id)
        source = self._sources.get(chat_id)
        if source is None:
            logger.warning("No configuration found for chat %s", chat_id)
            return
        try:
            media = await self._collect_attachments([message], chat_id)
            if media is None:
                return
            attachments, images = media
            post = IncomingPost(
                source=str(source.chat),
                chat_id=chat_id,
                message_id=int(message.id),
                text=message.message or "",
                image_paths=images,
                attachment_paths=attachments,
            )
            result = await self.processor.process(post, source)
            if result:
                await self._publish(result)
                logger.info("Processed post %s/%s", chat_id, message.id)
        except Exception:
            logger.exception("Failed to process post %s/%s", chat_id, message.id)

    async def _handle_album(self, event: Any) -> None:
        chat_id = int(event.chat_id)
        source = self._sources.get(chat_id)
        if source is None:
            return
        messages = list(event.messages)
        first = messages[0]
        try:
            media = await self._collect_attachments(messages, chat_id)
            if media is None:
                return
            attachments, images = media
            texts: list[str] = []
            for message in messages:
                if message.message:
                    texts.append(message.message)
            post = IncomingPost(
                source=str(source.chat),
                chat_id=chat_id,
                message_id=int(first.id),
                text="\n".join(texts),
                image_paths=images,
                attachment_paths=attachments,
            )
            result = await self.processor.process(post, source)
            if result:
                await self._publish(result)
                logger.info("Processed album %s/%s", chat_id, first.id)
        except Exception:
            logger.exception("Failed to process album %s/%s", chat_id, first.id)

    async def _handle_single(self, event: Any) -> None:
        if getattr(event.message, "grouped_id", None) is None:
            await self._handle(event)

    async def run(self) -> None:
        await self.client.start()
        entities = await self._resolve_sources()
        self.client.add_event_handler(self._handle_single, events.NewMessage(chats=entities))
        self.client.add_event_handler(self._handle_album, events.Album(chats=entities))
        logger.info("Monitor is running; press Ctrl+C to stop")
        await self.client.run_until_disconnected()
