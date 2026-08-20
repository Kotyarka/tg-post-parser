from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .config import GigaChatConfig, LLMConfig
from .gigachat import GigaChatProvider, GigaChatTokenProvider


class LLMProvider:
    def __init__(
        self,
        config: LLMConfig,
        gigachat: GigaChatConfig | None = None,
        client: AsyncOpenAI | None = None,
        token_provider: GigaChatTokenProvider | None = None,
    ) -> None:
        self.config = config
        self.gigachat = gigachat or GigaChatConfig()
        self._owns_client = client is None
        self._gigachat_provider: GigaChatProvider | None = None
        if self.gigachat.enabled:
            self._gigachat_provider = GigaChatProvider(
                self.gigachat,
                client=client,
                token_provider=token_provider,
            )
            self.client = self._gigachat_provider.client
        else:
            self.client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)

    def model(self, image_paths: list[Path]) -> str:
        if self.gigachat.enabled:
            return self.gigachat.model
        return self.config.vision_model if image_paths and self.config.vision_model else self.config.model

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def content(
        self, instruction: str, image_paths: list[Path]
    ) -> str | list[dict[str, object]]:
        if not image_paths or not self.config.vision_model or self.gigachat.enabled:
            return instruction
        content: list[dict[str, object]] = [{"type": "text", "text": instruction}]
        content.extend(
            {"type": "image_url", "image_url": {"url": self._data_url(path)}}
            for path in image_paths
        )
        return content

    async def complete(self, **kwargs: Any) -> Any:
        if self._gigachat_provider:
            return await self._gigachat_provider.complete(**kwargs)
        return await self.client.chat.completions.create(**kwargs)

    async def close(self) -> None:
        if self._gigachat_provider:
            await self._gigachat_provider.close()
            return
        if self._owns_client:
            await self.client.close()
