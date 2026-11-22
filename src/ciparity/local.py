"""Host observations. Repository commands and project interpreters are never invoked."""

import os
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from .models import InputError, LocalEnvironment, ProjectMetadata
from .repository import inside, read_project_text

FILES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "setup.py",
    "setup.cfg",
)
TOOLS = ("git", "python", "pip", "uv", "node", "npm", "docker", "pytest")
ENV_NAMES = ("CI", "GITHUB_ACTIONS", "VIRTUAL_ENV", "PYTHONPATH")
SHELLS = {"bash", "sh", "zsh", "fish", "cmd", "pwsh", "powershell", "python"}
PROBES = {"git": ["--version"], "node": ["--version"], "uv": ["--version"], "docker": ["--version"]}


def os_family(value: str) -> str:
    return {"darwin": "macOS", "linux": "Linux", "windows": "Windows"}.get(value.lower(), "other")


def architecture(value: str) -> str | None:
    return {
        "x86_64": "x64",
        "amd64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
    }.get(value.lower())


def executable(tool: str, root: Path) -> Path | None:
    # shutil.which on Windows may implicitly search the working directory.
    suffixes = (".exe", ".cmd", ".bat") if os.name == "nt" else ("",)
    for item in os.get_exec_path():
        directory = Path(item)
        if not directory.is_absolute():
            continue
        for suffix in suffixes:
            try:
                candidate = directory / (tool + suffix)
                if candidate.is_relative_to(root) or not candidate.is_file():
                    continue
                path = candidate.resolve()
                if path.is_relative_to(root):
                    continue
                if os.access(path, os.X_OK):
                    return path
            except (ValueError, RuntimeError, OSError):
                continue
    return None


def probe_version(tool: str, path: Path, root: Path) -> str | None:
    if tool not in PROBES or path.suffix.lower() in {".cmd", ".bat"}:
        return None
    try:
        if (
            not path.is_absolute()
            or path.is_relative_to(root)
            or path.resolve().is_relative_to(root)
        ):
            return None
        # Only native binaries from the host PATH are eligible, never shell wrappers.
        with path.open("rb") as stream:
            magic = stream.read(4)
        if not (
            magic.startswith(b"MZ")
            or magic
            in {
                b"\x7fELF",
                bytes.fromhex("cffaedfe"),
                bytes.fromhex("cafebabe"),
                bytes.fromhex("cafebabf"),
            }
        ):
            return None
        temp = Path(tempfile.gettempdir()).resolve()
        if temp.is_relative_to(root):
            return None
        env = {"PATH": os.defpath}
        if os.name == "nt" and "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        with tempfile.TemporaryDirectory(dir=temp) as scratch:
            result = subprocess.run(
                [str(path), *PROBES[tool]],
                cwd=scratch,
                env=env,
                shell=False,
                timeout=2,
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                check=False,
            )
        if result.returncode == 0:
            match = re.search(rb"\b[vV]?(\d{1,4}\.\d{1,4}(?:\.\d{1,6})?)\b", result.stdout[:256])
            if match:
                return match[1].decode("ascii")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def project_metadata(root: Path, project_python: str | None) -> ProjectMetadata:
    """Expose only PEP 621 declaration counts and interpreter compatibility."""
    try:
        doc = tomllib.loads(read_project_text(root, Path("pyproject.toml")))
        if "project" not in doc:
            return ProjectMetadata("no_project_table")
        project = doc["project"]
        if not isinstance(project, dict):
            return ProjectMetadata("invalid")
        dynamic = project.get("dynamic", [])
        deps = project.get("dependencies", [])
        if not isinstance(dynamic, list) or not isinstance(deps, list):
            return ProjectMetadata("invalid")
        if len(deps) > 1000:
            return ProjectMetadata("unavailable")
        for requirement in deps:
            if not isinstance(requirement, str):
                return ProjectMetadata("invalid")
            if len(requirement) > 4096:
                return ProjectMetadata("unavailable")
            Requirement(requirement)
        count = None if "dependencies" in dynamic else len(deps)
        compatible = None
        if "requires-python" in project and "requires-python" not in dynamic:
            spec = project["requires-python"]
            if not isinstance(spec, str):
                return ProjectMetadata("invalid")
            constraint = SpecifierSet(spec)
            if project_python:
                compatible = constraint.contains(project_python)
        return ProjectMetadata("declared", count, compatible)
    except (tomllib.TOMLDecodeError, InvalidRequirement, InvalidSpecifier, RecursionError):
        return ProjectMetadata("invalid")
    except (InputError, OSError):
        return ProjectMetadata("unavailable")


def inspect_local(
    root: Path,
    current_python: bool = False,
    tool_versions: bool = False,
    env_names: set[str] | None = None,
) -> LocalEnvironment:
    inventory: dict[str, dict[str, str | bool | None]] = {}
    for tool in TOOLS:
        path = executable(tool, root)
        version = probe_version(tool, path, root) if tool_versions and path else None
        if tool == "python" and path and path == Path(sys.executable).resolve():
            version = platform.python_version()
        inventory[tool] = {"available": path is not None, "version": version}
    hint = Path(os.environ.get("SHELL") or os.environ.get("COMSPEC", "")).stem.lower()
    files = [name for name in FILES if inside(root, Path(name)).is_file()]
    project_python = platform.python_version() if current_python else None
    return LocalEnvironment(
        os_family(platform.system()),
        platform.release(),
        architecture(platform.machine()),
        os.sep,
        None,
        str(Path.cwd()),
        sys.executable,
        platform.python_version(),
        platform.python_implementation(),
        sys.prefix != sys.base_prefix,
        project_python,
        hint if hint in SHELLS else None,
        inventory,
        files,
        project_metadata(root, project_python)
        if "pyproject.toml" in files
        else ProjectMetadata("absent"),
        {name: name in os.environ for name in sorted(set(ENV_NAMES) | (env_names or set()))},
    )
