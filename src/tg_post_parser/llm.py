from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from openai import AsyncOpenAI

from .config import LLMConfig

SYSTEM_PROMPT = """Ты — редактор Telegram-канала. Обработай входной пост:
1. Сохрани факты, имена, числа, смысл и полезные детали. Ничего не выдумывай.
2. Удали подпись автора, призывы подписаться, ссылки и упоминания исходного канала.
3. Если формулировки слишком похожи на оригинал, естественно перефразируй их.
4. Не сообщай, что текст переписан, и не добавляй комментарии от себя.
5. Верни только готовый текст поста. Сохрани уместную структуру и язык оригинала.
Если на изображениях есть важная информация, учти ее в тексте, но не додумывай
неразборчивые детали."""


class LLMRewriter:
    def __init__(self, config: LLMConfig, client: AsyncOpenAI | None = None) -> None:
        self.config = config
        self.client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    async def rewrite(
        self, text: str, image_paths: list[Path], prompt_addition: str = ""
    ) -> str:
        model = self.config.vision_model if image_paths and self.config.vision_model else self.config.model
        instruction = "Исходный текст:\n" + (text.strip() or "[текст отсутствует]")
        if prompt_addition.strip():
            instruction += f"\n\nДополнительные требования для этого источника:\n{prompt_addition.strip()}"

        if image_paths and self.config.vision_model:
            content: str | list[dict[str, object]] = [{"type": "text", "text": instruction}]
            content.extend(
                {"type": "image_url", "image_url": {"url": self._data_url(path)}}
                for path in image_paths
            )
        else:
            content = instruction

        response = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        result = response.choices[0].message.content
        if not result or not result.strip():
            raise RuntimeError("LLM returned an empty response")
        return result.strip()

