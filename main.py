"""AstrBot entrypoint for the stateless nzm-wiki interpreter."""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star

from .tools import NzmWikiQueryTool, NzmWikiReadTool

PLUGIN_NAME = "astrbot_plugin_nzm_wiki"


class NzmWikiPlugin(Star):
    """Register stateless Wiki query tools with AstrBot."""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

    async def initialize(self) -> None:
        """Validate the read-only source and register LLM tools."""
        repo_path = Path(
            str(self.config.get("repo_path", "/knowledge/nzm-wiki"))
        ).expanduser()
        if not repo_path.is_dir():
            logger.error(
                "[%s] Wiki repository is unavailable: %s",
                PLUGIN_NAME,
                repo_path,
            )
            return

        source_dirs = self.config.get("source_dirs", ["data"])
        if not isinstance(source_dirs, list) or not source_dirs:
            logger.error("[%s] source_dirs must be a non-empty list", PLUGIN_NAME)
            return

        self.context.add_llm_tools(
            NzmWikiQueryTool(plugin=self),
            NzmWikiReadTool(plugin=self),
        )
        logger.info(
            "[%s] Loaded stateless interpreter from %s",
            PLUGIN_NAME,
            repo_path,
        )

    async def terminate(self) -> None:
        """Terminate without cleanup because the plugin stores no state."""
        logger.info("[%s] Unloaded", PLUGIN_NAME)

