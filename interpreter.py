"""Stateless interpreter for raw nzm-wiki MDX and related JSON sources."""

from __future__ import annotations

import ast
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    from .modifier_protocol import ModifierProtocolError, ModifierProtocolResolver
    from .weapon_protocol import WeaponProtocolError, WeaponProtocolResolver
except ImportError:  # Direct interpreter execution in the standalone test project.
    from modifier_protocol import ModifierProtocolError, ModifierProtocolResolver
    from weapon_protocol import WeaponProtocolError, WeaponProtocolResolver

_SOURCE_SUFFIXES = frozenset({".md", ".mdx", ".json"})
_SKIPPED_PARTS = frozenset({".git", "node_modules", "refs", "refs-test", "MD"})
_NON_CONSUMER_FILE_MARKERS = frozenset({"migration", "snapshot", "pilot", "report"})
_ANSI_OR_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<header>.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_IMPORT_EXPORT_RE = re.compile(r"(?m)^\s*(?:import|export)\s+.*(?:\n|$)")
_COMMENT_RE = re.compile(r"\{?/\*.*?\*/\}?|<!--.*?-->", re.DOTALL)
_LEVEL_TABLE_RE = re.compile(r"<LevelTable\b(?P<attrs>.*?)/>", re.DOTALL)
_OPEN_COMPONENT_RE = re.compile(r"<(?P<tag>[A-Z][A-Za-z0-9_.]*)\b(?P<attrs>.*?)(?P<self>/?)>", re.DOTALL)
_CLOSE_COMPONENT_RE = re.compile(r"</[A-Z][A-Za-z0-9_.]*\s*>")
_HTML_TAG_RE = re.compile(r"</?[a-z][^>]*>")
_SIMPLE_PROP_RE = re.compile(
    r"(?P<name>[A-Za-z_][\w-]*)\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|\{(?P<brace>[^{}]*)\})",
    re.DOTALL,
)
_WORD_RE = re.compile(r"[a-zA-Z0-9_.:/+-]+|[\u3400-\u9fff]+")
_ID_RE = re.compile(r"^\d{5,}$")
_WEAPON_SKILL_RE = re.compile(
    r"(?ms)^### (?P<kind>主动技能|被动技能): (?P<name>[^\n]+)\n"
    r"(?P<description>.*?)(?=^#{1,3}\s|\Z)"
)
_MODIFIER_INTENT_TERMS = (
    "增伤",
    "伤害提高",
    "伤害提升",
    "伤害增加",
    "易伤",
    "乘区",
    "伤害倍率",
)


class WikiQueryError(RuntimeError):
    """Raised when a stateless Wiki request cannot be completed."""


@dataclass(frozen=True)
class _Candidate:
    """One source candidate retained only for the current request."""

    path: Path
    relative_path: str
    raw_text: str
    title: str
    frontmatter: dict[str, Any]
    score: float


