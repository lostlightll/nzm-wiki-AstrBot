"""LLM tools exposed by the nzm-wiki plugin."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.api.event import AstrMessageEvent

from .interpreter import NzmWikiInterpreter, WikiQueryError


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """Convert a tool argument to a bounded integer.

    Args:
        value: Untrusted tool or plugin configuration value.
        default: Value used when conversion fails or the value is zero.
        minimum: Inclusive lower bound.
        maximum: Inclusive upper bound.

    Returns:
        A bounded integer.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed == 0:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _make_interpreter(plugin: Any) -> NzmWikiInterpreter:
    """Create a fresh interpreter for one stateless request.

    Args:
        plugin: Active AstrBot plugin instance.

    Returns:
        A newly constructed interpreter with no retained query state.
    """
    config = plugin.config
    source_dirs = config.get("source_dirs", ["data"])
    if not isinstance(source_dirs, list):
        source_dirs = ["data"]
    return NzmWikiInterpreter(
        repo_path=str(config.get("repo_path", "/knowledge/nzm-wiki")),
        source_dirs=[str(item) for item in source_dirs],
        max_file_size_mb=_bounded_int(
            config.get("max_file_size_mb", 64),
            default=64,
            minimum=1,
            maximum=128,
        ),
    )


@dataclass
class NzmWikiQueryTool(FunctionTool):
    """Search and interpret relevant raw Wiki sources."""

    plugin: Any = None
    name: str = "nzm_wiki_query"
    description: str = (
        "Query the locally mounted 逆战：未来 nzm-wiki clone for weapons, perks, traps, "
        "enemies, builds, game mechanics, exact values, and related knowledge. "
        "Use this tool before answering any factual question about 逆战：未来. "
        "Never use web search or a remote Wiki as a supplement. It resolves Weapon "
        "Numerical V2, mode-aware HpCalScale base damage, non-damage elemental "
        "status application probability, and damage modifier factors through "
        "committed local data. "
        "Internal source routing is automatic; inspect weapon_skills and "
        "modifier_protocol when present. Base the answer only on returned evidence "
        "with local source_path and json_pointer provenance."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise Chinese query, entity name, or exact value question.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Optional evidence result limit, from 1 to 12.",
                },
                "max_characters": {
                    "type": "number",
                    "description": "Optional total result character budget, from 4000 to 40000.",
                },
            },
            "required": ["query"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        query: str,
        max_results: float = 0,
        max_characters: float = 0,
    ) -> str:
        """Run one independent Wiki query.

        Args:
            event: AstrBot message event supplied by the tool runtime.
            query: User question or search phrase.
            max_results: Optional result count override.
            max_characters: Optional character budget override.

        Returns:
            A JSON evidence bundle or a concise error string.
        """
        del event
        if self.plugin is None:
            return "Error: nzm-wiki plugin is not initialized."
        query = str(query or "").strip()
        if not query:
            return "Error: query is required."

        config = self.plugin.config
        result_limit = _bounded_int(
            max_results or config.get("default_max_results", 6),
            default=6,
            minimum=1,
            maximum=12,
        )
        character_limit = _bounded_int(
            max_characters or config.get("default_max_characters", 18000),
            default=18000,
            minimum=4000,
            maximum=40000,
        )

        try:
            result = await asyncio.to_thread(
                _make_interpreter(self.plugin).query,
                query,
                max_results=result_limit,
                max_characters=character_limit,
            )
            return json.dumps(result, ensure_ascii=False)
        except WikiQueryError as exc:
            return f"Error: nzm-wiki query failed: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.error("nzm_wiki_query failed: %s", exc, exc_info=True)
            return f"Error: nzm-wiki query failed unexpectedly: {exc}"


@dataclass
class NzmWikiReadTool(FunctionTool):
    """Read and interpret one exact source returned by a prior query."""

    plugin: Any = None
    name: str = "nzm_wiki_read"
    description: str = (
        "Read one exact source_path from the locally mounted nzm-wiki clone. "
        "Use it when the search excerpt is insufficient or a detailed table, "
        "skill description, or linked JSON record is needed. Never fetch a remote copy."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Repository-relative source_path from nzm_wiki_query.",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional topic used to select the most relevant sections.",
                },
                "max_characters": {
                    "type": "number",
                    "description": "Optional result character budget, from 4000 to 40000.",
                },
            },
            "required": ["source_path"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        source_path: str,
        focus: str = "",
        max_characters: float = 0,
    ) -> str:
        """Interpret a repository-relative source without retaining state.

        Args:
            event: AstrBot message event supplied by the tool runtime.
            source_path: Exact repository-relative source file.
            focus: Optional section or fact to prioritize.
            max_characters: Optional character budget override.

        Returns:
            A JSON evidence bundle or a concise error string.
        """
        del event
        if self.plugin is None:
            return "Error: nzm-wiki plugin is not initialized."
        source_path = str(source_path or "").strip()
        if not source_path:
            return "Error: source_path is required."

        config = self.plugin.config
        character_limit = _bounded_int(
            max_characters or config.get("default_max_characters", 18000),
            default=18000,
            minimum=4000,
            maximum=40000,
        )
        try:
            result = await asyncio.to_thread(
                _make_interpreter(self.plugin).read,
                source_path,
                focus=str(focus or "").strip(),
                max_characters=character_limit,
            )
            return json.dumps(result, ensure_ascii=False)
        except WikiQueryError as exc:
            return f"Error: nzm-wiki read failed: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.error("nzm_wiki_read failed: %s", exc, exc_info=True)
            return f"Error: nzm-wiki read failed unexpectedly: {exc}"
