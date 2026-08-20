"""LLM-анализ постов на рекламу и смысловые дубликаты."""

from __future__ import annotations

import json
from pathlib import Path

from .config import LLMConfig
from .models import PostAnalysis
from .provider import LLMProvider

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


class PostAnalyzer:
    """Сравнивает новый пост с историей и классифицирует нежелательный контент."""

    def __init__(self, config: LLMConfig, provider: LLMProvider) -> None:
        """Сохраняет настройки генерации и общий транспорт LLM."""
        self.config = config
        self.provider = provider

    @staticmethod
    def _parse(content: str) -> PostAnalysis:
        """Преобразует JSON-ответ модели в типизированный результат анализа."""
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
        """Отправляет текущий пост и историю в LLM и возвращает решение фильтра."""
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
        response = await self.provider.complete(
            model=self.provider.model(image_paths),
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": self.provider.content(instruction, image_paths)},
            ],
            temperature=0,
            max_tokens=min(self.config.max_tokens, 500),
        )
        result = response.choices[0].message.content
        if not result or not result.strip():
            raise RuntimeError("LLM returned an empty analysis response")
        return self._parse(result)
