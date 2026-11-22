# CIParity

CIParity helps you understand why your project behaves differently locally and in GitHub Actions. Its offline CLI compares local observations with one declared job and matrix variant. Each finding identifies its evidence, possible impact and a next check.

## Quickstart

From a source checkout or an unpacked source distribution, create and activate a virtual environment with Python 3.11 or newer. Install CIParity from that directory.

```sh
python -m pip install -e .
ciparity --version
ciparity inspect ci --root examples/basic
```

The example workflow has three variants after its exclusion rule.

| Variant | Runner | Python selector |
| --- | --- | --- |
| 1 | ubuntu-latest | 3.11 |
| 2 | ubuntu-latest | 3.12 |
| 3 | windows-latest | 3.12 |

For a local test launched from the project root in PowerShell 7 with `python -m pytest`, describe that invocation explicitly.

```sh
ciparity doctor --root examples/basic --job test --variant 1 --local-shell pwsh --local-workdir . --local-command "python -m pytest"
```

The report shows a confirmed shell mismatch for the test step. Its explicit CI shell is Bash. The working directory and test invocation match. Python remains unknown until you identify the project interpreter. If CIParity is running in that interpreter, add `--use-current-python`. A local 3.11.9 matches the declared 3.11 selector at minor-version precision. The CI patch version is still unknown.

## Commands

| Command | Purpose |
| --- | --- |
| `ciparity --help` / `--version` | Show commands or the installed version |
| `ciparity inspect local` | Inspect the host, running Python, environment-name presence, tools and dependency files |
| `ciparity inspect ci` | List workflow paths, job IDs and numbered variants |
| `ciparity compare --job test` | Compare one unambiguous job and variant |
| `ciparity doctor` | Order differences and unresolved questions, then show concrete next checks |
| `ciparity report --format json` | Emit the comparison as a versioned JSON document |

All analysis commands accept `--root PATH` and `--format text|json`. CI commands accept `--workflow PATH`, relative to the root or absolute inside it. Comparison, doctor and report share `--job`, `--variant N`, `--verbose`, `--use-current-python`, `--local-shell`, `--local-workdir` and `--local-command`. Omit selectors for a single job and variant. With several candidates, use the numbers from `inspect ci` on the same checkout.

```sh
ciparity inspect local --format json
ciparity inspect ci --workflow .github/workflows/ci.yml
ciparity compare --root examples/basic --job test --variant 3 --verbose
ciparity report --root examples/basic --job test --variant 1 --format json > report.json
```

## What the inspectors cover

**Local host.** CIParity targets Windows, Linux and macOS. It records OS family and release, machine architecture, directory separator, current directory, Python implementation, executable, version and virtual-environment state. The shell hint is restricted to recognised names. The actual test shell, test working directory and project interpreter need explicit local context. Filesystem case sensitivity stays unknown.

The tool inventory checks absolute host PATH entries for Git, Python, pip, uv, Node.js, npm, Docker and pytest. Repository executables are excluded. Add `--tool-versions` to `inspect local` to probe recognised native Git, Node.js, Docker and uv binaries. Other versions can remain unknown.

The local inspector inventories Python and Node manifests and lockfiles. For PEP 621 `pyproject.toml` metadata it counts static direct dependencies and checks `requires-python` against an explicitly identified interpreter. Dynamic dependency counts stay unknown. A conventional filename or a version constraint cannot establish which packages a CI job installed.

**GitHub Actions.** The inspector reads both YAML extensions, event names, job IDs and names, runner labels, job dependencies, conditions, run defaults, environment scopes and ordered action/run steps. JSON inspection includes safe step IDs, recognised command types and direct references to known step IDs in supported expression fields. Conditions are recorded as present. Display names and raw expressions stay private.

