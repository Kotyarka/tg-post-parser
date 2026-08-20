"""Консольная точка входа для запуска Telegram-монитора."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import load_config
from .analyzer import PostAnalyzer
from .parser import PostParser, TelegramParser
from .provider import LLMProvider
from .rewriter import PostRewriter
from .storage import PostStore


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(description="Monitor Telegram posts and rewrite them with an LLM")
    parser.add_argument("--config", default="config.yml", help="Path to YAML config")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


async def async_main(config_path: str) -> None:
    """Собирает компоненты приложения и запускает асинхронный мониторинг."""
    config = load_config(config_path)
    with PostStore(config.storage.database) as store:
        provider = LLMProvider(config.llm, config.gigachat)
        analyzer = PostAnalyzer(config.llm, provider)
        rewriter = PostRewriter(config.llm, provider)
        parser = PostParser(analyzer, rewriter, store, config.storage.output_dir, config.analysis)
        try:
            await TelegramParser(config, parser).run()
        finally:
            await provider.close()


def main() -> None:
    """Обрабатывает аргументы и запускает основной цикл приложения."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(async_main(args.config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
