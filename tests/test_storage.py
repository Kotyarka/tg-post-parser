from pathlib import Path

from tg_post_parser.storage import PostStore


def test_store_marks_post_only_by_chat_and_message(tmp_path: Path) -> None:
    with PostStore(tmp_path / "state.db") as store:
        assert not store.contains(10, 20)
        store.mark_processed(10, 20)
        assert store.contains(10, 20)
        assert not store.contains(11, 20)