| Declaration | Interpretation |
| --- | --- |
| `runs-on` | Recognised Ubuntu, Windows and macOS labels give an OS family. A moving label leaves the image release open. Self-hosted, custom label sets and runner groups have unknown machine properties. A container keeps its execution environment separate from its host. |
| `strategy.matrix` | Scalar axes, Cartesian products, partial exclusions and ordered inclusions. Includes can extend surviving original combinations or create an additional variant. |
| `actions/setup-python` | Quoted numeric major, minor and patch selectors or a direct matrix reference. Each setup step has its own finding. |
| `defaults.run`, `shell`, `working-directory` | Workflow defaults, job overrides and step overrides. An unnamed hosted Windows shell resolves to pwsh, a container default to sh. An unnamed Unix shell remains unknown because it permits fallback. Portable project-relative directory declarations are comparable. |
| `env` | Names and local presence only. Each finding cites the first effective declaration. Dynamic environment mappings are marked in the report. |
| `uses`, `run` | Recognised action boundaries and a small set of whole commands. Reusable workflows and opaque actions remain visible. Arbitrary scripts stay private. |

Recognised commands are `pytest`, `python -m pytest`, `uv run pytest`, `uv sync`, `npm ci`, `pip install -r requirements.txt`, `python -m pip install -r requirements.txt` and `python -m pip install -e .`. CIParity compares invocations when their purpose matches. Extra flags, pipelines and multiline scripts require manual inspection.

With `--local-command`, the local shell and directory apply only to recognised CI invocations of the same purpose. Without a local command they supply a baseline for each run step.

The version recogniser deliberately requires quoted string selectors. Numeric YAML values can lose precision, for example an unquoted `3.10`. Only an entire `${{ matrix.axis }}` expression resolves. Nested object axes, expressions that compute matrices, arbitrary expression evaluation, action expansion, Docker image inspection, installed dependency resolution, CI logs and observed runtime data are outside version 0.1.

## Reading a report

JSON has the top-level fields `schema_version`, `tool_version`, `kind` and `data`. A comparison includes its workflow, job, one-based variant, runner declaration, notes, counts and ordered findings. Finding IDs are stable within a given workflow revision and variant. `null` represents unavailable evidence. There are no timestamps or generated UUIDs. Identical inputs and observations produce the same report.

| Status | Interpretation |
| --- | --- |
| `confirmed_match` | Both sides support equality at the stated scope or selector precision. |
| `confirmed_mismatch` | The observed or supplied local property differs from a comparable declaration. |
| `potential_mismatch` | A discrepancy merits checking, such as a named variable absent from the current process. |
| `unknown` | Evidence on one or both sides is insufficient. |
| `unsupported` | The construct falls outside the static inspector. |

Confirmed differences receive warning severity. Other findings are informational. Doctor prioritises confirmed differences, potential differences, unsupported items, unknowns and matches, in that order. Severity indicates an actionable configuration difference. It does not estimate the chance that the difference caused a failure.

| Exit | Meaning |
| --- | --- |
| 0 | Successful inspection, or comparison with zero confirmed differences. Check counts for unknown or unsupported findings. |
| 1 | Successful comparison with at least one confirmed difference. The report is valid. |
| 2 | Invalid, ambiguous or inaccessible input. Details go to stderr. |
| 3 | The selected analysis exceeds a limit or needs unsupported configuration. CI inventory can still list jobs and explain their limitations. An opaque step alone remains a finding inside a usable comparison. |
| 4 | Unexpected internal error. |

## Security and limits

The core works offline. Workflow commands, actions, project scripts and package installations are never executed by inspection. YAML is processed as events with bounded depth and node counts. Explicit tags, anchors, aliases and duplicate keys are rejected. Each input file is limited to 256 KiB. Discovery permits up to 32 workflows and expansion permits up to 256 variants per job.

Reports expose useful identifiers and known semantic values through a defined projection. Environment values, raw commands, arbitrary matrix values, dependency URLs and source snippets are excluded. Local inspection includes machine paths. See the [security policy](SECURITY.md) for the trust boundary and private issue reporting.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
ruff format --check .
python -m mypy
python -m build
```

The package keeps local inspection, bounded file access, YAML parsing, matrix expansion, comparison rules and reporting in separate modules under `src/ciparity/`. The CLI shares the comparison engine across its three report commands. Tests exercise the installed CLI, JSON and text output, safe failure modes and the example repository. The GitHub Actions workflow defines tests, linting, type checks and packaging on Windows, Ubuntu and macOS with Python 3.11 and 3.14.

Contributions are welcome under the existing MIT licence. [Contributing guide](CONTRIBUTING.md) · [MIT licence](LICENSE)
