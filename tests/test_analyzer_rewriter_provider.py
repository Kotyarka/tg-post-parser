"""Tests for the analyzer, rewriter and their shared LLM provider."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError

from tg_post_parser.analyzer import PostAnalyzer
from tg_post_parser.config import GigaChatConfig, LLMConfig
from tg_post_parser.gigachat import GigaChatTokenProvider
from tg_post_parser.provider import LLMProvider
from tg_post_parser.rewriter import PostRewriter


class FakeCompletions:
    def __init__(self, content="  Готовый пост  ") -> None:
        self.kwargs = None
        self.content = content

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class FakeClient:
    def __init__(self, content="  Готовый пост  ") -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(content))


@pytest.mark.asyncio
async def test_rewriter_uses_source_prompt_and_text_model() -> None:
    client = FakeClient()
    config = LLMConfig(api_key="x", model="text-model")
    rewriter = PostRewriter(config, LLMProvider(config, client=client))

    result = await rewriter.rewrite("Исходник", [], "Пиши кратко")

    assert result == "Готовый пост"
    request = client.chat.completions.kwargs
    assert request["model"] == "text-model"
    assert "Пиши кратко" in request["messages"][1]["content"]


@pytest.mark.asyncio
async def test_rewriter_sends_image_to_vision_model(tmp_path: Path) -> None:
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"fake-image")
    client = FakeClient()
    config = LLMConfig(api_key="x", model="text", vision_model="vision")

    await PostRewriter(config, LLMProvider(config, client=client)).rewrite("caption", [image])

    request = client.chat.completions.kwargs
    assert request["model"] == "vision"
    content = request["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_analyzer_sends_history_and_parses_json() -> None:
    client = FakeClient(
        '```json\n{"is_duplicate": true, "is_advertisement": false, '
        '"reason": "Тот же материал"}\n```'
    )
    config = LLMConfig(api_key="x", model="text-model")
    analyzer = PostAnalyzer(config, LLMProvider(config, client=client))

    result = await analyzer.analyze("Новый текст", [], ["Старый текст"])

    assert result.is_duplicate is True
    assert result.is_advertisement is False
    assert result.reason == "Тот же материал"
    request = client.chat.completions.kwargs
    assert request["temperature"] == 0
    assert "Старый текст" in request["messages"][1]["content"]


@pytest.mark.asyncio
async def test_gigachat_token_is_cached_and_refreshed() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(requests)}", "expires_at": 4_102_444_800_000},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = GigaChatTokenProvider(
            GigaChatConfig(enabled=True, authorization_key="Basic auth-key"),
            http_client=http_client,
        )

        assert await provider.get_token() == "token-1"
        assert await provider.get_token() == "token-1"
        provider.invalidate()
        assert await provider.get_token() == "token-2"

    assert len(requests) == 2
    assert requests[0].headers["Authorization"] == "Basic auth-key"
    assert requests[0].headers["RqUID"]
    assert requests[0].content == b"scope=GIGACHAT_API_PERS"


@pytest.mark.asyncio
async def test_rewriter_uses_gigachat_token_and_model() -> None:
    class FakeTokenProvider:
        async def get_token(self):
            return "fresh-token"

        def invalidate(self):
            pass

        async def close(self):
            pass

    client = FakeClient()
    config = LLMConfig(api_key="unused", model="other-model")
    provider = LLMProvider(
        config,
        GigaChatConfig(enabled=True, authorization_key="auth-key", model="GigaChat-Pro"),
        client=client,
        token_provider=FakeTokenProvider(),
    )
    rewriter = PostRewriter(config, provider)

    await rewriter.rewrite("Текст", [], "")

    assert client.api_key == "fresh-token"
    assert client.chat.completions.kwargs["model"] == "GigaChat-Pro"


@pytest.mark.asyncio
async def test_rewriter_refreshes_gigachat_token_after_401() -> None:
    class RetryCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                response = httpx.Response(
                    401,
                    request=httpx.Request("POST", "https://api.giga.chat/v1/chat/completions"),
                )
                raise AuthenticationError("expired", response=response, body=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Готово"))]
            )

    class RotatingTokenProvider:
        def __init__(self):
            self.calls = 0
            self.invalidated = False

        async def get_token(self):
            self.calls += 1
            return f"token-{self.calls}"

        def invalidate(self):
            self.invalidated = True

        async def close(self):
            pass

    client = SimpleNamespace(chat=SimpleNamespace(completions=RetryCompletions()))
    token_provider = RotatingTokenProvider()
    config = LLMConfig()
    provider = LLMProvider(
        config,
        GigaChatConfig(enabled=True, authorization_key="auth-key"),
        client=client,
        token_provider=token_provider,
    )
    rewriter = PostRewriter(config, provider)

    result = await rewriter.rewrite("Текст", [], "")

    assert result == "Готово"
    assert client.api_key == "token-2"
    assert token_provider.calls == 2
    assert token_provider.invalidated is True
