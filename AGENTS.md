# Project instructions

- This repository contains an AstrBot plugin for stateless nzm-wiki queries.
- The plugin must treat the configured nzm-wiki repository as read-only.
- Do not add persistent indexes, caches, databases, task workspaces, or sync jobs.
- Keep the interpreter independent from AstrBot so it can be tested directly.
- Parse MDX statically. Never execute MDX JavaScript or repository scripts.
- Preserve source provenance in every returned evidence item.
- Run `python -m unittest discover -s tests -v` after Python changes.
- Run `ruff check .` when Ruff is available.

