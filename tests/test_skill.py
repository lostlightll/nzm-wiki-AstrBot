"""Contract checks for the bundled AstrBot nzm-wiki Skill."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


class NzmWikiSkillTest(unittest.TestCase):
    """Keep policy in the Skill and source routing in the interpreter."""

    def test_skill_is_thin_and_declares_required_answer_checks(self) -> None:
        skill_path = Path(__file__).parents[1] / "skills" / "nzm-wiki" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        _, header, body = text.split("---", 2)
        metadata = yaml.safe_load(header)

        self.assertEqual(metadata["name"], "nzm-wiki")
        self.assertIn("nzm_wiki_query", body)
        self.assertIn("weapon_skills", body)
        self.assertIn("modifier_protocol", body)
        self.assertIn("主动技能", body)
        self.assertIn("被动技能", body)
        self.assertIn("乘区", body)

        # Internal source selection belongs to Python protocol resolvers.
        self.assertNotIn("modifier-index-runtime.json", body)
        self.assertNotIn("num-modifier-lock.json", body)
        self.assertNotIn("weapon-data-lock.json", body)


if __name__ == "__main__":
    unittest.main()
