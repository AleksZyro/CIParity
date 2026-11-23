"""Rules for safe public values and narrow comparisons of workflow declarations."""

import re
from pathlib import PurePosixPath
from typing import Any

from packaging.version import InvalidVersion, Version

from .local import SHELLS
from .matrix import resolve
from .models import CIEnvironment, Job, Status, Step

# Explicit recognisers avoid treating a custom runner label as a hosted platform.
HOSTED = {
    "ubuntu": ("Linux", ("latest", "22.04", "24.04", "26.04")),
    "windows": ("Windows", ("latest", "2022", "2025")),
    "macos": ("macOS", ("latest", "13", "14", "15", "26")),
}
RUNNERS = {f"{prefix}-{tag}": family for prefix, (family, tags) in HOSTED.items() for tag in tags}
RUNNERS.update({f"ubuntu-{tag}-arm": "Linux" for tag in ("22.04", "24.04", "26.04")})
RUNNERS["windows-11-arm"] = "Windows"
VERSION = re.compile(r"(?:0|[1-9][0-9]?)(?:\.(?:0|[1-9][0-9]?)){0,2}")
MATRIX_CONDITION = re.compile(r"matrix\.([A-Za-z_][\w-]*)\s*(==|!=)\s*'([^']*)'", re.ASCII)
COMMANDS = {
    "pytest": ("pytest", "test", "pytest"),
    "python -m pytest": ("python -m pytest", "test", "python"),
    "python -m pip install -r requirements.txt": ("pip requirements.txt", "install", "python"),
    "pip install -r requirements.txt": ("pip requirements.txt", "install", "pip"),
    "python -m pip install -e .": ("pip editable project", "install", "python"),
    "uv sync": ("uv sync", "install", "uv"),
    "uv run pytest": ("uv run pytest", "test", "uv"),
    "npm ci": ("npm ci", "install", "npm"),
}
MANIFESTS = {
    "pip requirements.txt": ("requirements.txt",),
    "uv sync": ("pyproject.toml",),
    "npm ci": ("package-lock.json",),
    "pip editable project": ("pyproject.toml", "setup.py", "setup.cfg"),
}


def ci_environment(job: Job, variant: dict[str, Any]) -> CIEnvironment:
    raw = job.runner
    labels = (
        [resolve(i, variant) for i in raw] if isinstance(raw, list) else [resolve(raw, variant)]
    )
    if any(label and label.lower() == "self-hosted" for label in labels):
        return CIEnvironment("self-hosted", None, None, job.container)
    label = labels[0] if len(labels) == 1 else None
    label = label.lower() if label and label.isascii() else None
    if label not in RUNNERS or label is None:
        return CIEnvironment("unresolved", None, None, job.container)
    arch = "arm64" if label.endswith("-arm") else None
    if label.startswith(("windows-", "ubuntu-")) and arch is None:
        arch = "x64"
    return CIEnvironment(
        label,
        None if job.container else RUNNERS[label],
        None if job.container else arch,
        job.container,
    )


def condition_value(condition: str | bool | None, variant: dict[str, Any]) -> bool | None:
    """Whether a declared guard permits this variant, assuming normal preceding success."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    if expression in {"true", "false"}:
        return expression == "true"
    match = MATRIX_CONDITION.fullmatch(expression)
    if not match:
        return None
    left = variant.get(match[1])
    right = match[3]
    if not isinstance(left, str) or not (left.isascii() and right.isascii()):
        return None
    equal = left.lower() == right.lower()
    return equal if match[2] == "==" else not equal


def shell_for(step: Step, ci: CIEnvironment, variant: dict[str, Any]) -> str | None:
    if step.shell is not None:
        value = resolve(step.shell, variant)
        return value if value in SHELLS else None
    if ci.container:
        return "sh"
    if ci.os == "Windows":
        return "pwsh"
    # An unspecified Unix shell may fall back to sh.
    return None


def python_selector(step: Step, variant: dict[str, Any]) -> str | None:
    value = resolve(step.inputs.get("python-version"), variant)
    return value if value is not None and VERSION.fullmatch(value) else None


def compare_python(local: str | None, selector: str | None) -> Status:
    if not local or not selector or not VERSION.fullmatch(selector):
        return Status.UNKNOWN
    try:
        actual, requested = Version(local), Version(selector)
    except InvalidVersion:
        return Status.UNKNOWN
    if actual.release[: len(requested.release)] != requested.release:
        return Status.DIFFERENCE
    if actual.is_prerelease or actual.is_devrelease or actual.is_postrelease or actual.local:
        return Status.UNKNOWN
    return Status.MATCH


def action_kind(uses: str | None) -> str | None:
    if not uses:
        return None
    action, sep, ref = uses.partition("@")
    if sep and ref and action in {"actions/setup-python", "actions/checkout", "actions/setup-node"}:
        return action
    return "opaque action"


def portable_directory(value: str | None) -> str | None:
    if value is None or not re.fullmatch(r"[A-Za-z0-9_./-]{1,180}", value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def command_kind(command: str | None) -> tuple[str, str, str] | None:
    return COMMANDS.get(command.strip()) if command else None
