"""Internal workflow declarations and the public, value-minimised report model."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class InputError(Exception):
    """A fixed or safely constructed message suitable for the CLI."""


class Unsupported(InputError):
    """A valid construct outside the static analysis boundary."""


class Status(StrEnum):
    MATCH = "confirmed_match"
    DIFFERENCE = "confirmed_mismatch"
    POTENTIAL = "potential_mismatch"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Evidence:
    source: Literal["local_observation", "user_declaration", "declared_ci", "unavailable"]
    location: str
    value: str | None


@dataclass(frozen=True)
class Finding:
    id: str
    category: str
    title: str
    status: Status
    severity: Literal["info", "warning"]
    local: Evidence
    ci: Evidence
    reason: str
    possible_impact: str
    next_check: str


@dataclass
class ProjectMetadata:
    state: str
    declared_dependencies: int | None = None
    project_python_compatible: bool | None = None


@dataclass
class LocalEnvironment:
    os: str
    os_release: str | None
    architecture: str | None
    path_separator: str
    filesystem_case_sensitive: bool | None
    cwd: str
    interpreter_path: str
    python: str
    implementation: str
    virtual_environment: bool
    project_python: str | None
    shell_hint: str | None
    tools: dict[str, dict[str, str | bool | None]]
    dependency_files: list[str]
    pyproject: ProjectMetadata
    environment_presence: dict[str, bool]


@dataclass
class Step:
    number: int
    id: str | None
    condition: str | bool | None
    uses: str | None
    command: str | None
    inputs: dict[str, Any]
    shell: str | None
    directory: str | None
    env: set[str]
    dynamic_env: bool


@dataclass
class Job:
    id: str
    name: str | None
    runner: Any
    matrix: Any
    condition: str | bool | None
    needs: list[str]
    container: bool
    reusable: bool
    env: set[str]
    dynamic_env: bool
    steps: list[Step]


@dataclass
class Workflow:
    path: str
    name: str | None
    triggers: list[str]
    jobs: list[Job]


@dataclass
class CIEnvironment:
    runner: str
    os: str | None
    architecture: str | None
    container: bool


@dataclass
class Report:
    workflow: str
    job: str
    variant: int
    environment: CIEnvironment
    conditional_job: bool
    findings: list[Finding]
    notes: list[str]
