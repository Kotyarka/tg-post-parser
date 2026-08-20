"""Tests for the post-processing pipeline in parser.py."""

import json
from pathlib import Path

import pytest

from tg_post_parser.config import SourceConfig
from tg_post_parser.models import IncomingPost, PostAnalysis
from tg_post_parser.parser import PostParser
from tg_post_parser.storage import PostStore


class FakeRewriter:
    def __init__(self) -> None:
        self.calls = []

    async def rewrite(self, text, image_paths, prompt_addition):
        self.calls.append((text, image_paths, prompt_addition))
        return "Новая редакция"

    async def analyze(self, text, image_paths, history):
        self.analysis_call = (text, image_paths, history)
        return PostAnalysis(False, False, "Полезный новый пост")


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
        processor = PostParser(rewriter, rewriter, store, tmp_path / "output")
        result = await processor.process(post, source)
        duplicate = await processor.process(post, source)

    assert result is not None and result.text == "Новая редакция"
    assert duplicate is None
    assert len(rewriter.calls) == 1
    saved = json.loads((tmp_path / "output" / "-1001_42.json").read_text(encoding="utf-8"))
    assert saved["text"] == "Новая редакция"
    assert saved["attachment_paths"] == [str(document)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("analysis", "expected_status"),
    [
        (PostAnalysis(True, False, "Смысловой дубль"), "filtered_duplicate"),
        (PostAnalysis(False, True, "Есть рекламная ссылка"), "filtered_advertisement"),
    ],
)
async def test_processor_filters_before_rewrite(tmp_path: Path, analysis, expected_status) -> None:
    rewriter = FakeRewriter()

    async def analyze(text, image_paths, history):
        return analysis

    rewriter.analyze = analyze
    post = IncomingPost(source="@source", chat_id=1, message_id=2, text="original")
    with PostStore(tmp_path / "state.db") as store:
        processor = PostParser(rewriter, rewriter, store, tmp_path / "output")
        result = await processor.process(post, SourceConfig(chat="@source"))
        row = store._connection.execute(
            "SELECT status, filter_reason FROM processed_posts WHERE chat_id = 1 AND message_id = 2"
        ).fetchone()

    assert result is None
    assert rewriter.calls == []
    assert row == (expected_status, analysis.reason)
