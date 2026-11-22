from dataclasses import asdict

import pytest

from ciparity.compare import LocalContext, compare, select_job, select_variant
from ciparity.local import inspect_local
from ciparity.models import InputError, Status, Unsupported
from ciparity.semantics import ci_environment, compare_python, shell_for
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
