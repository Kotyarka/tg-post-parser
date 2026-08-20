from __future__ import annotations

from pathlib import Path

from .config import LLMConfig
from .provider import LLMProvider

SYSTEM_PROMPT = """Ты — редактор Telegram-канала. Обработай входной пост:
1. Сохрани факты, имена, числа, смысл и полезные детали. Ничего не выдумывай.
2. Удали подпись автора, призывы подписаться, ссылки и упоминания исходного канала.
3. Если формулировки слишком похожи на оригинал, естественно перефразируй их.
4. Не сообщай, что текст переписан, и не добавляй комментарии от себя.
5. Верни только готовый текст поста. Сохрани уместную структуру и язык оригинала.
Если на изображениях есть важная информация, учти ее в тексте, но не додумывай
неразборчивые детали."""


class PostRewriter:
    def __init__(self, config: LLMConfig, provider: LLMProvider) -> None:
        self.config = config
        self.provider = provider

    async def rewrite(
        self, text: str, image_paths: list[Path], prompt_addition: str = ""
    ) -> str:
        instruction = "Исходный текст:\n" + (text.strip() or "[текст отсутствует]")
        if prompt_addition.strip():
            instruction += (
                "\n\nДополнительные требования для этого источника:\n"
                + prompt_addition.strip()
            )
        response = await self.provider.complete(
            model=self.provider.model(image_paths),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.provider.content(instruction, image_paths)},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        result = response.choices[0].message.content
        if not result or not result.strip():
            raise RuntimeError("LLM returned an empty response")
        return result.strip()
