"""FastAPI-сервер, управление процессом бота и хранение настроек веб-панели."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Timer
from typing import Any, AsyncIterator

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from ..config import AppConfig

SECRET_MASK = "••••••••"
SECRET_PATHS = (
    ("telegram", "api_hash"),
    ("llm", "api_key"),
    ("gigachat", "authorization_key"),
)


class ConfigRepository:
    """Читает, маскирует, валидирует и атомарно сохраняет YAML-конфигурацию."""

    def __init__(self, path: Path) -> None:
        """Запоминает абсолютный путь к файлу конфигурации."""
        self.path = path.resolve()

    def _read(self) -> dict[str, Any]:
        """Читает YAML как словарь либо возвращает пустую конфигурацию."""
        if not self.path.exists():
            return {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("Корень config.yml должен быть объектом")
        return raw

    @staticmethod
    def _defaults() -> dict[str, Any]:
        """Возвращает полную конфигурацию по умолчанию для веб-формы."""
        return {
            "telegram": {"api_id": 0, "api_hash": "", "session": "tg_monitor", "destination": None},
            "llm": {
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "vision_model": None,
                "temperature": 0.3,
                "max_tokens": 1800,
            },
            "gigachat": {
                "enabled": False,
                "authorization_key": "",
                "scope": "GIGACHAT_API_PERS",
                "model": "GigaChat",
                "base_url": "https://api.giga.chat/v1",
                "oauth_url": "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                "verify_ssl": True,
                "ca_bundle_file": None,
            },
            "analysis": {
                "enabled": True,
                "history_hours": 24,
            },
            "storage": {
                "database": "state.db",
                "output_dir": "output",
                "max_post_download_mb": 100,
            },
            "sources": [],
        }

    @staticmethod
    def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        """Рекурсивно объединяет входящие настройки с базовым словарём."""
        result = dict(base)
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ConfigRepository._merge(result[key], value)
            else:
                result[key] = value
        return result

    def public(self) -> dict[str, Any]:
        """Возвращает настройки для браузера с замаскированными секретами."""
        merged = self._merge(self._defaults(), self._read())
        for section, field in SECRET_PATHS:
            if merged.get(section, {}).get(field):
                merged[section][field] = SECRET_MASK
        return merged

    def save(self, incoming: dict[str, Any]) -> dict[str, Any]:
        """Валидирует и атомарно сохраняет настройки, не затирая скрытые секреты."""
        current = self._merge(self._defaults(), self._read())
        merged = self._merge(current, incoming)
        for section, field in SECRET_PATHS:
            submitted = incoming.get(section, {}).get(field)
            if submitted in (None, "", SECRET_MASK) and current.get(section, {}).get(field):
                merged[section][field] = current[section][field]
        try:
            validated = AppConfig.model_validate(merged)
        except ValidationError as exc:
            messages = [" → ".join(str(part) for part in error["loc"]) + ": " + error["msg"] for error in exc.errors()]
            raise ValueError("; ".join(messages)) from exc
        serializable = validated.model_dump(mode="json")
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            yaml.safe_dump(serializable, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        return self.public()


class BotProcessManager:
    """Запускает монитор отдельным процессом и транслирует его журнал браузеру."""

    def __init__(self, config_path: Path, working_directory: Path) -> None:
        """Настраивает пути, состояние процесса, журнал и подписчиков."""
        self.config_path = config_path.resolve()
        self.working_directory = working_directory.resolve()
        self.process: asyncio.subprocess.Process | None = None
        self.logs: deque[str] = deque(maxlen=2000)
        self.subscribers: set[asyncio.Queue[str]] = set()
        self._reader_task: asyncio.Task[None] | None = None
        self._waiter_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """Показывает, существует ли активный процесс мониторинга."""
        return self.process is not None and self.process.returncode is None

    def status(self) -> dict[str, Any]:
        """Возвращает сериализуемое состояние процесса для API."""
        return {
            "running": self.running,
            "pid": self.process.pid if self.running and self.process else None,
            "exit_code": self.process.returncode if self.process else None,
        }

    def _broadcast(self, line: str) -> None:
        """Добавляет строку в журнал и отправляет её всем подписчикам."""
        self.logs.append(line)
        for queue in tuple(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(line)

    async def start(self) -> dict[str, Any]:
        """Запускает CLI-монитор в дочернем процессе."""
        if self.running:
            raise RuntimeError("Мониторинг уже запущен")
        if not self.config_path.exists():
            raise RuntimeError(f"Файл настроек не найден: {self.config_path}")
        self.logs.clear()
        self._broadcast("[web] Запуск Telegram-монитора…")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "tg_post_parser.cli",
            "--config",
            str(self.config_path),
            cwd=str(self.working_directory),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=creation_flags,
        )
        self._reader_task = asyncio.create_task(self._read_output())
        self._waiter_task = asyncio.create_task(self._wait_for_exit())
        return self.status()

    async def _read_output(self) -> None:
        """Читает объединённый вывод процесса и транслирует строки."""
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            self._broadcast(line.decode("utf-8", errors="replace").rstrip())

    async def _wait_for_exit(self) -> None:
        """Ожидает завершения процесса и фиксирует его код возврата."""
        assert self.process
        code = await self.process.wait()
        if self._reader_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._broadcast(f"[web] Процесс завершён с кодом {code}")

    async def stop(self) -> dict[str, Any]:
        """Мягко останавливает монитор и принудительно завершает его по тайм-ауту."""
        if not self.running or not self.process:
            return self.status()
        self._broadcast("[web] Остановка мониторинга…")
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=8)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
        return self.status()

    async def send_input(self, value: str) -> None:
        """Передаёт строку в stdin монитора для авторизации Telegram."""
        if not self.running or not self.process or not self.process.stdin:
            raise RuntimeError("Мониторинг не запущен")
        self.process.stdin.write((value + "\n").encode("utf-8"))
        await self.process.stdin.drain()

    async def subscribe(self) -> AsyncIterator[str]:
        """Выдаёт историю журнала и последующие строки конкретному подписчику."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self.subscribers.add(queue)
        try:
            for line in self.logs:
                yield line
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)


