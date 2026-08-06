from pathlib import Path
from types import SimpleNamespace

import pytest

from tg_post_parser.models import ProcessedPost
from tg_post_parser.telegram import TelegramMonitor


class FakeMessage:
    id = 77
    media = object()
    photo = None
    file = SimpleNamespace(name="report.pdf", ext=".pdf", mime_type="application/pdf", size=100)

    def __init__(self) -> None:
        self.target = None
        self.download_calls = 0
        self.payload = b""

    async def download_media(self, file: str):
        self.download_calls += 1
        self.target = file
        if self.payload:
            Path(file).write_bytes(self.payload)
        return file


class FakeClient:
    def __init__(self) -> None:
        self.files = []
        self.messages = []

    async def send_file(self, destination, paths, caption=None):
        self.files.append((destination, paths, caption))

    async def send_message(self, destination, text):
        self.messages.append((destination, text))


def make_monitor(tmp_path: Path) -> TelegramMonitor:
    monitor = object.__new__(TelegramMonitor)
    monitor.config = SimpleNamespace(
        storage=SimpleNamespace(output_dir=tmp_path, max_post_download_mb=100),
        telegram=SimpleNamespace(destination="@destination"),
    )
    monitor.client = FakeClient()
    return monitor


@pytest.mark.asyncio
async def test_downloads_document_but_does_not_send_it_to_vision(tmp_path: Path) -> None:
    monitor = make_monitor(tmp_path)
    message = FakeMessage()

    attachments, images = await monitor._download_attachment(message, -1001)

    assert attachments == [tmp_path / "media" / "-1001" / "77_report.pdf"]
    assert images == []
    assert message.target.endswith("77_report.pdf")


@pytest.mark.asyncio
async def test_aggregate_limit_skips_post_before_downloading(tmp_path: Path) -> None:
    monitor = make_monitor(tmp_path)
    monitor.config.storage.max_post_download_mb = 10
    first = FakeMessage()
    second = FakeMessage()
    first.file = SimpleNamespace(name="a.zip", ext=".zip", mime_type="application/zip", size=6 * 1024 * 1024)
    second.file = SimpleNamespace(name="b.zip", ext=".zip", mime_type="application/zip", size=6 * 1024 * 1024)

    result = await monitor._collect_attachments([first, second], -1001)

    assert result is None
    assert first.download_calls == 0
    assert second.download_calls == 0


@pytest.mark.asyncio
async def test_actual_size_limit_removes_download_and_skips_post(tmp_path: Path) -> None:
    monitor = make_monitor(tmp_path)
    monitor.config.storage.max_post_download_mb = 0.000001
    message = FakeMessage()
    message.file = SimpleNamespace(name="unknown.bin", ext=".bin", mime_type="application/octet-stream", size=0)
    message.payload = b"larger than one byte"

    result = await monitor._collect_attachments([message], -1001)

    assert result is None
    assert message.download_calls == 1
    assert not Path(message.target).exists()


@pytest.mark.asyncio
async def test_publish_sends_all_attachments_and_long_text_separately(tmp_path: Path) -> None:
    monitor = make_monitor(tmp_path)
    paths = [tmp_path / "video.mp4", tmp_path / "document.docx"]
    post = ProcessedPost(
        text="x" * 1500,
        source="@source",
        chat_id=-1001,
        message_id=1,
        attachment_paths=paths,
    )

    await monitor._publish(post)

    assert monitor.client.files == [("@destination", paths, None)]
    assert "".join(text for _, text in monitor.client.messages) == post.text
