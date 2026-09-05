# AstrBot nzm-wiki plugin

Stateless AstrBot tools for querying a locally cloned `nzm-wiki` repository.

The plugin does not create an index, cache, database, workspace, or synchronized
copy. Each request reads the configured repository snapshot, interprets relevant
MDX, follows ID-like references into JSON, returns a source-grounded evidence
bundle, and discards all request-local data.

The plugin never fetches the Wiki over HTTP and never returns remote citation
URLs. Evidence uses repository-relative `source_path` values, JSON Pointers, and
the local clone's Git revision.

## Tools

- `nzm_wiki_query`: searches raw MDX, Markdown, and JSON and returns relevant
  interpreted evidence.
- `nzm_wiki_read`: reads one exact `source_path` from a previous query and
  resolves related JSON records.

MDX is parsed statically. Literal component properties and common components
such as `GameMode`, `ActiveSkill`, `PassiveSkill`, and `LevelTable` are rendered
into readable text. JavaScript expressions and repository scripts are never
executed.

Weapon files using `schema_version: 2` follow the repository's active protocol:
MDX owns source selection, `data/weapon-data-lock.json` owns committed raw facts,
and the interpreter projects LC/TD sources before deriving fields such as RPM.
It does not use `refs/`, migration reports, or migration snapshots as published
runtime facts.

The interpreter also owns knowledge routing. Weapon results contain a complete
`weapon_skills` projection, and damage-modifier questions receive a
`modifier_protocol` projection that joins the provider, exact numerical row,
modifier type, and damage factor. The LLM is never expected to choose internal
JSON files or reconstruct factor mappings itself.

For registered damage settlements, Weapon Numerical V2 also projects
`damage.base` from `HpCalScale` using the mode base attack (500 for LC and 400
for TD). Recovery settlements retain ratio semantics and are not converted into
damage.

`ElementAddRate` is projected as `element_status_application`: a per-attack
probability of applying an elemental buff. It is explicitly marked as not being
a damage multiplier and must never be included in base-damage or DPS formulas.

## Skill

The plugin bundles the AstrBot Skill `skills/nzm-wiki/SKILL.md`. It defines only
answering obligations: use local evidence, include active/passive skills for a
weapon, and require a resolved factor for damage-increase questions. Source
routing and protocol interpretation remain in the interpreter layer.

## Deployment

Mount both this plugin and the raw Wiki repository into the AstrBot container:

```yaml
services:
  astrbot:
    volumes:
      - ../astrbot-plugin-nzm-wiki:/AstrBot/data/plugins/astrbot_plugin_nzm_wiki:ro
      - ../nzm-wiki:/knowledge/nzm-wiki:ro
```

The default plugin configuration reads `/knowledge/nzm-wiki/data`. Change
`repo_path` or `source_dirs` in the AstrBot plugin settings when the mount layout
differs.

## Validation

```bash
python3 -m unittest discover -s tests -v
```
