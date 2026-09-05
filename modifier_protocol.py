"""Resolve damage modifier channels from nzm-wiki's committed runtime data."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ModifierProtocolError(RuntimeError):
    """Raised when committed modifier runtime data violates its contract."""


def _pointer(parts: list[str]) -> str:
    encoded = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(encoded)


class ModifierProtocolResolver:
    """Project provider effects, exact values, and damage factors for one query."""

    INDEX_PATH = "data/modifier-index-runtime.json"
    MULTIPLIER_PATH = "data/guides/multiplier.json"
    NUM_LOCK_PATH = "data/num-modifier-lock.json"

    def __init__(self, repo_path: Path, max_file_bytes: int):
        self.repo_path = repo_path
        self.max_file_bytes = max_file_bytes

    def resolve(
        self,
        query: str,
        *,
        contexts: list[dict[str, Any]],
        max_providers: int = 6,
    ) -> dict[str, Any] | None:
        """Resolve matching providers without exposing source-selection work to the LLM."""
        index = self._read_json(self.INDEX_PATH)
        multiplier = self._read_json(self.MULTIPLIER_PATH)
        num_lock = self._read_json(self.NUM_LOCK_PATH)

        attribute_types = {
            str(item.get("id")): item
            for item in index.get("attributeTypes", [])
            if isinstance(item, dict) and item.get("id")
        }
        factors = {
            str(item.get("id")): (index, item)
            for index, item in enumerate(multiplier.get("factors", []))
            if isinstance(item, dict) and item.get("id")
        }
        dilution_categories = {
            str(item.get("id")): (index, item)
            for index, item in enumerate(multiplier.get("dilutionCategories", []))
            if isinstance(item, dict) and item.get("id")
        }

        matched: list[tuple[float, int, dict[str, Any]]] = []
        for index_position, provider in enumerate(index.get("providers", [])):
            if not isinstance(provider, dict):
                continue
            score = self._provider_score(provider, query, contexts)
            if score <= 0:
                continue
            effects = self._resolve_effects(
                provider.get("effects"),
                attribute_types=attribute_types,
                factors=factors,
                dilution_categories=dilution_categories,
                num_lock=num_lock,
            )
            if not effects:
                continue
            matched.append(
                (
                    score,
                    index_position,
                    {
                        "id": provider.get("id"),
                        "label": provider.get("label"),
                        "source": provider.get("source"),
                        "effects": effects,
                        "reviewed_override": provider.get("reviewedOverride", False),
                        "provenance": [
                            {
                                "role": "provider",
                                "source_path": self.INDEX_PATH,
                                "json_pointer": _pointer(
                                    ["providers", str(index_position)]
                                ),
                            }
                        ],
                    },
                )
            )

        matched.sort(key=lambda item: (-item[0], str(item[2].get("label") or "")))
        providers = [item[2] for item in matched[: max(1, max_providers)]]
        if not providers:
            return None
        return {
            "kind": "modifier_protocol",
            "title": "增伤与乘区解析",
            "source_path": self.INDEX_PATH,
            "protocol": "num-modifier-v2",
            "retrieval": "local_committed_runtime",
            "providers": providers,
            "provenance": [
                {"role": "provider_index", "source_path": self.INDEX_PATH},
                {"role": "factor_definition", "source_path": self.MULTIPLIER_PATH},
                {"role": "numerical_lock", "source_path": self.NUM_LOCK_PATH},
            ],
        }

    def _resolve_effects(
        self,
        raw_effects: Any,
        *,
        attribute_types: dict[str, dict[str, Any]],
        factors: dict[str, tuple[int, dict[str, Any]]],
        dilution_categories: dict[str, tuple[int, dict[str, Any]]],
        num_lock: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_effects, list):
            return []
        results: list[dict[str, Any]] = []
        for effect in raw_effects:
            if not isinstance(effect, dict):
                continue
            attribute_type = attribute_types.get(str(effect.get("attributeTypeId")))
            resolved_factors = self._resolve_factors(
                effect,
                attribute_type=attribute_type,
                factors=factors,
                dilution_categories=dilution_categories,
            )
            if not resolved_factors:
                continue
            result = {
                "row": effect.get("row"),
                "attribute_type_id": effect.get("attributeTypeId"),
                "attribute_label": effect.get("attributeLabel"),
                "direction": effect.get("direction"),
                "recipient": effect.get("recipient"),
                "facets": effect.get("facetIds", []),
                "factors": resolved_factors,
                "reviewed": effect.get("reviewed", False),
            }
            numerical = self._resolve_numerical(
                str(effect.get("row") or ""),
                attribute_type=attribute_type,
                num_lock=num_lock,
            )
            if numerical:
                result["numerical"] = numerical
            results.append(result)
        return results

    def _resolve_factors(
        self,
        effect: dict[str, Any],
        *,
        attribute_type: dict[str, Any] | None,
        factors: dict[str, tuple[int, dict[str, Any]]],
        dilution_categories: dict[str, tuple[int, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        facet_ids = [str(item) for item in effect.get("facetIds", [])]
        if not facet_ids and attribute_type:
            facet = attribute_type.get("facets", {}).get(str(effect.get("direction")))
            if isinstance(facet, dict) and facet.get("consumer") == "damage":
                facet_ids.append(str(facet.get("id")))

        results: list[dict[str, Any]] = []
        for facet_id in facet_ids:
            factor_id = facet_id
            provenance_pointer: str | None = None
            if facet_id in dilution_categories:
                factor_id = "dilution"
                category_index, category = dilution_categories[facet_id]
                provenance_pointer = _pointer(
                    ["dilutionCategories", str(category_index)]
                )
                category_label = category.get("target")
            else:
                category_label = None
            factor_entry = factors.get(factor_id)
            if factor_entry is None:
                continue
            factor_index, factor = factor_entry
            resolved = {
                "id": factor_id,
                "label": factor.get("label"),
                "facet_id": facet_id,
                "source_path": self.MULTIPLIER_PATH,
                "json_pointer": _pointer(["factors", str(factor_index)]),
            }
            if category_label:
                resolved["modifier_type"] = category_label
                resolved["modifier_type_json_pointer"] = provenance_pointer
            results.append(resolved)
        return results

    def _resolve_numerical(
        self,
        row: str,
        *,
        attribute_type: dict[str, Any] | None,
        num_lock: dict[str, Any],
    ) -> dict[str, Any] | None:
        if ":" not in row:
            return None
        namespace, key = row.split(":", 1)
        record = num_lock.get("rows", {}).get(namespace, {}).get(key)
        raw = record.get("raw") if isinstance(record, dict) else None
        if not isinstance(raw, dict):
            raise ModifierProtocolError(f"missing numerical modifier row: {row}")

        operation = str(raw.get("GPModifierOp") or "")
        base_value = raw.get("BaseValue")
        coefficient = raw.get("CoefValue")
        result: dict[str, Any] = {
            "base_value": base_value,
            "coefficient_value": coefficient,
            "operation": operation,
            "level": raw.get("Level"),
            "attribute_name": raw.get("AttributeName"),
            "description": raw.get("Description"),
            "source_path": self.NUM_LOCK_PATH,
            "json_pointer": _pointer(["rows", namespace, key, "raw"]),
        }
        if operation == "B1":
            result["operation_semantics"] = "additive_delta"
            result["certainty"] = "structured"
            if (
                attribute_type
                and attribute_type.get("quantity") == "ratio"
                and isinstance(base_value, (int, float))
                and coefficient in {0, 0.0, None}
            ):
                result["display_value"] = f"{base_value:+.0%}"
        elif operation == "B5":
            result["operation_semantics"] = "multiplicative_family"
            result["certainty"] = "operation_only"
            result["caveat"] = "B5 的统一基线与换算公式尚未确认，不能据此臆造倍率。"
        elif row == "lc:111031014_1_0" and operation == "B2" and base_value == 6:
            result["operation_semantics"] = "single_event_correction"
            result["derived_multiplier"] = 7
            result["certainty"] = "reviewed_exception"
        else:
            result["operation_semantics"] = "unresolved"
            result["certainty"] = "raw_only"
            result["caveat"] = "该操作码尚无可安全套用的统一换算规则。"
        return result

    def _provider_score(
        self,
        provider: dict[str, Any],
        query: str,
        contexts: list[dict[str, Any]],
    ) -> float:
        normalized_query = self._normalize(query)
        label = self._normalize(provider.get("label"))
        source = provider.get("source")
        source = source if isinstance(source, dict) else {}
        slug = self._normalize(source.get("slug"))
        skill_name = self._normalize(source.get("skillName"))
        item_id = str(source.get("itemId") or "")
        score = 0.0

        if label and label in normalized_query:
            score += 160
        if slug and slug in normalized_query:
            score += 80
        if skill_name and skill_name in normalized_query:
            score += 120

        for context in contexts:
            title = self._normalize(context.get("title"))
            path = str(context.get("source_path") or "")
            frontmatter = context.get("frontmatter")
            frontmatter = frontmatter if isinstance(frontmatter, dict) else {}
            context_ids = {
                str(frontmatter.get(key))
                for key in ("id", "item_id", "itemId")
                if frontmatter.get(key) is not None
            }
            if title and slug == title:
                score += 120
            if item_id and item_id in context_ids:
                score += 180
            if source.get("type") == "weapon" and path.startswith("data/weapons/"):
                if title and (slug == title or title in label):
                    score += 40
            if source.get("type") == "perk" and path.startswith("data/perks/"):
                if title and (slug == title or title in label):
                    score += 40
        return score

    def _read_json(self, relative_path: str) -> dict[str, Any]:
        path = (self.repo_path / relative_path).resolve()
        if not path.is_relative_to(self.repo_path) or not path.is_file():
            raise ModifierProtocolError(f"missing committed runtime data: {relative_path}")
        if path.stat().st_size > self.max_file_bytes:
            raise ModifierProtocolError(f"runtime data exceeds read limit: {relative_path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ModifierProtocolError(f"cannot read {relative_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ModifierProtocolError(f"invalid runtime data root: {relative_path}")
        return data

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", str(value or "").lower())
