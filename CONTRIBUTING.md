# Contributing to diff.lab

Thanks for your interest in diff.lab — a self-hosted viewer for uncommitted
working-tree diffs across machines, the thing Gitea and cgit don't show.

## Ground rules

- **Read-only git operations only.** The two allowed operations are
  `git diff` and `git status --short`. No operation that writes to a repo
  will be merged.
- **`shell=False` everywhere.** All subprocess calls must pass arguments as
  a list, never a shell string. Repo paths are passed as discrete `-C` arguments
  or quoted for the SSH remote; no shell interpolation.
- **Autoescape on.** All diff/status output is HTML-escaped before rendering;
  Jinja2 autoescape must remain enabled.

## Development setup

You need Python 3.12+ and a virtual environment.

```sh
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
# edit config.yaml to add at least one target
python -m difflab
```

Run the test suite:

```sh
pytest
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs `pytest` on
every push and pull request.

## Code style

- Match surrounding code density and naming conventions.
- `shell=False` is not optional — see ground rules.
- Keep the three layers clean: config parsing, git operations, HTTP views.

## Tests

- Unit tests live in `tests/`. Add a test for any new behavior.
- A change that alters responses should update expectations, not loosen them.
- `conftest.py` has shared fixtures; keep them generic (no real hostnames or
  paths).
