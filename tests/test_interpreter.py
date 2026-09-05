"""Tests for stateless raw Wiki interpretation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from interpreter import NzmWikiInterpreter, WikiQueryError


class NzmWikiInterpreterTest(unittest.TestCase):
    """Exercise MDX parsing, JSON references, and read boundaries."""

    def setUp(self) -> None:
        """Create one disposable raw repository fixture."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        (self.repo / "data" / "weapons").mkdir(parents=True)
        (self.repo / "data" / "perks").mkdir(parents=True)
        (self.repo / "data" / "guides").mkdir(parents=True)
        (self.repo / "data" / "weapons" / "测试武器.mdx").write_text(
            """---
title: 测试武器
release_date: 2026-09-03
item_id: "20101000024"
damage_sources:
  - source:
      numerical:
        id: 120100242
---
<WeaponSkill>
  <ActiveSkill name="秘法榴弹">
    <GameMode only="lc">造成 150% 攻击力伤害。</GameMode>
  </ActiveSkill>
</WeaponSkill>

<LevelTable
  headers={["等级", "伤害"]}
  data={[
    [1, 100],
    [2, 150],
  ]}
/>
""",
            encoding="utf-8",
        )
        (self.repo / "data" / "weapons" / "协议武器.mdx").write_text(
            """---
title: 协议武器
schema_version: 2
game_modes: [lc, td]
prototype_id: "20005000034"
use_type: 主武器
weapon_type: 冲锋枪
element: 火焰
rarity: 传说
damage_sources:
  - id: normal-shot
    name: 普通射击
    section: fire_mode
    source:
      numerical: { id: 120500340, level: 1 }
      asc_type_id: "339"
  - id: healing-skill
    name: 恢复技能
    section: skill
    source:
      numerical: { id: 120500341, level: 1 }
---
<WeaponSkill>
  <ActiveSkill name="协议主动">
    切换武器形态。
  </ActiveSkill>
  <PassiveSkill name="协议增伤">
    命中后武器伤害提高 25%。
  </PassiveSkill>
</WeaponSkill>

<WeaponModeDiff />
""",
            encoding="utf-8",
        )
        (self.repo / "data" / "weapon-data.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "item_id": "20101000024",
                            "numerical_id": 120100242,
                            "base_value": 150,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.repo / "data" / "perks" / "perks.json").write_text(
            json.dumps(
                [{"id": "perk-1", "name": "快速换弹", "description": "换弹速度提高"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.repo / "data" / "weapon-data-lock.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "rows": {
                        "numerical-lc": {
                            "lc:120500340_1": {
                                "raw": {
                                    "HpCalScale": 0.07,
                                    "HpCalBase": 0,
                                    "FleshDamageBase": 0.6,
                                    "HurtableBase": 0.6,
                                    "ImpulseBase": 0.8,
                                    "ToughnessBase": 0.8,
                                    "ElementAddRate": 0.051,
                                    "EnableWeaknessDamage": True,
                                    "bEnableCriticalDamage": True,
                                    "WeaknessDamageAddScale": 0.3,
                                    "bDamageIgnoreShield": False,
                                    "Settlements": [
                                        {"TagName": "Numerical.SettlementType.Health.WeaponDamage"}
                                    ],
                                }
                            },
                            "lc:120500341_1": {
                                "raw": {
                                    "HpCalScale": 0.1,
                                    "HpCalBase": 5,
                                    "Settlements": [
                                        {
                                            "TagName": (
                                                "Numerical.SettlementType.Health."
                                                "CharStandardHealing"
                                            )
                                        }
                                    ],
                                }
                            }
                        },
                        "numerical-td": {
                            "td:120500340_1": {
                                "raw": {
                                    "HpCalScale": 0.08,
                                    "HpCalBase": 0,
                                    "FleshDamageBase": 0.7,
                                    "HurtableBase": 0.7,
                                    "ImpulseBase": 0.9,
                                    "ToughnessBase": 0.9,
                                    "ElementAddRate": 0.061,
                                    "EnableWeaknessDamage": True,
                                    "bEnableCriticalDamage": True,
                                    "WeaknessDamageAddScale": 0.3,
                                    "bDamageIgnoreShield": False,
                                    "Settlements": [
                                        {"TagName": "Numerical.SettlementType.Health.WeaponDamage"}
                                    ],
                                }
                            },
                            "td:120500341_1": {
                                "raw": {
                                    "HpCalScale": 0.1,
                                    "HpCalBase": 5,
                                    "Settlements": [
                                        {
                                            "TagName": (
                                                "Numerical.SettlementType.Health."
                                                "CharStandardHealing"
                                            )
                                        }
                                    ],
                                }
                            }
                        },
                        "asc": {
                            "339": {
                                "raw": {
                                    "FireIntervalBase": 0.045,
                                    "SplinterNum": 1,
                                    "SubFireCountPerShot": 1,
                                    "SubFireIntervalBase": 0,
                                    "ClipAmmoCountBase": 100,
                                    "MaxAmmoCount": 1700,
                                    "HaveInfinityAmmo": False,
                                }
                            }
                        },
                        "feel": {"339": {"raw": {"WeaponChangeClipTimeBase": 1.36}}},
                        "item": {},
                        "skill-pve": {},
                        "gp-active-skill": {},
                    },
                    "active_skills": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.repo / "data" / "modifier-index-runtime.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "attributeTypes": [
                        {
                            "id": "weapon-damage",
                            "quantity": "ratio",
                            "facets": {
                                "increase": {
                                    "id": "weapon-damage",
                                    "consumer": "damage",
                                }
                            },
                        }
                    ],
                    "providers": [
                        {
                            "id": "weapon:协议武器:协议增伤",
                            "label": "协议武器·协议增伤",
                            "source": {
                                "type": "weapon",
                                "slug": "协议武器",
                                "skillName": "协议增伤",
                            },
                            "effects": [
                                {
                                    "row": "lc:120500340_1_0",
                                    "attributeTypeId": "weapon-damage",
                                    "attributeLabel": "武器伤害增加",
                                    "direction": "increase",
                                    "recipient": "self",
                                    "facetIds": ["weapon-damage"],
                                    "reviewed": True,
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.repo / "data" / "guides" / "multiplier.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "factors": [{"id": "dilution", "label": "大稀释乘区"}],
                    "dilutionCategories": [
                        {"id": "weapon-damage", "target": "武器伤害增加"}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.repo / "data" / "num-modifier-lock.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "rows": {
                        "lc": {
                            "120500340_1_0": {
                                "raw": {
                                    "AttributeName": (
                                        "GPAttributeSetGiveDamageRatio.WeaponDamageRatio"
                                    ),
                                    "BaseValue": 0.25,
                                    "CoefValue": 0,
                                    "Description": "协议武器被动增伤",
                                    "GPModifierOp": "B1",
                                    "ID": 120500340,
                                    "Level": 1,
                                }
                            }
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.interpreter = NzmWikiInterpreter(
            str(self.repo),
            source_dirs=["data"],
        )

    def tearDown(self) -> None:
        """Release the disposable fixture."""
        self.temp_dir.cleanup()

    def test_query_interprets_mdx_and_resolves_json_references(self) -> None:
        """MDX evidence includes semantic components and linked JSON records."""
        result = self.interpreter.query("测试武器秘法榴弹伤害", max_results=6)

        self.assertTrue(result["stateless"])
        mdx = next(item for item in result["evidence"] if item["kind"] == "mdx")
        self.assertEqual(mdx["title"], "测试武器")
        self.assertEqual(mdx["frontmatter"]["release_date"], "2026-09-03")
        self.assertIn("主动技能: 秘法榴弹", mdx["content"])
        self.assertIn("游戏模式: lc", mdx["content"])
        self.assertIn("| 等级 | 伤害 |", mdx["content"])
        related = [item for item in result["evidence"] if item["kind"] == "json"]
        self.assertTrue(related)
        self.assertIn("120100242", json.dumps(related, ensure_ascii=False))

    def test_json_only_entity_can_be_queried(self) -> None:
        """Knowledge stored only in JSON remains discoverable."""
        result = self.interpreter.query("快速换弹有什么效果", max_results=4)

        serialized = json.dumps(result["evidence"], ensure_ascii=False)
        self.assertIn("快速换弹", serialized)
        self.assertIn("换弹速度提高", serialized)

    def test_weapon_v2_uses_local_lock_and_derives_rpm(self) -> None:
        """V2 weapons resolve mode-isolated Lock rows without remote URLs."""
        result = self.interpreter.query("协议武器射速", max_results=6)

        self.assertEqual(result["retrieval"], "local_repository_only")
        protocol = next(
            item for item in result["evidence"] if item["kind"] == "weapon_protocol"
        )
        self.assertEqual(protocol["retrieval"], "local_committed_lock")
        lc = protocol["modes"]["lc"]["damage_sources"][0]
        td = protocol["modes"]["td"]["damage_sources"][0]
        self.assertAlmostEqual(lc["fire"]["rounds_per_minute"], 1333.3333333333335)
        self.assertEqual(lc["numerical"]["health"]["scale"], 0.07)
        self.assertEqual(td["numerical"]["health"]["scale"], 0.08)
        self.assertEqual(
            lc["numerical"]["element_status_application"],
            {
                "source_field": "ElementAddRate",
                "probability_per_attack": 0.051,
                "semantics": "probability_to_apply_element_buff_on_each_attack",
                "is_damage_multiplier": False,
                "display_probability": "5.1%",
            },
        )
        self.assertEqual(
            td["numerical"]["element_status_application"]["display_probability"],
            "6.1%",
        )
        self.assertNotIn("element_add_rate", lc["numerical"])
        self.assertEqual(lc["numerical"]["damage"]["base"], 35)
        self.assertEqual(td["numerical"]["damage"]["base"], 32)
        self.assertEqual(
            lc["numerical"]["damage"]["base_calculation"]["mode_base_attack"],
            500,
        )
        self.assertEqual(
            td["numerical"]["damage"]["base_calculation"]["mode_base_attack"],
            400,
        )
        self.assertEqual(
            lc["provenance"][1]["json_pointer"],
            "/rows/numerical-lc/lc:120500340_1/raw",
        )
        mdx = next(item for item in result["evidence"] if item["kind"] == "mdx")
        self.assertEqual(mdx["weapon_skills"]["active"][0]["name"], "协议主动")
        self.assertEqual(mdx["weapon_skills"]["passive"][0]["name"], "协议增伤")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("source_url", serialized)
        self.assertNotIn("http://", serialized)
        self.assertNotIn("https://", serialized)

    def test_recovery_health_scale_is_not_converted_to_damage(self) -> None:
        """Recovery settlements retain ratio semantics rather than mode damage."""
        result = self.interpreter.query("协议武器恢复技能", max_results=6)
        protocol = next(
            item for item in result["evidence"] if item["kind"] == "weapon_protocol"
        )

        for mode in ("lc", "td"):
            healing = next(
                source
                for source in protocol["modes"][mode]["damage_sources"]
                if source["id"] == "healing-skill"
            )
            self.assertEqual(healing["numerical"]["health"]["scale"], 0.1)
            self.assertNotIn("base", healing["numerical"]["damage"])

    def test_modifier_intent_resolves_value_and_factor_inside_interpreter(self) -> None:
        """Damage questions receive a structured factor without LLM path selection."""
        result = self.interpreter.query("协议武器增伤在哪个乘区", max_results=6)

        protocol = next(
            item for item in result["evidence"] if item["kind"] == "modifier_protocol"
        )
        effect = protocol["providers"][0]["effects"][0]
        self.assertEqual(effect["factors"][0]["label"], "大稀释乘区")
        self.assertEqual(effect["factors"][0]["modifier_type"], "武器伤害增加")
        self.assertEqual(effect["numerical"]["display_value"], "+25%")
        self.assertEqual(effect["numerical"]["operation_semantics"], "additive_delta")
        self.assertEqual(
            effect["numerical"]["json_pointer"],
            "/rows/lc/120500340_1_0/raw",
        )

    def test_each_call_reads_current_raw_source_without_writing(self) -> None:
        """A second call sees source changes and creates no repository files."""
        before = {path.relative_to(self.repo) for path in self.repo.rglob("*")}
        first = self.interpreter.query("测试武器", max_results=2)
        weapon = self.repo / "data" / "weapons" / "测试武器.mdx"
        weapon.write_text(
            weapon.read_text(encoding="utf-8").replace("150%", "175%"),
            encoding="utf-8",
        )
        second = self.interpreter.query("测试武器", max_results=2)
        after = {path.relative_to(self.repo) for path in self.repo.rglob("*")}

        self.assertIn("150%", json.dumps(first, ensure_ascii=False))
        self.assertIn("175%", json.dumps(second, ensure_ascii=False))
        self.assertEqual(before, after)

    def test_read_rejects_path_outside_configured_sources(self) -> None:
        """Exact reads cannot escape the configured read-only source boundary."""
        outside = self.repo / "secret.md"
        outside.write_text("secret", encoding="utf-8")

        with self.assertRaises(WikiQueryError):
            self.interpreter.read("secret.md")
        with self.assertRaises(WikiQueryError):
            self.interpreter.read("../secret.md")


if __name__ == "__main__":
    unittest.main()
