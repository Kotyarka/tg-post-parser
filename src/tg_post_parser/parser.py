from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, utils

from .analyzer import PostAnalyzer
from .config import AnalysisConfig, AppConfig, SourceConfig
from .models import IncomingPost, ProcessedPost
from .poster import TelegramPoster
from .rewriter import PostRewriter
from .storage import PostStore

logger = logging.getLogger(__name__)


class PostParser:
    def __init__(
        self,
        analyzer: PostAnalyzer,
        rewriter: PostRewriter,
        store: PostStore,
        output_dir: Path,
        analysis: AnalysisConfig | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.rewriter = rewriter
        self.store = store
        self.output_dir = output_dir
        self.analysis = analysis or AnalysisConfig()
        output_dir.mkdir(parents=True, exist_ok=True)

    async def process(self, post: IncomingPost, source: SourceConfig) -> ProcessedPost | None:
        if self.store.contains(post.chat_id, post.message_id):
            logger.debug("Skipping already processed post %s/%s", post.chat_id, post.message_id)
            return None
        if self.analysis.enabled:
            history = self.store.recent_published_texts(self.analysis.history_hours)
            analysis = await self.analyzer.analyze(post.text, post.image_paths, history)
            if analysis.is_duplicate or analysis.is_advertisement:
                status = (
                    "filtered_advertisement"
                    if analysis.is_advertisement
                    else "filtered_duplicate"
                )
                self.store.mark_processed(
                    post.chat_id,
                    post.message_id,
                    original_text=post.text,
                    status=status,
                    filter_reason=analysis.reason,
                )
                logger.info(
                    "Filtered post %s/%s (%s): %s",
                    post.chat_id,
                    post.message_id,
                    status,
                    analysis.reason or "no reason provided",
                )
                return None
        rewritten = await self.rewriter.rewrite(
            post.text,
            post.image_paths,
            source.prompt_addition,
        )
        result = ProcessedPost(
            text=rewritten,
            source=post.source,
            chat_id=post.chat_id,
            message_id=post.message_id,
            image_paths=post.image_paths,
            attachment_paths=post.attachment_paths,
        )
        output_file = self.output_dir / f"{post.chat_id}_{post.message_id}.json"
        payload = asdict(result)
        payload["image_paths"] = [str(path) for path in result.image_paths]
        payload["attachment_paths"] = [str(path) for path in result.attachment_paths]
        output_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.store.mark_processed(
            post.chat_id,
            post.message_id,
            original_text=post.text,
            status="published",
        )
        return result


class TelegramParser:
    def __init__(
        self,
        config: AppConfig,
        parser: PostParser,
        client: TelegramClient | None = None,
    ) -> None:
        self.config = config
        self.parser = parser
        tg = config.telegram
        self.client = client or TelegramClient(tg.session, tg.api_id, tg.api_hash)
        self.poster = TelegramPoster(self.client, tg.destination)
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
                actual_total += (
                    path.stat().st_size
                    if path.exists()
                    else self._declared_file_size(message)
                )
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
            result = await self.parser.process(post, source)
            if result:
                await self.poster.publish(result)
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
            texts = [message.message for message in messages if message.message]
            post = IncomingPost(
                source=str(source.chat),
                chat_id=chat_id,
                message_id=int(first.id),
                text="\n".join(texts),
                image_paths=images,
                attachment_paths=attachments,
            )
            result = await self.parser.process(post, source)
            if result:
                await self.poster.publish(result)
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
