"""Клиент GigaChat с OAuth-обновлением токена и настройкой TLS."""

from __future__ import annotations

import asyncio
import ssl
import time
import uuid
from typing import Any

import httpx
from openai import AsyncOpenAI, AuthenticationError

from .config import GigaChatConfig


def ssl_verification(config: GigaChatConfig) -> bool | ssl.SSLContext:
    """Возвращает режим TLS-проверки или контекст с пользовательским CA bundle."""
    if not config.verify_ssl:
        return False
    if config.ca_bundle_file:
        return ssl.create_default_context(cafile=str(config.ca_bundle_file))
    return True


class GigaChatTokenProvider:
    """Получает, кэширует и заблаговременно обновляет OAuth-токен GigaChat."""

    def __init__(
        self,
        config: GigaChatConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Создаёт OAuth-клиент и пустой потокобезопасный кэш токена."""
        self.config = config
        self._client = http_client or httpx.AsyncClient(verify=ssl_verification(config))
        self._owns_client = http_client is None
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Помечает текущий токен просроченным для принудительного обновления."""
        self._expires_at = 0.0

    async def get_token(self) -> str:
        """Возвращает действующий токен либо получает новый через OAuth."""
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
        """Закрывает внутренний HTTP-клиент, если класс создал его сам."""
        if self._owns_client:
            await self._client.aclose()


class GigaChatProvider:
    """Выполняет OpenAI-совместимые запросы с актуальным токеном GigaChat."""

    def __init__(
        self,
        config: GigaChatConfig,
        client: AsyncOpenAI | None = None,
        token_provider: GigaChatTokenProvider | None = None,
    ) -> None:
        """Создаёт API-клиент и подключает менеджер OAuth-токена."""
        self.config = config
        self._owns_client = client is None
        self.token_provider = token_provider or GigaChatTokenProvider(config)
        self.client = client or AsyncOpenAI(
            api_key="pending-gigachat-token",
            base_url=config.base_url,
            http_client=httpx.AsyncClient(verify=ssl_verification(config)),
        )

    async def complete(self, **kwargs: Any) -> Any:
        """Выполняет запрос и один раз повторяет его с новым токеном после 401."""
        self.client.api_key = await self.token_provider.get_token()
        try:
            return await self.client.chat.completions.create(**kwargs)
        except AuthenticationError:
            self.token_provider.invalidate()
            self.client.api_key = await self.token_provider.get_token()
            return await self.client.chat.completions.create(**kwargs)

    async def close(self) -> None:
        """Закрывает API-клиент и менеджер токена."""
        if self._owns_client:
            await self.client.close()
        await self.token_provider.close()
