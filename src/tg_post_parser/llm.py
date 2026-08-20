"""Совместимый монолитный LLM-клиент с анализом, рерайтом и GigaChat OAuth."""

from __future__ import annotations

import base64
import asyncio
import json
import mimetypes
import ssl
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI, AuthenticationError

from .config import GigaChatConfig, LLMConfig
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


class GigaChatTokenProvider:
    """Получает и кэширует краткоживущий OAuth-токен GigaChat."""

    def __init__(
        self,
        config: GigaChatConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Создаёт HTTP-клиент, блокировку и пустой кэш токена."""
        self.config = config
        self._client = http_client or httpx.AsyncClient(verify=_ssl_verification(config))
        self._owns_client = http_client is None
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Принудительно помечает текущий токен недействительным."""
        self._expires_at = 0.0

    async def get_token(self) -> str:
        """Возвращает кэшированный токен либо получает новый через OAuth."""
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._expires_at - 60:
                return self._token
            authorization_key = self.config.authorization_key.strip()
            if authorization_key.lower().startswith("basic "):
                authorization_key = authorization_key[6:].strip()
            if not authorization_key:
                raise RuntimeError("Не задан ключ авторизации GigaChat")
            response = await self._client.post(
                self.config.oauth_url,
                data={"scope": self.config.scope},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "RqUID": str(uuid.uuid4()),
                    "Authorization": f"Basic {authorization_key}",
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                details = response.text.strip()[:500]
                raise RuntimeError(
                    f"GigaChat OAuth вернул HTTP {response.status_code}: {details}"
                ) from exc
            payload = response.json()
            token = payload.get("access_token") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token.strip():
                raise RuntimeError("GigaChat OAuth не вернул access_token")
            expires_at = payload.get("expires_at", time.time() + 29 * 60)
            try:
                expires_at = float(expires_at)
            except (TypeError, ValueError):
                expires_at = time.time() + 29 * 60
            if expires_at > 100_000_000_000:
                expires_at /= 1000
            if expires_at <= time.time():
                expires_at = time.time() + 29 * 60
            self._token = token.strip()
            self._expires_at = expires_at
            return self._token

    async def close(self) -> None:
        """Закрывает принадлежащий объекту HTTP-клиент."""
        if self._owns_client:
            await self._client.aclose()


def _ssl_verification(config: GigaChatConfig) -> bool | ssl.SSLContext:
    """Создаёт настройку TLS с системным или пользовательским набором CA."""
    if not config.verify_ssl:
        return False
    if config.ca_bundle_file:
        return ssl.create_default_context(cafile=str(config.ca_bundle_file))
    return True


class LLMRewriter:
    """Совместимый клиент, объединяющий предварительный анализ и рерайт."""

    def __init__(
        self,
        config: LLMConfig,
        gigachat: GigaChatConfig | None = None,
        client: AsyncOpenAI | None = None,
        token_provider: GigaChatTokenProvider | None = None,
    ) -> None:
        """Выбирает обычный OpenAI-клиент либо клиент с авторизацией GigaChat."""
        self.config = config
        self.gigachat = gigachat or GigaChatConfig()
        self._owns_client = client is None
        self._token_provider = token_provider
        if self.gigachat.enabled:
            self._token_provider = token_provider or GigaChatTokenProvider(self.gigachat)
            self.client = client or AsyncOpenAI(
                api_key="pending-gigachat-token",
                base_url=self.gigachat.base_url,
                http_client=httpx.AsyncClient(verify=_ssl_verification(self.gigachat)),
            )
        else:
            self.client = client or AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)

    def _model(self, image_paths: list[Path]) -> str:
        """Определяет модель для текста или изображений текущего запроса."""
        if self.gigachat.enabled:
            return self.gigachat.model
        return self.config.vision_model if image_paths and self.config.vision_model else self.config.model

    async def _create_completion(self, **kwargs: Any) -> Any:
        """Выполняет chat completion и обновляет GigaChat-токен после 401."""
        if not self._token_provider:
            return await self.client.chat.completions.create(**kwargs)
        self.client.api_key = await self._token_provider.get_token()
        try:
            return await self.client.chat.completions.create(**kwargs)
        except AuthenticationError:
            self._token_provider.invalidate()
            self.client.api_key = await self._token_provider.get_token()
            return await self.client.chat.completions.create(**kwargs)

    async def close(self) -> None:
        """Освобождает созданные LLM- и OAuth-клиенты."""
        if self._owns_client:
            await self.client.close()
        if self._token_provider:
            await self._token_provider.close()

    @staticmethod
    def _data_url(path: Path) -> str:
        """Преобразует локальное изображение в data URL."""
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _request_content(
        self, instruction: str, image_paths: list[Path]
    ) -> str | list[dict[str, object]]:
        """Собирает текстовое или мультимодальное содержимое запроса."""
        if not image_paths or not self.config.vision_model or self.gigachat.enabled:
            return instruction
        content: list[dict[str, object]] = [{"type": "text", "text": instruction}]
        content.extend(
            {"type": "image_url", "image_url": {"url": self._data_url(path)}}
            for path in image_paths
        )
        return content

    @staticmethod
    def _parse_analysis(content: str) -> PostAnalysis:
        """Проверяет JSON-ответ анализатора и создаёт результат фильтрации."""
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
        """Сравнивает пост с историей и определяет дубликаты и рекламу."""
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
        response = await self._create_completion(
            model=self._model(image_paths),
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
        """Переформулирует допущенный пост с учётом инструкций источника."""
        instruction = "Исходный текст:\n" + (text.strip() or "[текст отсутствует]")
        if prompt_addition.strip():
            instruction += f"\n\nДополнительные требования для этого источника:\n{prompt_addition.strip()}"

        content = self._request_content(instruction, image_paths)

        response = await self._create_completion(
            model=self._model(image_paths),
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
