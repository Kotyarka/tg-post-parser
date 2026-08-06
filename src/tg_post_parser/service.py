from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from .config import SourceConfig
from .llm import LLMRewriter
from .models import IncomingPost, ProcessedPost
from .storage import PostStore

logger = logging.getLogger(__name__)


class PostProcessor:
    def __init__(self, rewriter: LLMRewriter, store: PostStore, output_dir: Path) -> None:
        self.rewriter = rewriter
        self.store = store
        self.output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

    async def process(self, post: IncomingPost, source: SourceConfig) -> ProcessedPost | None:
        if self.store.contains(post.chat_id, post.message_id):
            logger.debug("Skipping already processed post %s/%s", post.chat_id, post.message_id)
            return None
        rewritten = await self.rewriter.rewrite(post.text, post.image_paths, source.prompt_addition)
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
        output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.mark_processed(post.chat_id, post.message_id)
        return result
