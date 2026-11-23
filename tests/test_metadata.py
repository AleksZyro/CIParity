import os
from pathlib import Path

import pytest

from ciparity.local import inspect_local, project_metadata
from ciparity.models import InputError, ProjectMetadata
from ciparity.repository import inside, read_project_text
from ciparity.workflows import parse_workflow


@pytest.mark.parametrize(
    ("source", "python", "expected"),
    [
        (
            '[project]\ndependencies = ["pytest>=8", "pkg @ https://example.invalid/a.whl"]\nrequires-python = ">=3.11,<3.14"',
            "3.12.1",
            ProjectMetadata("declared", 2, True),
        ),
        (
            '[project]\ndynamic = ["dependencies"]\nrequires-python = ">=3.12"',
            "3.11.7",
            ProjectMetadata("declared", None, False),
        ),
        ('[project]\nrequires-python = ">=3.11"', None, ProjectMetadata("declared", 0, None)),
        ('[project]\ndependencies = ["invalid/requirement"]', None, ProjectMetadata("invalid")),
        ('[project]\nrequires-python = "malformed"', None, ProjectMetadata("invalid")),
        ("[tool.example]\nvalue = 1", None, ProjectMetadata("no_project_table")),
        ("broken = [", None, ProjectMetadata("invalid")),
    ],
)
def test_project_declarations(tmp_path, source, python, expected):
    (tmp_path / "pyproject.toml").write_text(source, encoding="utf-8")
    assert project_metadata(tmp_path, python) == expected


def test_file_budget_is_visible(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b"#" * 262145)
    assert inspect_local(tmp_path).pyproject.state == "unavailable"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction and alternate-stream policy")
def test_windows_directory_boundary(tmp_path):
    import _winapi

    root = tmp_path / "root"
    target = tmp_path / "other"
    root.mkdir()
    target.mkdir()
    (target / "ci.yml").touch()
    _winapi.CreateJunction(str(target), str(root / "link"))
    with pytest.raises(InputError, match="outside"):
        read_project_text(root, Path("link/ci.yml"))
    with pytest.raises(InputError, match="reserved"):
        inside(root, Path("ci.yml:stream"))


@pytest.mark.parametrize(
    "declaration",
    [
        "{}",
        "{runs-on: 17, steps: [{run: pytest}]}",
        "{runs-on: ubuntu-latest, steps: {}}",
        "{uses: './job.yml', steps: [{run: pytest}]}",
        "{runs-on: ubuntu-latest, steps: [{uses: ./a, run: pytest}]}",
    ],
)
def test_malformed_job_shapes(declaration):
    with pytest.raises(InputError):
        parse_workflow("on: push\njobs:\n  test: " + declaration, "ci.yml")
