import subprocess
from pathlib import Path

import pytest

from ciparity.local import architecture, inspect_local, os_family, probe_version
from ciparity.matrix import expand, resolve
from ciparity.models import InputError, Unsupported
from ciparity.repository import read_project_text, workflows
from ciparity.workflows import parse_workflow, parse_yaml


def test_scopes_and_on():
    wf = parse_workflow(
        """
name: Example
on: [push, pull_request]
defaults: {run: {shell: bash, working-directory: tests}}
env: {GLOBAL: secret}
jobs:
  test:
    name: Unit tests
    needs: build
    if: true
    runs-on: ubuntu-latest
    env: {JOB: x}
    defaults: {run: {shell: sh}}
    steps:
      - id: first
        run: pytest
        shell: pwsh
        env: {STEP: value}
      - run: echo result
""",
        "ci.yml",
    )
    job = wf.jobs[0]
    assert wf.name == "Example" and wf.triggers == ["push", "pull_request"]
    assert job.name == "Unit tests" and job.condition is True and job.needs == ["build"]
    assert job.steps[0].shell == "pwsh" and job.steps[1].shell == "sh"
    assert job.steps[0].directory == "tests" and job.steps[0].id == "first"
    assert job.steps[0].env == {"GLOBAL", "JOB", "STEP"}
    assert job.steps[1].env == {"GLOBAL", "JOB"}
    assert parse_yaml("on: [yes, no, off, '3.10', 3.10]") == {
        "on": ["yes", "no", "off", "3.10", 3.1]
    }


def test_include_uses_original_combinations():
    variants = expand(
        {
            "fruit": ["apple", "pear"],
            "animal": ["cat", "dog"],
            "include": [
                {"color": "green"},
                {"animal": "cat", "color": "pink"},
                {"fruit": "banana"},
                {"fruit": "banana", "animal": "cat"},
            ],
            "exclude": [{"fruit": "pear", "animal": "dog"}],
        }
    )
    assert variants == [
        {"fruit": "apple", "animal": "cat", "color": "pink"},
        {"fruit": "apple", "animal": "dog", "color": "green"},
        {"fruit": "pear", "animal": "cat", "color": "pink"},
        {"fruit": "banana"},
        {"fruit": "banana", "animal": "cat"},
    ]
    assert expand({"include": [{"a": "x"}, {"a": "y"}]}) == [{"a": "x"}, {"a": "y"}]
    assert expand(
        {"a": [False, "false"], "exclude": [{"a": False}], "include": [{"a": False}]}
    ) == [{"a": "false"}, {"a": False}]


@pytest.mark.parametrize(
    "matrix",
    [
        "${{ fromJSON(needs.build.outputs.matrix) }}",
        {"a": [{"b": 1}]},
        {"a": list(range(17)), "b": list(range(17))},
        {"a": ["${{ github.ref }}"]},
    ],
)
def test_matrix_limits(matrix):
    with pytest.raises(Unsupported):
        expand(matrix)


def test_expression_subset():
    assert resolve("${{ matrix.python-version }}", {"python-version": "3.10"}) == "3.10"
    for value in [
        "${{ needs.build.outputs.version }}",
        "x${{ matrix.os }}",
        "${{ matrix.absent }}",
    ]:
        assert resolve(value, {}) is None


@pytest.mark.parametrize(
    "source",
    [
        "a: 1\na: 2",
        "a: &anchor [1]",
        "a: !!str x",
        "x: [\n",
        "a: b\n---\nc: d",
        "[" * 64 + "]" * 64,
    ],
)
def test_invalid_yaml(source):
    with pytest.raises(InputError):
        parse_yaml(source)


def test_workflow_discovery_and_boundary(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(InputError, match="No workflows"):
        workflows(root, None)
    folder = root / ".github" / "workflows"
    folder.mkdir(parents=True)
    for name in ("a.yml", "b.yaml", "readme.txt"):
        (folder / name).touch()
    assert [p.name for p in workflows(root, None)] == ["a.yml", "b.yaml"]
    with pytest.raises(InputError, match="outside"):
        workflows(root, Path("../other.yml"))
    (folder / "a.yml").write_bytes(b"x" * 262145)
    with pytest.raises(Unsupported):
        read_project_text(root, folder / "a.yml")


def test_external_symlink(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = tmp_path / "outside.yml"
    target.touch()
    try:
        (root / "ci.yml").symlink_to(target)
    except OSError:
        pytest.skip("Host does not grant symbolic-link creation.")
    with pytest.raises(InputError, match="outside"):
        workflows(root, Path("ci.yml"))


def test_local_only_observes(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "private-test-value")
    monkeypatch.setenv("SHELL", "/a/private-shell-name")
    (tmp_path / "requirements.txt").write_text("some-package")
    local = inspect_local(tmp_path)
    assert local.project_python is None and local.shell_hint is None
    assert local.dependency_files == ["requirements.txt"]
    assert local.environment_presence["CI"] and "private-test-value" not in str(local)
    assert inspect_local(tmp_path, current_python=True).project_python == local.python
    assert os_family("Darwin") == "macOS" and os_family("Windows") == "Windows"
    assert architecture("AMD64") == architecture("x86_64") == "x64"


def test_timeout_does_not_leak(tmp_path, monkeypatch):
    binary = tmp_path / "native"
    binary.write_bytes(b"MZ00")

    def timeout(args, **kwargs):
        assert kwargs["shell"] is False and kwargs["timeout"] == 2
        assert kwargs["cwd"] != str(tmp_path)
        raise subprocess.TimeoutExpired(args, 2)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert probe_version("git", binary, tmp_path / "repo") is None
