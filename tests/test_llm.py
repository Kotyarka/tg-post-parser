from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_post_parser.config import LLMConfig
from tg_post_parser.llm import LLMRewriter


class FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="  Готовый пост  "))])


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


@pytest.mark.asyncio
async def test_rewriter_uses_source_prompt_and_text_model() -> None:
    client = FakeClient()
    rewriter = LLMRewriter(LLMConfig(api_key="x", model="text-model"), client=client)

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

    await LLMRewriter(config, client=client).rewrite("caption", [image])

    request = client.chat.completions.kwargs
    assert request["model"] == "vision"
    content = request["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

