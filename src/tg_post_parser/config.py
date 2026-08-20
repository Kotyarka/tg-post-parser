from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class TelegramConfig(BaseModel):
    api_id: int
    api_hash: str
    session: str = "tg_monitor"
    destination: str | int | None = None


class LLMConfig(BaseModel):
    api_key: str = ""
    base_url: str | None = None
    model: str = "deepseek-chat"
    vision_model: str | None = None
    temperature: float = Field(default=0.3, ge=0, le=2)
    max_tokens: int = Field(default=1800, gt=0)


class GigaChatConfig(BaseModel):
    enabled: bool = False
    authorization_key: str = ""
    scope: Literal["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"] = (
        "GIGACHAT_API_PERS"
    )
    model: str = "GigaChat"
    base_url: str = "https://api.giga.chat/v1"
    oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    verify_ssl: bool = True
    ca_bundle_file: Path | None = None

    @model_validator(mode="after")
    def authorization_key_is_required_when_enabled(self) -> "GigaChatConfig":
        if self.enabled and not self.authorization_key.strip():
            raise ValueError("authorization_key is required when GigaChat is enabled")
        return self


class StorageConfig(BaseModel):
    database: Path = Path("state.db")
    output_dir: Path = Path("output")
    max_post_download_mb: float = Field(default=100, gt=0)


class AnalysisConfig(BaseModel):
    enabled: bool = True
    history_hours: int = Field(default=24, gt=0)


class SourceConfig(BaseModel):
    chat: str | int
    enabled: bool = True
    prompt_addition: str = ""

    @field_validator("chat")
    @classmethod
    def chat_is_not_blank(cls, value: str | int) -> str | int:
        if isinstance(value, str) and not value.strip():
            raise ValueError("source chat cannot be blank")
        return value


class AppConfig(BaseModel):
    telegram: TelegramConfig
    llm: LLMConfig
    gigachat: GigaChatConfig = GigaChatConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    storage: StorageConfig = StorageConfig()
    sources: list[SourceConfig]

    @field_validator("sources")
    @classmethod
    def at_least_one_source(cls, value: list[SourceConfig]) -> list[SourceConfig]:
        if not any(source.enabled for source in value):
            raise ValueError("at least one source must be enabled")
        return value


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return os.path.expandvars(value)
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")
    try:
        config = AppConfig.model_validate(_expand_env(raw))
    except ValidationError as exc:
        raise ValueError(f"Invalid config: {exc}") from exc
    base = config_path.resolve().parent
    if not config.storage.database.is_absolute():
        config.storage.database = base / config.storage.database
    if not config.storage.output_dir.is_absolute():
        config.storage.output_dir = base / config.storage.output_dir
    if config.gigachat.ca_bundle_file and not config.gigachat.ca_bundle_file.is_absolute():
        config.gigachat.ca_bundle_file = base / config.gigachat.ca_bundle_file
    return config
