from dataclasses import asdict

import pytest

from ciparity.compare import LocalContext, compare, select_job, select_variant
from ciparity.local import inspect_local
from ciparity.models import InputError, Status, Unsupported
from ciparity.reporting import ci_view
from ciparity.semantics import ci_environment, compare_python, condition_value, shell_for
from ciparity.workflows import parse_workflow


@pytest.mark.parametrize(
    ("actual", "requested", "status"),
    [
        ("3.12.4", "3.12", Status.MATCH),
        ("3.12.4", "3.12.3", Status.DIFFERENCE),
        ("3.10.12", "3.1", Status.DIFFERENCE),
        ("3.11.9", "3", Status.MATCH),
        (None, "3.12", Status.UNKNOWN),
        ("3.13.0rc1", "3.13", Status.UNKNOWN),
        ("3.12.0", ">=3.12", Status.UNKNOWN),
        ("3.12.0", None, Status.UNKNOWN),
    ],
)
def test_python_version_components(actual, requested, status):
    assert compare_python(actual, requested) is status


def job_for(runner="ubuntu-latest", extra=""):
    return parse_workflow(
        "on: push\njobs:\n  test:\n    runs-on: "
        + runner
        + "\n"
        + extra
        + "    steps:\n      - run: pytest\n",
        "ci.yml",
    ).jobs[0]


@pytest.mark.parametrize(
    ("label", "family", "arch", "shell"),
    [
        ("macos-latest", "macOS", None, None),
        ("macOS-latest", "macOS", None, None),
        ("ubuntu-22.04", "Linux", "x64", None),
        ("windows-2022", "Windows", "x64", "pwsh"),
        ("ubuntu-24.04-arm", "Linux", "arm64", None),
        ("[self-hosted, linux, x64]", None, None, None),
        ("ubuntu-custom", None, None, None),
        ("'${{ github.ref }}'", None, None, None),
    ],
)
def test_runner_families(label, family, arch, shell):
    job = job_for(label)
    ci = ci_environment(job, {})
    assert (ci.os, ci.architecture) == (family, arch)
    assert shell_for(job.steps[0], ci, {}) == shell


def test_container_hides_host_properties():
    job = job_for(extra="    container: python:3.11\n")
    ci = ci_environment(job, {})
    assert ci.container and ci.runner == "ubuntu-latest" and ci.os is None
    assert shell_for(job.steps[0], ci, {}) == "sh"


def test_job_and_matrix_ambiguity():
    wf = parse_workflow("on: push\njobs:\n  test: {uses: './called.yml'}", "ci.yml")
    with pytest.raises(InputError, match="Ambiguous"):
        select_job([wf, wf], "test")
    with pytest.raises(Unsupported, match="Reusable"):
        select_variant(wf.jobs[0], None)
    job = job_for(extra="    strategy: {matrix: {py: ['3.10', '3.11']}}\n")
    with pytest.raises(InputError, match="2 variants"):
        select_variant(job, None)
    assert select_variant(job, 2) == (2, {"py": "3.11"})
    with pytest.raises(InputError, match="1..2"):
        select_variant(job, 0)


def test_evidence_and_context(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_FLAG", raising=False)
    wf = parse_workflow(
        """
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    defaults: {run: {shell: bash}}
    steps:
      - uses: actions/setup-python@v6
        with: {python-version: '3.12'}
      - run: python -m pytest
        env: {APP_FLAG: literal}
      - run: pip install -r requirements.txt
        working-directory: subdir
      - uses: ./private-action
""",
        "ci.yml",
    )
    local = inspect_local(tmp_path, env_names={"APP_FLAG"})
    local.os = "Windows"
    result = compare(local, wf, wf.jobs[0], None, LocalContext("pwsh", ".", "pytest"))
    findings = {v.id: v for v in result.findings}
    for key in ("os", "shell.2", "command.2"):
        assert findings[key].status == Status.DIFFERENCE and findings[key].severity == "warning"
    assert findings["directory.2"].status == Status.MATCH
    assert findings["directory.3"].status == Status.UNKNOWN
    assert findings["shell.3"].status == Status.UNKNOWN
    baseline = compare(local, wf, wf.jobs[0], None, LocalContext(directory="."))
    assert next(f for f in baseline.findings if f.id == "directory.3").status == Status.DIFFERENCE
    assert findings["python.1"].status == Status.UNKNOWN
    assert findings["command.3"].status == Status.UNKNOWN
    assert findings["env.APP_FLAG"].status == Status.POTENTIAL
    assert findings["action.4"].status == Status.UNSUPPORTED
    assert findings["os"].local.source == "local_observation"
    assert findings["os"].ci.source == "declared_ci"
    assert all(f.reason and f.possible_impact and f.next_check for f in findings.values())
    assert "literal" not in str(asdict(result))


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (False, False),
        (None, True),
        ("${{ true }}", True),
        ("matrix.os == 'LINUX'", True),
        ("${{ matrix.os != 'linux' }}", False),
        ("matrix.missing == ''", None),
        ("matrix.number == '1'", None),
        ("success() && matrix.os == 'linux'", None),
        ("matrix.os == 'é'", None),
    ],
)
def test_condition_subset(condition, expected):
    assert condition_value(condition, {"os": "linux", "number": 1}) is expected


def test_conditional_steps_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAG", "value")
    wf = parse_workflow(
        """
on: push
jobs:
  test:
    runs-on: macOS-latest
    strategy: {matrix: {arch: [ppc64le, arm64]}}
    steps:
      - if: matrix.arch != 'ppc64le'
        uses: actions/setup-python@v6
        with: {python-version: '3.11'}
      - if: matrix.arch == 'ARM64'
        run: pytest
        shell: pwsh
      - if: failure()
        env: {FLAG: some-value}
        run: pytest
        shell: pwsh
      - env: {FLAG: another-value}
        run: pytest
        shell: bash
""",
        "ci.yml",
    )
    job = wf.jobs[0]
    local = inspect_local(tmp_path, env_names={"FLAG"})
    local.os, local.project_python = "macOS", "3.11.9"
    ctx = LocalContext("bash", ".", "pytest")
    report = compare(local, wf, job, 1, ctx)
    by_id = {f.id: f for f in report.findings}
    assert "shell.2" not in by_id and "python.1" not in by_id
    assert by_id["shell.3"].status is Status.POTENTIAL
    assert by_id["command.3"].status is Status.UNKNOWN
    assert by_id["env.FLAG"].status is Status.MATCH and "step 4" in by_id["env.FLAG"].ci.location
    rows = ci_view(wf)["jobs"][0]["variants"]
    assert rows[0]["excluded_steps"] == [1, 2] and rows[0]["python_selectors"] == []
    assert rows[1]["excluded_steps"] == [] and rows[1]["python_selectors"] == ["3.11"]
    assert rows[0]["unresolved_step_conditions"] == [3]
    by_id = {f.id: f for f in compare(local, wf, job, 2, ctx).findings}
    assert by_id["shell.2"].status is Status.DIFFERENCE and by_id["python.1"].status is Status.MATCH
    job.condition = "github.event_name == 'push'"
    assert all(
        f.status not in (Status.MATCH, Status.DIFFERENCE)
        for f in compare(local, wf, job, 2, ctx).findings
    )
    job.condition = False
    assert compare(local, wf, job, 2, ctx).findings == []
    assert ci_view(wf)["jobs"][0]["variants"][1]["python_selectors"] == []