def create_app(config_path: Path | str = "config.yml", static_path: Path | None = None) -> FastAPI:
    """Создаёт FastAPI-приложение, REST API, WebSocket и раздачу фронтенда."""
    project_root = Path(__file__).resolve().parents[3]
    repository = ConfigRepository(Path(config_path))
    manager = BotProcessManager(repository.path, project_root)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Останавливает дочерний монитор при завершении веб-сервера."""
        yield
        await manager.stop()

    app = FastAPI(title="Telegram Post Parser", lifespan=lifespan)
    app.state.repository = repository
    app.state.manager = manager

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """Возвращает браузеру публичное представление конфигурации."""
        try:
            return repository.public()
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.put("/api/config")
    async def save_config(payload: dict[str, Any]) -> dict[str, Any]:
        """Сохраняет конфигурацию, если монитор в данный момент остановлен."""
        if manager.running:
            raise HTTPException(status_code=409, detail="Сначала остановите мониторинг")
        try:
            return repository.save(payload)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/bot/status")
    async def bot_status() -> dict[str, Any]:
        """Возвращает состояние процесса мониторинга."""
        return manager.status()

    @app.post("/api/bot/start")
    async def start_bot() -> dict[str, Any]:
        """Запускает процесс мониторинга через HTTP API."""
        try:
            return await manager.start()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/bot/stop")
    async def stop_bot() -> dict[str, Any]:
        """Останавливает процесс мониторинга через HTTP API."""
        return await manager.stop()

    @app.post("/api/bot/input")
    async def bot_input(payload: dict[str, Any]) -> dict[str, bool]:
        """Передаёт введённые пользователем данные в процесс мониторинга."""
        value = str(payload.get("value", ""))
        try:
            await manager.send_input(value)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.websocket("/api/logs")
    async def logs(websocket: WebSocket) -> None:
        """Транслирует строки журнала клиенту по WebSocket."""
        await websocket.accept()
        try:
            async for line in manager.subscribe():
                await websocket.send_text(line)
        except (WebSocketDisconnect, RuntimeError):
            pass

    web_root = static_path or project_root / "web"
    assets = web_root / "assets"
    vendor = web_root / "vendor"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    if vendor.exists():
        app.mount("/vendor", StaticFiles(directory=vendor), name="vendor")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        """Возвращает статический файл или главную страницу одностраничного UI."""
        requested = (web_root / path).resolve()
        if path and requested.is_relative_to(web_root.resolve()) and requested.is_file():
            return FileResponse(requested)
        index = web_root / "index.html"
        if not index.exists():
            raise HTTPException(status_code=503, detail="Web-интерфейс не найден")
        return FileResponse(index)

    return app


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер параметров запуска веб-сервера."""
    parser = argparse.ArgumentParser(description="Web UI for Telegram Post Parser")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    return parser


def main() -> None:
    """Запускает Uvicorn и при необходимости открывает панель в браузере."""
    args = build_parser().parse_args()
    if args.open_browser:
        Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    uvicorn.run(create_app(args.config), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
