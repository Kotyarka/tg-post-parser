from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from openai import AsyncOpenAI

from .config import LLMConfig
from .models import PostAnalysis

SYSTEM_PROMPT = """Ты — редактор Telegram-канала. Обработай входной пост:
1. Сохрани факты, имена, числа, смысл и полезные детали. Ничего не выдумывай.
2. Удали подпись автора, призывы подписаться, ссылки и упоминания исходного канала.
3. Если формулировки слишком похожи на оригинал, естественно перефразируй их.
4. Не сообщай, что текст переписан, и не добавляй комментарии от себя.
5. Верни только готовый текст поста. Сохрани уместную структуру и язык оригинала.
Если на изображениях есть важная информация, учти ее в тексте, но не додумывай
неразборчивые детали."""

ANALYSIS_PROMPT = """Ты — фильтр входящих постов Telegram-канала.
Определи, нужно ли отклонить текущий пост по одной из двух причин.

1. Дубликат: в истории уже есть пост с тем же содержанием и теми же основными
фактами, даже если он сформулирован другими словами. Не считай дубликатом
продолжение, обновление или дополнение, в котором появились существенные новые
факты, цифры, события или развитие истории.
2. Реклама: пост продвигает товар, услугу, компанию, канал, мероприятие или другой
объект. Признаки: ссылка на рекламный материал или предложение, явная маркировка
рекламы, призыв купить/заказать/зарегистрироваться, либо частое и конкретное
упоминание продвигаемого объекта. Обычное нейтральное упоминание объекта в новости
само по себе рекламой не является.

Верни только JSON без Markdown:
{"is_duplicate": false, "is_advertisement": false, "reason": "краткое объяснение"}
Поля is_duplicate и is_advertisement должны быть логическими значениями."""


class LLMRewriter:
    def __init__(self, config: LLMConfig, client: AsyncOpenAI | None = None) -> None:
        self.config = config
        self.client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _request_content(
        self, instruction: str, image_paths: list[Path]
    ) -> str | list[dict[str, object]]:
        if not image_paths or not self.config.vision_model:
            return instruction
        content: list[dict[str, object]] = [{"type": "text", "text": instruction}]
        content.extend(
            {"type": "image_url", "image_url": {"url": self._data_url(path)}}
            for path in image_paths
        )
        return content

    @staticmethod
    def _parse_analysis(content: str) -> PostAnalysis:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError("LLM returned invalid analysis JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("LLM analysis response must be a JSON object")
        duplicate = payload.get("is_duplicate")
        advertisement = payload.get("is_advertisement")
        reason = payload.get("reason", "")
        if not isinstance(duplicate, bool) or not isinstance(advertisement, bool):
            raise RuntimeError("LLM analysis flags must be booleans")
        if not isinstance(reason, str):
            raise RuntimeError("LLM analysis reason must be a string")
        return PostAnalysis(duplicate, advertisement, reason.strip())

    async def analyze(
        self, text: str, image_paths: list[Path], history: list[str]
    ) -> PostAnalysis:
        history_payload = [
            {"number": index, "text": historical_text}
            for index, historical_text in enumerate(history, start=1)
        ]
        instruction = (
            "Текущий пост:\n"
            + (text.strip() or "[текст отсутствует; оцени изображения]")
            + "\n\nИстория ранее опубликованных постов:\n"
            + json.dumps(history_payload, ensure_ascii=False)
        )
        model = self.config.vision_model if image_paths and self.config.vision_model else self.config.model
        response = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": self._request_content(instruction, image_paths)},
            ],
            temperature=0,
            max_tokens=min(self.config.max_tokens, 500),
        )
        result = response.choices[0].message.content
        if not result or not result.strip():
            raise RuntimeError("LLM returned an empty analysis response")
        return self._parse_analysis(result)

    async def rewrite(
        self, text: str, image_paths: list[Path], prompt_addition: str = ""
    ) -> str:
        model = self.config.vision_model if image_paths and self.config.vision_model else self.config.model
        instruction = "Исходный текст:\n" + (text.strip() or "[текст отсутствует]")
        if prompt_addition.strip():
            instruction += f"\n\nДополнительные требования для этого источника:\n{prompt_addition.strip()}"

        content = self._request_content(instruction, image_paths)

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

