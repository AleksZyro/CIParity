# Contributing

Start a focused branch from `main`. Read [the implementation plan](PLAN.md) before editing. Use Python 3.11 or newer in a virtual environment.

```sh
python -m pip install -e ".[dev]"
pytest -q
ruff check src tests
ruff check .
ruff format --check .
python -m mypy
python -m build
```

For parser or diagnostic changes, add an example that exercises both the supported case and an inconclusive case. Exercise the public CLI when changing reports or exit codes. Keep private data, real credentials, local environments and files under `data/` out of commits.

Describe the comparison scope, source of evidence and platforms actually tested in your pull request. Preserve copyright and provenance information.
