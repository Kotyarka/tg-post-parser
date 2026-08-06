from __future__ import annotations

import argparse
import asyncio
import logging

from .config import load_config
from .llm import LLMRewriter
from .service import PostProcessor
from .storage import PostStore
from .telegram import TelegramMonitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Telegram posts and rewrite them with an LLM")
    parser.add_argument("--config", default="config.yml", help="Path to YAML config")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


async def async_main(config_path: str) -> None:
    config = load_config(config_path)
    with PostStore(config.storage.database) as store:
        rewriter = LLMRewriter(config.llm)
        processor = PostProcessor(rewriter, store, config.storage.output_dir)
        await TelegramMonitor(config, processor).run()


def main() -> None:
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

