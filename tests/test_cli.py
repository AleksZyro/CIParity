import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ciparity.cli import main
from ciparity.reporting import ci_view
from ciparity.workflows import parse_workflow

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "basic"


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["ciparity", *map(str, args)])
    with pytest.raises(SystemExit) as stop:
        main()
    captured = capsys.readouterr()
    return stop.value.code, captured.out, captured.err


def write_ci(root, body):
    directory = root / ".github" / "workflows"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ci.yml").write_text(body, encoding="utf-8")


def test_example_and_public_entrypoint():
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "ciparity",
            "inspect",
            "ci",
            "--root",
            str(EXAMPLE),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["schema_version"] == 1 and data["kind"] == "workflows"
    variants = data["data"][0]["jobs"][0]["variants"]
    assert [(v["runner"], v["python_selectors"]) for v in variants] == [
        ("ubuntu-latest", ["3.11"]),
        ("ubuntu-latest", ["3.12"]),
        ("windows-latest", ["3.12"]),
    ]


def test_full_report_and_doctor(tmp_path, monkeypatch, capsys):
    for cmd in ("compare", "report", "doctor"):
        code, output, err = invoke(
            monkeypatch,
            capsys,
            cmd,
            "--root",
            EXAMPLE,
            "--variant",
            1,
            "--local-shell",
            "pwsh",
            "--format",
            "json",
        )
        report = json.loads(output)
        assert code == 1 and err == "" and report["data"]["counts"]["confirmed_mismatch"] >= 1
        assert report["data"]["job"] == "test"
    code, output, _ = invoke(monkeypatch, capsys, "doctor", "--root", EXAMPLE, "--variant", 1)
    assert code in {0, 1} and "Next checks" in output
    code, _, err = invoke(monkeypatch, capsys, "compare", "--root", EXAMPLE)
    assert code == 2 and "--variant" in err
    code, _, err = invoke(monkeypatch, capsys, "inspect", "ci", "--root", tmp_path)
    assert code == 2 and "No workflows" in err


def test_unknown_and_unsupported(tmp_path, monkeypatch, capsys):
    write_ci(tmp_path, "on: push\njobs:\n  test: {runs-on: custom, steps: [{run: do-something}]}")
    code, output, _ = invoke(monkeypatch, capsys, "compare", "--root", tmp_path, "--format", "json")
    assert code == 0 and json.loads(output)["data"]["counts"]["unknown"] >= 4
    write_ci(tmp_path, "on: push\njobs:\n  test: {uses: './called.yml'}")
    for args in [("inspect", "ci"), ("compare",)]:
        code, _, _ = invoke(monkeypatch, capsys, *args, "--root", tmp_path)
        assert code == 3


def test_private_content_and_repository_code_stay_inert(tmp_path, monkeypatch, capsys):
    token = "private-fixture-marker"
    monkeypatch.setenv("APP_FLAG", token)
    write_ci(
        tmp_path,
        """
name: private-fixture-marker
on: push
env: {APP_FLAG: private-fixture-marker}
jobs:
  test:
    runs-on: ubuntu-latest
    strategy: {matrix: {flavour: [private-fixture-marker]}}
    steps:
      - uses: ./private-fixture-marker
      - if: contains(github.ref, 'private-fixture-marker')
        run: echo private-fixture-marker > executed
      - uses: actions/setup-python@v6
        with: {python-version: private-fixture-marker}
""",
    )
    (tmp_path / "setup.py").write_text("raise Exception('private-fixture-marker')")
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("analysis tried to spawn a process")

    # Initialise stdlib platform caches before checking analysis-owned probes.
    import platform

    platform.uname()
    monkeypatch.setattr(subprocess, "run", forbidden)
    for args in [("inspect", "local"), ("inspect", "ci"), ("compare",), ("doctor",), ("report",)]:
        for fmt in ("text", "json"):
            code, output, err = invoke(monkeypatch, capsys, *args, "--format", fmt)
            assert code in {0, 1} and token not in output + err
    assert not (tmp_path / "executed").exists()
    write_ci(tmp_path, "on: [private-fixture-marker\n")
    code, output, err = invoke(monkeypatch, capsys, "inspect", "ci")
    assert code == 2 and "line" in err and token not in output + err
    code, output, err = invoke(monkeypatch, capsys, "compare", "--variant", token)
    assert code == 2 and token not in output + err


def test_step_dependencies():
    wf = parse_workflow(
        """
on: push
jobs:
  multi:
    runs-on: ubuntu-latest
    steps:
      - id: build
        run: echo output
      - if: steps.build.outputs.ok
        run: pytest
""",
        "ci.yml",
    )
    assert ci_view(wf)["jobs"][0]["steps"][1]["references_steps"] == ["build"]


def test_environment_path_ignores_project_executables(tmp_path, monkeypatch):
    from ciparity.local import executable

    folder = tmp_path / "bin"
    folder.mkdir()
    tool = folder / ("git.exe" if os.name == "nt" else "git")
    tool.touch(mode=0o755)
    monkeypatch.setenv("PATH", str(folder))
    assert executable("git", tmp_path) is None
