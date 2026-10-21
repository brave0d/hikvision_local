# Notes for coding agents

Conventions for automated contributors to this repository. People should read
[CONTRIBUTING.md](CONTRIBUTING.md) first; everything there applies here too.

## Layout

- `custom_components/hikvision_local/`: the integration. `isapi/` is the device client and has no
  Home Assistant imports, so it can be used and tested on its own.
- `tests/fixtures/devices/*.json`: recorded diagnostics of real devices, one file per model. The
  device tests replay every GET response from these files.
- `tests/fixtures/ISAPI/<endpoint>/*.xml`: single responses for ISAPI client tests.

## Environment

- Python 3.14, Home Assistant 2026.10 or later (minimum in `hacs.json`).
- `pip install -r requirements.test.txt`, then `pytest`, `ruff check custom_components tests` and
  `ruff format --check custom_components tests`. CI runs the same commands.

## Rules

- Don't set `entity_id` on entities. Use `has_entity_name` and translation keys; Home Assistant
  generates the IDs.
- Don't change a `unique_id` format without a registry migration.
- Every change ships with tests. Tests fail when Home Assistant logs "Error adding entity" or a
  deprecation report for this integration (see `tests/conftest.py`), so fix those instead of
  silencing them.
- New fixtures must be anonymised: serials, MAC and IP addresses, device IDs, names and locations.
- New ISAPI endpoints go into the diagnostics output too.
- One change per commit with a semantic prefix (`feat`, `fix`, `refactor`, `test`, `chore`,
  `docs`). Pull requests are squash-merged.
- Ported code keeps its author: cherry-pick the original commit, or add a `Co-authored-by` trailer
  when the change had to be rewritten, and link the source in the pull request.
