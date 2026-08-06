import json
from pathlib import Path

import pytest

from tg_post_parser.config import SourceConfig
from tg_post_parser.models import IncomingPost
from tg_post_parser.service import PostProcessor
from tg_post_parser.storage import PostStore


class FakeRewriter:
    def __init__(self) -> None:
        self.calls = []

    async def rewrite(self, text, image_paths, prompt_addition):
        self.calls.append((text, image_paths, prompt_addition))
        return "Новая редакция"


@pytest.mark.asyncio
async def test_processor_persists_result_and_skips_duplicate(tmp_path: Path) -> None:
    rewriter = FakeRewriter()
    source = SourceConfig(chat="@source", prompt_addition="Тон: деловой")
    document = tmp_path / "report.pdf"
    post = IncomingPost(
        source="@source",
        chat_id=-1001,
        message_id=42,
        text="original",
        attachment_paths=[document],
    )
    with PostStore(tmp_path / "state.db") as store:
        processor = PostProcessor(rewriter, store, tmp_path / "output")
        result = await processor.process(post, source)
        duplicate = await processor.process(post, source)

    assert result is not None and result.text == "Новая редакция"
    assert duplicate is None
    assert len(rewriter.calls) == 1
    saved = json.loads((tmp_path / "output" / "-1001_42.json").read_text(encoding="utf-8"))
    assert saved["text"] == "Новая редакция"
    assert saved["attachment_paths"] == [str(document)]