class NzmWikiInterpreter:
    """Interpret a read-only nzm-wiki repository for one request at a time."""

    def __init__(
        self,
        repo_path: str,
        source_dirs: list[str] | None = None,
        max_file_size_mb: int = 64,
    ):
        """Configure immutable source locations.

        Args:
            repo_path: Read-only nzm-wiki repository root.
            source_dirs: Repository-relative directories eligible for queries.
            max_file_size_mb: Per-source read ceiling.

        Raises:
            WikiQueryError: If paths are missing or escape the repository root.
        """
        self.repo_path = Path(repo_path).expanduser().resolve()
        if not self.repo_path.is_dir():
            raise WikiQueryError(f"repository does not exist: {self.repo_path}")

        requested_dirs = source_dirs or ["data"]
        self.source_dirs: tuple[Path, ...] = tuple(
            self._resolve_source_dir(item) for item in requested_dirs
        )
        self.max_file_bytes = max(1, int(max_file_size_mb)) * 1024 * 1024

    def query(
        self,
        query: str,
        *,
        max_results: int = 6,
        max_characters: int = 18000,
    ) -> dict[str, Any]:
        """Search raw sources and return an answer-ready evidence bundle.

        Args:
            query: User question, entity name, or exact value query.
            max_results: Maximum number of evidence items.
            max_characters: Approximate total serialized evidence budget.

        Returns:
            A source-grounded dictionary suitable for an AstrBot tool result.

        Raises:
            WikiQueryError: If the query is empty or sources cannot be read.
        """
        query = str(query or "").strip()
        if not query:
            raise WikiQueryError("query is empty")

        result_limit = max(1, min(int(max_results), 12))
        character_limit = max(1000, int(max_characters))
        terms = self._query_terms(query)
        revision = self._repository_revision()
        candidates, warnings = self._find_candidates(query, terms, result_limit)

        evidence: list[dict[str, Any]] = []
        remaining = character_limit
        mdx_references: set[str] = set()
        selected_paths: set[str] = set()
        interpreted_markdown: list[dict[str, Any]] = []

        markdown_candidates = [
            candidate
            for candidate in candidates
            if candidate.path.suffix.lower() in {".md", ".mdx"}
        ]
        json_candidates = [
            candidate
            for candidate in candidates
            if candidate.path.suffix.lower() == ".json"
        ]
        primary_score = markdown_candidates[0].score if markdown_candidates else 0
        selected_markdown = [
            candidate
            for candidate in markdown_candidates
            if candidate.score >= max(12, primary_score * 0.45)
        ][: max(1, min(3, result_limit // 2))]

        for candidate in selected_markdown:
            if len(evidence) >= result_limit or remaining < 500:
                break
            item = self._interpret_markdown_candidate(
                candidate,
                query=query,
                terms=terms,
                revision=revision,
                max_characters=min(remaining, 7000),
            )
            item_size = len(json.dumps(item, ensure_ascii=False))
            if item_size > remaining:
                continue
            evidence.append(item)
            remaining -= item_size
            selected_paths.add(candidate.relative_path)
            mdx_references.update(item.pop("_reference_values", []))
            interpreted_markdown.append(item)
            try:
                protocol_item = self._resolve_weapon_protocol(item, focus=query)
            except WeaponProtocolError as exc:
                warnings.append(f"{candidate.relative_path}: {exc}")
                protocol_item = None
            if protocol_item and len(evidence) < result_limit:
                protocol_size = len(json.dumps(protocol_item, ensure_ascii=False))
                if protocol_size <= remaining:
                    evidence.append(protocol_item)
                    remaining -= protocol_size

        if (
            self._needs_modifier_context(query, interpreted_markdown)
            and len(evidence) < result_limit
            and remaining >= 500
        ):
            try:
                modifier_item = ModifierProtocolResolver(
                    self.repo_path, self.max_file_bytes
                ).resolve(query, contexts=interpreted_markdown[:1])
            except ModifierProtocolError as exc:
                warnings.append(str(exc))
                modifier_item = None
            if modifier_item:
                modifier_size = len(json.dumps(modifier_item, ensure_ascii=False))
                if modifier_size <= remaining:
                    evidence.append(modifier_item)
                    remaining -= modifier_size

        if mdx_references and len(evidence) < result_limit and remaining >= 500:
            related = self._find_related_json(
                query=query,
                terms=terms,
                references=mdx_references,
                excluded_paths=selected_paths,
                revision=revision,
                max_results=result_limit - len(evidence),
                max_characters=remaining,
            )
            for item in related:
                item_size = len(json.dumps(item, ensure_ascii=False))
                if item_size > remaining or len(evidence) >= result_limit:
                    break
                evidence.append(item)
                remaining -= item_size

        for candidate in (json_candidates if not selected_markdown else []):
            if len(evidence) >= result_limit or remaining < 500:
                break
            if candidate.relative_path in selected_paths:
                continue
            items = self._interpret_json_candidate(
                candidate,
                query=query,
                terms=terms,
                references=set(),
                revision=revision,
                max_characters=min(remaining, 6000),
            )
            for item in items:
                item_size = len(json.dumps(item, ensure_ascii=False))
                if item_size > remaining or len(evidence) >= result_limit:
                    break
                evidence.append(item)
                remaining -= item_size
                selected_paths.add(candidate.relative_path)

        return {
            "query": query,
            "repository_revision": revision,
            "retrieval": "local_repository_only",
            "evidence": evidence,
            "warnings": warnings,
            "truncated": len(candidates) > len(selected_paths) or remaining < 500,
            "stateless": True,
        }

    def read(
        self,
        source_path: str,
        *,
        focus: str = "",
        max_characters: int = 18000,
    ) -> dict[str, Any]:
        """Read one exact repository source and resolve nearby evidence.

        Args:
            source_path: Repository-relative path returned by ``query``.
            focus: Optional phrase used to select relevant source sections.
            max_characters: Approximate serialized result budget.

        Returns:
            A source-grounded dictionary for the exact file.

        Raises:
            WikiQueryError: If the path is unsafe, unsupported, or unreadable.
        """
        path = self._resolve_read_path(source_path)
        raw_text = self._read_text(path)
        relative_path = path.relative_to(self.repo_path).as_posix()
        query = focus or path.stem
        terms = self._query_terms(query)
        frontmatter, _ = self._split_frontmatter(raw_text)
        candidate = _Candidate(
            path=path,
            relative_path=relative_path,
            raw_text=raw_text,
            title=str(frontmatter.get("title") or path.stem),
            frontmatter=frontmatter,
            score=1.0,
        )
        revision = self._repository_revision()
        character_limit = max(1000, int(max_characters))

        if path.suffix.lower() == ".json":
            evidence = self._interpret_json_candidate(
                candidate,
                query=query,
                terms=terms,
                references=set(),
                revision=revision,
                max_characters=character_limit,
            )
        else:
            item = self._interpret_markdown_candidate(
                candidate,
                query=query,
                terms=terms,
                revision=revision,
                max_characters=min(character_limit, 12000),
                prefer_full=not focus,
            )
            references = set(item.pop("_reference_values", []))
            evidence = [item]
            used = len(json.dumps(item, ensure_ascii=False))
            warnings: list[str] = []
            try:
                protocol_item = self._resolve_weapon_protocol(item, focus=query)
            except WeaponProtocolError as exc:
                warnings.append(f"{relative_path}: {exc}")
                protocol_item = None
            if protocol_item:
                protocol_size = len(json.dumps(protocol_item, ensure_ascii=False))
                if used + protocol_size <= character_limit:
                    evidence.append(protocol_item)
                    used += protocol_size
            if self._needs_modifier_context(query, [item]):
                try:
                    modifier_item = ModifierProtocolResolver(
                        self.repo_path, self.max_file_bytes
                    ).resolve(query, contexts=[item])
                except ModifierProtocolError as exc:
                    warnings.append(str(exc))
                    modifier_item = None
                if modifier_item:
                    modifier_size = len(json.dumps(modifier_item, ensure_ascii=False))
                    if used + modifier_size <= character_limit:
                        evidence.append(modifier_item)
                        used += modifier_size
            if references and used < character_limit:
                evidence.extend(
                    self._find_related_json(
                        query=query,
                        terms=terms,
                        references=references,
                        excluded_paths={relative_path},
                        revision=revision,
                        max_results=4,
                        max_characters=character_limit - used,
                    )
                )

        return {
            "query": query,
            "repository_revision": revision,
            "retrieval": "local_repository_only",
            "evidence": evidence,
            "warnings": warnings if path.suffix.lower() != ".json" else [],
            "truncated": len(json.dumps(evidence, ensure_ascii=False))
            >= character_limit,
            "stateless": True,
        }

    def _resolve_source_dir(self, source_dir: str) -> Path:
        """Resolve one allowed directory beneath the repository root."""
        candidate = (self.repo_path / str(source_dir)).resolve()
        if not candidate.is_relative_to(self.repo_path):
            raise WikiQueryError(f"source directory escapes repository: {source_dir}")
        if not candidate.is_dir():
            raise WikiQueryError(f"source directory does not exist: {source_dir}")
        return candidate

    def _resolve_read_path(self, source_path: str) -> Path:
        """Resolve an exact source path within configured source directories."""
        candidate = (self.repo_path / str(source_path)).resolve()
        if not candidate.is_file() or candidate.suffix.lower() not in _SOURCE_SUFFIXES:
            raise WikiQueryError(f"unsupported source file: {source_path}")
        if not any(candidate.is_relative_to(root) for root in self.source_dirs):
            raise WikiQueryError(f"source is outside configured directories: {source_path}")
        return candidate

    def _iter_source_files(self):
        """Yield eligible source files without retaining an index."""
        for source_dir in self.source_dirs:
            for path in source_dir.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                relative = path.relative_to(self.repo_path)
                if any(part in _SKIPPED_PARTS or part.startswith(".") for part in relative.parts):
                    continue
                if any(marker in path.name.lower() for marker in _NON_CONSUMER_FILE_MARKERS):
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(self.repo_path):
                    continue
                yield resolved

    def _read_text(self, path: Path) -> str:
        """Read a bounded UTF-8 source file."""
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise WikiQueryError(f"cannot stat {path}: {exc}") from exc
        if size > self.max_file_bytes:
            raise WikiQueryError(
                f"source exceeds {self.max_file_bytes // (1024 * 1024)} MB: {path}"
            )
        try:
            return _ANSI_OR_CONTROL_RE.sub("", path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            raise WikiQueryError(f"cannot read {path}: {exc}") from exc

    def _find_candidates(
        self,
        query: str,
        terms: list[str],
        max_results: int,
    ) -> tuple[list[_Candidate], list[str]]:
        """Scan raw files for this request and retain only top candidates."""
        candidates: list[_Candidate] = []
        warnings: list[str] = []
        for path in self._iter_source_files():
            try:
                raw_text = self._read_text(path)
            except WikiQueryError as exc:
                warnings.append(str(exc))
                continue
            relative_path = path.relative_to(self.repo_path).as_posix()
            frontmatter: dict[str, Any] = {}
            title = path.stem
            if path.suffix.lower() in {".md", ".mdx"}:
                frontmatter, _ = self._split_frontmatter(raw_text)
                title = str(frontmatter.get("title") or path.stem)
            score = self._score_source(
                query=query,
                terms=terms,
                title=title,
                relative_path=relative_path,
                raw_text=raw_text,
            )
            if score <= 0:
                continue
            candidates.append(
                _Candidate(
                    path=path,
                    relative_path=relative_path,
                    raw_text=raw_text,
                    title=title,
                    frontmatter=frontmatter,
                    score=score,
                )
            )

        candidates.sort(
            key=lambda item: (
                -item.score,
                0 if item.path.suffix.lower() in {".md", ".mdx"} else 1,
                item.relative_path,
            )
        )
        return candidates[: max(max_results * 4, 16)], warnings[:10]

    def _score_source(
        self,
        *,
        query: str,
        terms: list[str],
        title: str,
        relative_path: str,
        raw_text: str,
    ) -> float:
        """Score a source using deterministic lexical evidence."""
        normalized_query = self._normalize(query)
        normalized_title = self._normalize(title)
        normalized_path = self._normalize(relative_path)
        normalized_text = self._normalize(raw_text)
        score = 0.0
        if normalized_query and normalized_query in normalized_title:
            score += 120
        elif normalized_query and normalized_query in normalized_path:
            score += 90
        elif normalized_query and normalized_query in normalized_text:
            score += 60
        for term in terms:
            if term in normalized_title:
                score += 24
            if term in normalized_path:
                score += 14
            count = normalized_text.count(term)
            score += min(count, 8) * 2
        return score

    def _interpret_markdown_candidate(
        self,
        candidate: _Candidate,
        *,
        query: str,
        terms: list[str],
        revision: str,
        max_characters: int,
        prefer_full: bool = False,
    ) -> dict[str, Any]:
        """Convert MDX into static readable evidence."""
        frontmatter, body = self._split_frontmatter(candidate.raw_text)
        interpreted = self._render_mdx(body)
        content_budget = max(500, max_characters - 2500)
        content = (
            interpreted[:content_budget]
            if prefer_full and len(interpreted) <= content_budget
            else self._relevant_excerpt(interpreted, query, terms, content_budget)
        )
        references = sorted(self._collect_reference_values(frontmatter))
        result = {
            "kind": "mdx" if candidate.path.suffix.lower() == ".mdx" else "markdown",
            "title": candidate.title,
            "source_path": candidate.relative_path,
            "frontmatter": frontmatter,
            "content": content,
            "references": references,
            "score": round(candidate.score, 2),
            "_reference_values": references,
        }
        if candidate.relative_path.startswith("data/weapons/"):
            result["weapon_skills"] = self._extract_weapon_skills(
                interpreted,
                source_path=candidate.relative_path,
            )
        return result

    @staticmethod
    def _extract_weapon_skills(
        interpreted: str,
        *,
        source_path: str,
    ) -> dict[str, Any]:
        """Return complete active/passive skill text independently of query excerpts."""
        active: list[dict[str, str]] = []
        passive: list[dict[str, str]] = []
        for match in _WEAPON_SKILL_RE.finditer(interpreted):
            item = {
                "name": match.group("name").strip(),
                "description": match.group("description").strip()[:4000],
            }
            target = active if match.group("kind") == "主动技能" else passive
            target.append(item)
        return {
            "active": active,
            "passive": passive,
            "source_path": source_path,
            "complete_from_mdx": True,
        }

    def _needs_modifier_context(
        self,
        query: str,
        contexts: list[dict[str, Any]],
    ) -> bool:
        """Route modifier intent internally; callers do not select data channels."""
        normalized_query = self._normalize(query)
        if any(self._normalize(term) in normalized_query for term in _MODIFIER_INTENT_TERMS):
            return True
        for context in contexts:
            skills = context.get("weapon_skills")
            if not isinstance(skills, dict):
                continue
            for kind in ("active", "passive"):
                for skill in skills.get(kind, []):
                    if not isinstance(skill, dict):
                        continue
                    name = self._normalize(skill.get("name"))
                    description = self._normalize(skill.get("description"))
                    describes_modifier = any(
                        self._normalize(term) in description
                        for term in _MODIFIER_INTENT_TERMS
                    )
                    if name and name in normalized_query and describes_modifier:
                        return True
        return False

    def _resolve_weapon_protocol(
        self,
        mdx_evidence: dict[str, Any],
        *,
        focus: str,
    ) -> dict[str, Any] | None:
        """Resolve a V2 weapon through the project's committed local Lock contract."""
        frontmatter = mdx_evidence.get("frontmatter")
        source_path = mdx_evidence.get("source_path")
        if not isinstance(frontmatter, dict) or not isinstance(source_path, str):
            return None
        return WeaponProtocolResolver(self.repo_path, self.max_file_bytes).resolve(
            frontmatter,
            source_path=source_path,
            focus=focus,
        )

    def _interpret_json_candidate(
        self,
        candidate: _Candidate,
        *,
        query: str,
        terms: list[str],
        references: set[str],
        revision: str,
        max_characters: int,
    ) -> list[dict[str, Any]]:
        """Extract matching JSON nodes rather than returning a whole data file."""
        try:
            data = json.loads(candidate.raw_text)
        except json.JSONDecodeError as exc:
            return [
                {
                    "kind": "warning",
                    "title": candidate.title,
                    "source_path": candidate.relative_path,
                    "content": f"Invalid JSON: {exc}",
                }
            ]
        matches = self._extract_json_matches(
            data,
            query=query,
            terms=terms,
            references=references,
            max_results=4,
        )
        if not matches:
            return []

        results: list[dict[str, Any]] = []
        remaining = max_characters
        for score, pointer, node in matches:
            serialized = json.dumps(node, ensure_ascii=False, indent=2)
            if len(serialized) > min(remaining - 300, 5000):
                serialized = serialized[: max(0, min(remaining - 340, 4960))] + "\n..."
            item = {
                "kind": "json",
                "title": candidate.title,
                "source_path": candidate.relative_path,
                "json_pointer": pointer,
                "content": serialized,
                "score": round(candidate.score + score, 2),
            }
            size = len(json.dumps(item, ensure_ascii=False))
            if size > remaining:
                break
            results.append(item)
            remaining -= size
        return results

    def _find_related_json(
        self,
        *,
        query: str,
        terms: list[str],
        references: set[str],
        excluded_paths: set[str],
        revision: str,
        max_results: int,
        max_characters: int,
    ) -> list[dict[str, Any]]:
        """Resolve MDX reference values against JSON records for this call."""
        reference_values = {item for item in references if item}
        if not reference_values:
            return []
        collected: list[dict[str, Any]] = []
        for path in self._iter_source_files():
            if path.suffix.lower() != ".json":
                continue
            relative_path = path.relative_to(self.repo_path).as_posix()
            if relative_path in excluded_paths:
                continue
            try:
                raw_text = self._read_text(path)
            except WikiQueryError:
                continue
            matched_refs = {ref for ref in reference_values if ref in raw_text}
            if not matched_refs:
                continue
            candidate = _Candidate(
                path=path,
                relative_path=relative_path,
                raw_text=raw_text,
                title=path.stem,
                frontmatter={},
                score=40 + len(matched_refs) * 5,
            )
            items = self._interpret_json_candidate(
                candidate,
                query=query,
                terms=terms,
                references=matched_refs,
                revision=revision,
                max_characters=min(max_characters, 6000),
            )
            for item in items:
                node_values = [
                    reference
                    for reference in matched_refs
                    if reference in str(item.get("content", ""))
                ]
                item["relationship"] = {
                    "type": "reference_match",
                    "values": sorted(node_values)[:20],
                }
                item["score"] = round(
                    float(item.get("score", 0))
                    + self._source_quality(relative_path),
                    2,
                )
                collected.append(item)

        collected.sort(
            key=lambda item: (-float(item.get("score", 0)), item["source_path"])
        )
        results: list[dict[str, Any]] = []
        remaining = max_characters
        for item in collected:
            size = len(json.dumps(item, ensure_ascii=False))
            if size > remaining:
                continue
            results.append(item)
            remaining -= size
            if len(results) >= max_results:
                break
        return results

    def _extract_json_matches(
        self,
        data: Any,
        *,
        query: str,
        terms: list[str],
        references: set[str],
        max_results: int,
    ) -> list[tuple[float, str, Any]]:
        """Find compact JSON nodes matching query text or explicit references."""
        matches: list[tuple[float, str, Any]] = []
        normalized_query = self._normalize(query)

        def walk(node: Any, path: list[str]) -> None:
            if isinstance(node, dict):
                direct_scalars = {
                    str(key): value
                    for key, value in node.items()
                    if not isinstance(value, (dict, list))
                }
                direct_text = self._normalize(
                    " ".join(
                        [
                            *direct_scalars.keys(),
                            *(str(value) for value in direct_scalars.values()),
                        ]
                    )
                )
                score = 0.0
                reference_score = 0.0
                if normalized_query and normalized_query in direct_text:
                    score += 80
                for term in terms:
                    if term in direct_text:
                        score += 8
                for reference in references:
                    if self._normalize(reference) in direct_text:
                        reference_score += 50
                score += reference_score
                if score > 0 and direct_scalars and (
                    not references or reference_score > 0
                ):
                    matches.append((score, self._json_pointer(path), node))
                for key, value in node.items():
                    if isinstance(value, (dict, list)):
                        walk(value, [*path, str(key)])
                return
            if isinstance(node, list):
                for index, value in enumerate(node):
                    if isinstance(value, (dict, list)):
                        walk(value, [*path, str(index)])

        walk(data, [])
        matches.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
        deduped: list[tuple[float, str, Any]] = []
        fingerprints: set[str] = set()
        for match in matches:
            fingerprint = json.dumps(match[2], ensure_ascii=False, sort_keys=True)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            deduped.append(match)
            if len(deduped) >= max_results:
                break
        return deduped

    def _split_frontmatter(self, raw_text: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter while preserving unknown fields."""
        match = _FRONTMATTER_RE.match(raw_text)
        if not match:
            return {}, raw_text
        try:
            parsed = yaml.safe_load(match.group("header")) or {}
        except yaml.YAMLError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {"value": parsed}
        return self._json_safe(parsed), raw_text[match.end() :]

    def _render_mdx(self, body: str) -> str:
        """Render MDX statically without executing expressions or components."""
        rendered = _COMMENT_RE.sub("", body)
        rendered = _IMPORT_EXPORT_RE.sub("", rendered)
        rendered = re.sub(r"<br\s*/?>", "\n", rendered, flags=re.IGNORECASE)
        rendered = _LEVEL_TABLE_RE.sub(self._render_level_table_match, rendered)
        rendered = _OPEN_COMPONENT_RE.sub(self._render_component_match, rendered)
        rendered = _CLOSE_COMPONENT_RE.sub("", rendered)
        rendered = _HTML_TAG_RE.sub("", rendered)
        rendered = re.sub(r"\n[ \t]+", "\n", rendered)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        return rendered.strip()

    def _render_level_table_match(self, match: re.Match[str]) -> str:
        """Render a static LevelTable component as Markdown when possible."""
        props = self._parse_static_props(match.group("attrs"))
        headers = props.get("headers")
        rows = props.get("data")
        if (
            isinstance(headers, list)
            and headers
            and isinstance(rows, list)
            and all(isinstance(row, list) for row in rows)
        ):
            header_cells = [str(item) for item in headers]
            lines = [
                "| " + " | ".join(header_cells) + " |",
                "| " + " | ".join("---" for _ in header_cells) + " |",
            ]
            for row in rows:
                cells = [str(item) for item in row]
                if len(cells) < len(header_cells):
                    cells.extend("" for _ in range(len(header_cells) - len(cells)))
                lines.append("| " + " | ".join(cells[: len(header_cells)]) + " |")
            return "\n\n" + "\n".join(lines) + "\n\n"
        return "\n[LevelTable: static data could not be decoded]\n"

    def _render_component_match(self, match: re.Match[str]) -> str:
        """Convert a generic MDX component opening tag into semantic text."""
        tag = match.group("tag")
        props = self._parse_static_props(match.group("attrs"))
        if tag in {"Yellow", "Blue", "Grey", "Gray", "Red", "Green", "Bold"}:
            return ""
        if tag == "GameMode":
            mode = props.get("only") or props.get("mode") or "unspecified"
            return f"\n\n[游戏模式: {mode}]\n"
        if tag in {"ActiveSkill", "PassiveSkill"}:
            name = props.get("name") or "未命名"
            kind = "主动技能" if tag == "ActiveSkill" else "被动技能"
            return f"\n\n### {kind}: {name}\n"
        if tag == "Callout":
            return "\n\n> 提示\n"
        if tag == "WeaponSkill":
            return "\n\n## 武器技能\n"

        static_props = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in props.items()
            if key not in {"children"}
        )
        suffix = f": {static_props}" if static_props else ""
        return f"\n[{tag}{suffix}]\n"

    def _parse_static_props(self, attrs: str) -> dict[str, Any]:
        """Parse only literal MDX props and never evaluate JavaScript."""
        props: dict[str, Any] = {}
        for match in _SIMPLE_PROP_RE.finditer(attrs):
            raw = match.group("double")
            if raw is None:
                raw = match.group("single")
            if raw is not None:
                props[match.group("name")] = raw
                continue
            expression = (match.group("brace") or "").strip()
            safe_expression = re.sub(r"\btrue\b", "True", expression)
            safe_expression = re.sub(r"\bfalse\b", "False", safe_expression)
            safe_expression = re.sub(r"\bnull\b", "None", safe_expression)
            try:
                props[match.group("name")] = ast.literal_eval(safe_expression)
            except (SyntaxError, ValueError):
                if expression and len(expression) <= 200:
                    props[match.group("name")] = expression
        return props

    def _relevant_excerpt(
        self,
        text: str,
        query: str,
        terms: list[str],
        max_characters: int,
    ) -> str:
        """Select relevant line windows while retaining nearby headings."""
        if len(text) <= max_characters:
            return text
        lines = text.splitlines()
        normalized_query = self._normalize(query)
        match_indexes: list[int] = []
        for index, line in enumerate(lines):
            normalized_line = self._normalize(line)
            if normalized_query and normalized_query in normalized_line:
                match_indexes.append(index)
                continue
            if any(term in normalized_line for term in terms):
                match_indexes.append(index)

        if not match_indexes:
            return text[:max_characters].rstrip() + "\n..."
        selected: set[int] = set()
        for index in match_indexes[:30]:
            selected.update(range(max(0, index - 3), min(len(lines), index + 5)))
            for heading_index in range(index, -1, -1):
                if lines[heading_index].lstrip().startswith("#"):
                    selected.add(heading_index)
                    break

        chunks: list[str] = []
        previous = -2
        for index in sorted(selected):
            if index != previous + 1 and chunks:
                chunks.append("...")
            chunks.append(lines[index])
            previous = index
        excerpt = "\n".join(chunks).strip()
        if len(excerpt) > max_characters:
            excerpt = excerpt[:max_characters].rstrip() + "\n..."
        return excerpt

    def _collect_reference_values(self, data: Any) -> set[str]:
        """Collect generic ID-like scalar references from parsed frontmatter."""
        references: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for child in value.values():
                    walk(child)
                return
            if isinstance(value, list):
                for child in value:
                    walk(child)
                return
            if isinstance(value, bool) or value is None:
                return
            scalar = str(value).strip()
            if _ID_RE.fullmatch(scalar):
                references.add(scalar)

        walk(data)
        return references

    def _query_terms(self, query: str) -> list[str]:
        """Build deterministic Latin tokens and Chinese bigrams."""
        terms: set[str] = set()
        for token in _WORD_RE.findall(query.lower()):
            normalized = self._normalize(token)
            if not normalized:
                continue
            terms.add(normalized)
            if re.fullmatch(r"[\u3400-\u9fff]+", normalized) and len(normalized) > 2:
                terms.update(
                    normalized[index : index + 2]
                    for index in range(len(normalized) - 1)
                )
        return sorted(terms, key=lambda item: (-len(item), item))

    @staticmethod
    def _normalize(value: Any) -> str:
        """Normalize text for exact deterministic matching."""
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value).lower())

    @staticmethod
    def _source_quality(relative_path: str) -> float:
        """Prefer published/runtime data over migration and audit artifacts."""
        normalized = relative_path.lower()
        if any(
            marker in normalized
            for marker in ("migration", "snapshot", "report", "decision", "pilot")
        ):
            return -200.0
        if any(marker in normalized for marker in ("-lock.json", "-runtime.json")):
            return 25.0
        return 0.0

    @staticmethod
    def _json_pointer(path: list[str]) -> str:
        """Encode a JSON Pointer for provenance."""
        if not path:
            return ""
        encoded = [part.replace("~", "~0").replace("/", "~1") for part in path]
        return "/" + "/".join(encoded)

    def _repository_revision(self) -> str:
        """Read Git HEAD directly without invoking Git or changing state."""
        git_path = self.repo_path / ".git"
        try:
            if git_path.is_file():
                pointer = git_path.read_text(encoding="utf-8").strip()
                if not pointer.startswith("gitdir:"):
                    return "unknown"
                git_path = (self.repo_path / pointer.split(":", 1)[1].strip()).resolve()
            head = (git_path / "HEAD").read_text(encoding="ascii").strip()
            if not head.startswith("ref:"):
                return head if re.fullmatch(r"[0-9a-fA-F]{40}", head) else "unknown"
            reference = head.split(":", 1)[1].strip()
            loose_ref = git_path / reference
            if loose_ref.is_file():
                revision = loose_ref.read_text(encoding="ascii").strip()
                return (
                    revision
                    if re.fullmatch(r"[0-9a-fA-F]{40}", revision)
                    else "unknown"
                )
            packed_refs = git_path / "packed-refs"
            if packed_refs.is_file():
                for line in packed_refs.read_text(encoding="ascii").splitlines():
                    if line.startswith(("#", "^")):
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == reference:
                        return parts[0]
        except (OSError, UnicodeError):
            return "unknown"
        return "unknown"

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        """Convert YAML scalar types into JSON-serializable equivalents."""
        if isinstance(value, dict):
            return {str(key): cls._json_safe(child) for key, child in value.items()}
        if isinstance(value, list):
            return [cls._json_safe(child) for child in value]
        if isinstance(value, tuple):
            return [cls._json_safe(child) for child in value]
        if isinstance(value, (dt.date, dt.datetime, dt.time)):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
