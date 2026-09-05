"""Read-only interpretation of nzm-wiki's Weapon Numerical V2 protocol."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_MODE_BASE_ATTACK = {"lc": 500, "td": 400}
_DAMAGE_HEALTH_SETTLEMENTS = frozenset(
    {
        "Numerical.SettlementType.Health.WeaponDamage",
        "Numerical.SettlementType.Health.MeleeWeaponDamage",
        "Numerical.SettlementType.Health.WeaponExplosionDamage",
        "Numerical.SettlementType.Health.WeaponSkillDamage",
        "Numerical.SettlementType.Health.SkillDamage",
        "Numerical.SettlementType.Health.DebuffDamage",
        "Numerical.SettlementType.Health.IndirectDamage",
        "Numerical.SettlementType.Health.EnvironmentDamage",
        "Numerical.SettlementType.Health.CustomDamage",
        "Numerical.SettlementType.Health.DeathExecute",
        "Numerical.SettlementType.Health.DropEnvironmentDamage",
    }
)


class WeaponProtocolError(RuntimeError):
    """Raised when committed Weapon V2 data violates its lookup contract."""


def _pointer(parts: list[str]) -> str:
    encoded = [part.replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(encoded)


class WeaponProtocolResolver:
    """Resolve V2 weapon frontmatter against the committed local data lock."""

    LOCK_PATH = "data/weapon-data-lock.json"

    def __init__(self, repo_path: Path, max_file_bytes: int):
        self.repo_path = repo_path
        self.max_file_bytes = max_file_bytes

    def resolve(
        self,
        frontmatter: dict[str, Any],
        *,
        source_path: str,
        focus: str = "",
    ) -> dict[str, Any] | None:
        """Resolve one V2 weapon without reading refs, snapshots, or the network."""
        if frontmatter.get("schema_version") != 2:
            return None
        if not source_path.startswith("data/weapons/"):
            return None

        lock = self._read_lock()
        modes = frontmatter.get("game_modes")
        sources = frontmatter.get("damage_sources")
        if not isinstance(modes, list) or not modes:
            raise WeaponProtocolError("V2 weapon has no game_modes")
        if not isinstance(sources, list):
            raise WeaponProtocolError("V2 weapon has no damage_sources array")

        resolved_modes: dict[str, Any] = {}
        for mode in modes:
            if mode not in {"lc", "td"}:
                raise WeaponProtocolError(f"unsupported weapon mode: {mode}")
            resolved_modes[mode] = self._resolve_mode(
                frontmatter,
                sources,
                mode,
                lock,
                source_path,
                focus,
            )

        return {
            "kind": "weapon_protocol",
            "title": str(frontmatter.get("title") or Path(source_path).stem),
            "protocol": "weapon-numerical-v2",
            "schema_version": 2,
            "source_path": source_path,
            "lock_source_path": self.LOCK_PATH,
            "retrieval": "local_committed_lock",
            "modes": resolved_modes,
        }

    def _read_lock(self) -> dict[str, Any]:
        path = (self.repo_path / self.LOCK_PATH).resolve()
        if not path.is_relative_to(self.repo_path) or not path.is_file():
            raise WeaponProtocolError(f"missing committed lock: {self.LOCK_PATH}")
        if path.stat().st_size > self.max_file_bytes:
            raise WeaponProtocolError("committed weapon lock exceeds the read limit")
        try:
            lock = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WeaponProtocolError(f"cannot read committed weapon lock: {exc}") from exc
        if not isinstance(lock, dict) or lock.get("schema_version") != 1:
            raise WeaponProtocolError("unsupported Weapon Data Lock schema")
        return lock

    def _resolve_mode(
        self,
        weapon: dict[str, Any],
        source_defs: list[Any],
        mode: str,
        lock: dict[str, Any],
        source_path: str,
        focus: str,
    ) -> dict[str, Any]:
        by_id = {
            str(item.get("id")): item
            for item in source_defs
            if isinstance(item, dict) and item.get("id")
        }
        cache: dict[str, dict[str, Any] | None] = {}
        visiting: set[str] = set()

        def effective(source_id: str) -> dict[str, Any] | None:
            if source_id in cache:
                return cache[source_id]
            if source_id in visiting:
                raise WeaponProtocolError(f"damage source inheritance cycle: {source_id}")
            definition = by_id.get(source_id)
            if definition is None:
                raise WeaponProtocolError(f"missing inherited damage source: {source_id}")
            visiting.add(source_id)
            parent = None
            if definition.get("inherits"):
                parent = effective(str(definition["inherits"]))
            local = self._select_mode_source(definition, mode)
            if local is None:
                cache[source_id] = None
                visiting.remove(source_id)
                return None
            merged = self._merge_mechanical_source(parent, local, definition)
            cache[source_id] = merged
            visiting.remove(source_id)
            return merged

        resolved_sources: list[dict[str, Any]] = []
        for raw_definition in source_defs:
            if not isinstance(raw_definition, dict) or not raw_definition.get("id"):
                continue
            source_id = str(raw_definition["id"])
            mechanical = effective(source_id)
            if mechanical is None:
                continue
            resolved_sources.append(
                self._resolve_damage_source(
                    raw_definition,
                    mechanical,
                    mode,
                    lock,
                    source_path,
                )
            )

        main_source = next(
            (
                item["id"]
                for item in resolved_sources
                if item.get("section") == "fire_mode" and item.get("numerical")
            ),
            next(
                (item["id"] for item in resolved_sources if item.get("numerical")),
                None,
            ),
        )
        resolved_sources = self._select_focused_sources(
            resolved_sources,
            main_source,
            focus,
        )
        result: dict[str, Any] = {
            "main_source_id": main_source,
            "damage_sources": resolved_sources,
        }
        item = self._resolve_item(weapon.get("item_id"), mode, lock)
        if item:
            result["item"] = item
        active_skill = self._resolve_active_skill(weapon.get("active_skill_id"), lock)
        if active_skill:
            result["active_skill"] = active_skill
        return result

    @staticmethod
    def _select_focused_sources(
        sources: list[dict[str, Any]],
        main_source_id: str | None,
        focus: str,
    ) -> list[dict[str, Any]]:
        """Keep the protocol projection within tool budgets without guessing facts."""
        normalized = "".join(character.lower() for character in focus if character.isalnum())
        if not normalized:
            return sources
        selected: list[dict[str, Any]] = []
        for source in sources:
            identity = "".join(
                character.lower()
                for character in " ".join(
                    str(source.get(key) or "") for key in ("id", "name", "label")
                )
                if character.isalnum()
            )
            section = source.get("section")
            relevant = source.get("id") == main_source_id
            relevant = relevant or (identity and identity in normalized)
            relevant = relevant or (
                "技能" in normalized and section in {"skill", "special"}
            )
            relevant = relevant or ("近战" in normalized and section == "melee")
            relevant = relevant or (
                any(term in normalized for term in ("形态", "模式"))
                and section == "variant"
            )
            if relevant:
                selected.append(source)
        return selected or sources[:1]

    @staticmethod
    def _select_mode_source(definition: dict[str, Any], mode: str) -> dict[str, Any] | None:
        shared = definition.get("source")
        if isinstance(shared, dict):
            return shared
        mode_sources = definition.get("sources")
        if isinstance(mode_sources, dict) and isinstance(mode_sources.get(mode), dict):
            return mode_sources[mode]
        return None

    @staticmethod
    def _merge_mechanical_source(
        parent: dict[str, Any] | None,
        local: dict[str, Any],
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        merged = copy.deepcopy(parent) if parent else {"reference": {}, "override_chain": []}
        reference = dict(merged.get("reference", {}))
        for key in ("prototype_mode", "numerical", "asc_type_id", "feel_param_id"):
            if key in local:
                reference[key] = copy.deepcopy(local[key])
        if "asc_type_id" in local and "feel_param_id" not in local:
            reference["feel_param_id"] = local["asc_type_id"]
        elif reference.get("asc_type_id") and not reference.get("feel_param_id"):
            reference["feel_param_id"] = reference["asc_type_id"]
        merged["reference"] = reference
        for key in (
            "fire_interval",
            "attack_interval",
            "attack_count",
            "attack_interval_source",
            "pellets",
        ):
            if key in local:
                merged[key] = copy.deepcopy(local[key])
        if local.get("overrides") and local.get("override_reason"):
            merged.setdefault("override_chain", []).append(
                {
                    "source_id": str(definition.get("id")),
                    "reason": str(local["override_reason"]),
                    "overrides": copy.deepcopy(local["overrides"]),
                }
            )
        return merged

    def _resolve_damage_source(
        self,
        definition: dict[str, Any],
        mechanical: dict[str, Any],
        mode: str,
        lock: dict[str, Any],
        mdx_path: str,
    ) -> dict[str, Any]:
        source_id = str(definition["id"])
        result: dict[str, Any] = {
            "id": source_id,
            "name": str(definition.get("name") or source_id),
            "section": definition.get("section"),
        }
        if definition.get("label"):
            result["label"] = definition["label"]
        reference = mechanical.get("reference", {})
        provenance: list[dict[str, Any]] = [
            {"role": "declaration", "source_path": mdx_path}
        ]

        numerical_ref = reference.get("numerical")
        if isinstance(numerical_ref, dict):
            numerical_id = numerical_ref.get("id")
            level = numerical_ref.get("level")
            key = f"{mode}:{numerical_id}_{level}"
            namespace = f"numerical-{mode}"
            raw, pointer = self._lock_raw(lock, namespace, key)
            settlements = [
                item.get("TagName")
                for item in raw.get("Settlements", [])
                if isinstance(item, dict) and item.get("TagName")
            ]
            health_scale = raw.get("HpCalScale")
            health_base = raw.get("HpCalBase")
            damage = {
                "flesh": raw.get("FleshDamageBase"),
                "hurtable": raw.get("HurtableBase"),
                "impulse": raw.get("ImpulseBase"),
                "toughness": raw.get("ToughnessBase"),
            }
            damage_settlement = next(
                (
                    settlement
                    for settlement in settlements
                    if settlement in _DAMAGE_HEALTH_SETTLEMENTS
                ),
                None,
            )
            if damage_settlement and isinstance(health_scale, (int, float)):
                mode_base_attack = _MODE_BASE_ATTACK[mode]
                damage["base"] = health_scale * mode_base_attack
                damage["base_calculation"] = {
                    "health_scale": health_scale,
                    "mode_base_attack": mode_base_attack,
                    "formula": "HpCalScale * mode_base_attack",
                    "settlement": damage_settlement,
                }
            result["numerical"] = {
                "reference": {"id": numerical_id, "level": level, "table": mode},
                "settlements": settlements,
                "health": {
                    "scale": health_scale,
                    "base": health_base,
                },
                "damage": damage,
                "element_add_rate": raw.get("ElementAddRate"),
                "enable_critical": raw.get("bEnableCriticalDamage"),
                "enable_weakness": raw.get("EnableWeaknessDamage"),
                "weakness_damage_add_scale": raw.get("WeaknessDamageAddScale"),
                "ignore_shield": raw.get("bDamageIgnoreShield"),
            }
            provenance.append(
                {
                    "role": "numerical",
                    "source_path": self.LOCK_PATH,
                    "json_pointer": pointer,
                }
            )

        asc_id = reference.get("asc_type_id")
        if asc_id is not None:
            raw, pointer = self._lock_raw(lock, "asc", str(asc_id))
            interval = raw.get("FireIntervalBase")
            interval_source = "weapon_data_lock"
            for step in mechanical.get("override_chain", []):
                overrides = step.get("overrides", {})
                asc_override = overrides.get("asc", {}) if isinstance(overrides, dict) else {}
                if isinstance(asc_override, dict) and "fire_interval" in asc_override:
                    interval = asc_override["fire_interval"]
                    interval_source = "mdx_override"
            result["fire"] = {
                "interval_seconds": interval,
                "rounds_per_minute": 60 / interval
                if isinstance(interval, (int, float)) and interval > 0
                else None,
                "pellets": raw.get("SplinterNum"),
                "sub_fire_count": raw.get("SubFireCountPerShot"),
                "sub_fire_interval_seconds": raw.get("SubFireIntervalBase"),
                "pre_fire_seconds": raw.get("PreFireTimeBase"),
                "post_fire_seconds": raw.get("PostFireTimeBase"),
                "max_rpm_ratio": raw.get("MaxGunRPMRatio"),
                "source": interval_source,
            }
            result["ammo"] = {
                "magazine": raw.get("ClipAmmoCountBase"),
                "reserve": raw.get("MaxAmmoCount"),
                "infinite": raw.get("HaveInfinityAmmo"),
            }
            provenance.append(
                {
                    "role": "asc",
                    "source_path": self.LOCK_PATH,
                    "json_pointer": pointer,
                }
            )
        elif isinstance(mechanical.get("fire_interval"), (int, float)):
            interval = mechanical["fire_interval"]
            result["fire"] = {
                "interval_seconds": interval,
                "rounds_per_minute": 60 / interval if interval > 0 else None,
                "pellets": mechanical.get("pellets"),
                "source": "mdx_compatibility_fallback",
            }

        feel_id = reference.get("feel_param_id")
        if feel_id is not None:
            raw, pointer = self._lock_raw(lock, "feel", str(feel_id))
            result["handling"] = {
                "reload_time_base": raw.get("WeaponChangeClipTimeBase"),
                "aim_time_base": raw.get("WeaponAimTimeBase"),
                "switch_time_base": raw.get("WeaponSwitchTimeBase"),
            }
            provenance.append(
                {
                    "role": "feel",
                    "source_path": self.LOCK_PATH,
                    "json_pointer": pointer,
                }
            )

        if mechanical.get("attack_interval") is not None:
            result["attack"] = {
                "interval_seconds": mechanical["attack_interval"],
                "count": mechanical.get("attack_count"),
                "source": mechanical.get("attack_interval_source"),
                "note": "fixed-frequency attack; protocol does not convert this to RPM",
            }
        if mechanical.get("override_chain"):
            result["overrides"] = mechanical["override_chain"]
        result["provenance"] = provenance
        return result

    def _resolve_item(
        self,
        item_id: Any,
        mode: str,
        lock: dict[str, Any],
    ) -> dict[str, Any] | None:
        selected = item_id.get(mode) if isinstance(item_id, dict) else item_id
        if not selected:
            return None
        raw, pointer = self._lock_raw(lock, "item", str(selected))
        return {
            "id": str(selected),
            "name": raw.get("Name") or raw.get("ItemName"),
            "description": raw.get("Description"),
            "source_path": self.LOCK_PATH,
            "json_pointer": pointer,
        }

    def _resolve_active_skill(
        self,
        skill_id: Any,
        lock: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(skill_id, int) or skill_id <= 0:
            return None
        selection_key = f"{skill_id}_1"
        selection = lock.get("active_skills", {}).get(selection_key)
        if not isinstance(selection, dict):
            raise WeaponProtocolError(f"missing active skill selection: {selection_key}")
        source = selection.get("source")
        source_key = str(selection.get("source_key"))
        namespace = "skill-pve" if source == "weapon_pve" else "gp-active-skill"
        raw, pointer = self._lock_raw(lock, namespace, source_key)
        if source == "weapon_pve":
            charge_time = raw.get("ChargeNeedTime")
            charge_count = raw.get("SkillCount")
        else:
            charge_time = raw.get("CooldownDuration")
            charge_count = raw.get("MaxChargeStackCount")
        return {
            "id": skill_id,
            "level": 1,
            "selection": source,
            "charge_time_seconds": charge_time,
            "charge_count": charge_count,
            "source_path": self.LOCK_PATH,
            "json_pointer": pointer,
        }

    def _lock_raw(
        self,
        lock: dict[str, Any],
        namespace: str,
        key: str,
    ) -> tuple[dict[str, Any], str]:
        rows = lock.get("rows", {}).get(namespace, {})
        row = rows.get(key) if isinstance(rows, dict) else None
        raw = row.get("raw") if isinstance(row, dict) else None
        if not isinstance(raw, dict):
            raise WeaponProtocolError(f"missing lock row: {namespace}/{key}")
        return raw, _pointer(["rows", namespace, key, "raw"])
