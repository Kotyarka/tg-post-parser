from pathlib import Path

import pytest

from tg_post_parser.config import load_config


def test_load_config_resolves_storage_paths_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "secret")
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
telegram:
  api_id: 1
  api_hash: hash
llm:
  api_key: ${TEST_LLM_KEY}
storage:
  max_post_download_mb: 25
sources:
  - chat: '@news'
    prompt_addition: concise
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.llm.api_key == "secret"
    assert config.storage.database == tmp_path / "state.db"
    assert config.storage.max_post_download_mb == 25
    assert config.sources[0].prompt_addition == "concise"


def test_config_requires_enabled_source(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
telegram: {api_id: 1, api_hash: hash}
llm: {api_key: key}
sources: [{chat: '@news', enabled: false}]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one source"):
        load_config(config_file)
