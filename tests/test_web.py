from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from tg_post_parser.web import ConfigRepository, SECRET_MASK, create_app


def write_config(path: Path) -> None:
    path.write_text(
        """
telegram:
  api_id: 123
  api_hash: telegram-secret
  session: test
llm:
  api_key: llm-secret
  base_url: https://example.test/v1
  model: model
gigachat:
  authorization_key: gigachat-secret
storage:
  database: state.db
  output_dir: output
  max_post_download_mb: 50
sources:
  - chat: '@source'
    enabled: true
    prompt_addition: test
""",
        encoding="utf-8",
    )


def test_repository_masks_secrets(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    write_config(path)

    public = ConfigRepository(path).public()

    assert public["telegram"]["api_hash"] == SECRET_MASK
    assert public["llm"]["api_key"] == SECRET_MASK
    assert public["gigachat"]["authorization_key"] == SECRET_MASK
    assert public["analysis"] == {"enabled": True, "history_hours": 24}


def test_repository_preserves_masked_secrets_on_save(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    write_config(path)
    repository = ConfigRepository(path)
    payload = repository.public()
    payload["llm"]["model"] = "new-model"
    payload["analysis"] = {"enabled": False, "history_hours": 48}

    repository.save(payload)

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["telegram"]["api_hash"] == "telegram-secret"
    assert saved["llm"]["api_key"] == "llm-secret"
    assert saved["gigachat"]["authorization_key"] == "gigachat-secret"
    assert saved["llm"]["model"] == "new-model"
    assert saved["analysis"] == {"enabled": False, "history_hours": 48}


def test_web_api_and_terminal_websocket(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    write_config(path)
    static = Path(__file__).resolve().parents[1] / "web"
    app = create_app(path, static_path=static)

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/config").json()["llm"]["api_key"] == SECRET_MASK
        assert client.get("/api/bot/status").json()["running"] is False

        app.state.manager._broadcast("test terminal line")
        with client.websocket_connect("/api/logs") as websocket:
            assert websocket.receive_text() == "test terminal line"
