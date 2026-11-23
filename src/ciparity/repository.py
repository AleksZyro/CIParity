"""Read only conventional, bounded files inside the selected project."""

import os
import stat
from pathlib import Path

from .models import InputError, Unsupported

MAX_BYTES = 256 * 1024
MAX_WORKFLOWS = 32


def root_path(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        if root.is_dir():
            return root
    except (OSError, RuntimeError, ValueError):
        pass
    raise InputError("Choose an existing project directory with --root.")


def inside(root: Path, path: Path) -> Path:
    try:
        candidate = Path(os.path.abspath(root / path))
        if not candidate.is_relative_to(root):
            raise InputError("A selected path points outside the project.")
        if os.name == "nt" and any(
            ":" in p or Path(p).is_reserved() for p in candidate.relative_to(root).parts
        ):
            raise InputError("A selected path uses a reserved Windows name.")
        resolved = candidate.resolve()
        if resolved.is_relative_to(root):
            return resolved
    except (OSError, RuntimeError, ValueError):
        pass
    raise InputError("A selected path is invalid or points outside the project.")


def workflows(root: Path, selected: Path | None) -> list[Path]:
    directory = inside(root, Path(".github/workflows"))
    if selected is not None:
        target = inside(root, selected)
        if target.suffix not in {".yml", ".yaml"} or not target.is_file():
            raise InputError("Select an existing .yml or .yaml workflow inside the project.")
        return [target]
    paths: list[Path] = []
    if directory.is_dir():
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if path.suffix in {".yml", ".yaml"} and inside(root, path).is_file():
                    paths.append(path)
                    if len(paths) > MAX_WORKFLOWS:
                        raise Unsupported("More than 32 workflows. Select one with --workflow.")
    if not paths:
        raise InputError("No workflows found in .github/workflows/. Try 'ciparity inspect local'.")
    return sorted(paths)


def read_project_text(root: Path, path: Path) -> str:
    target = inside(root, path)
    if not stat.S_ISREG(target.stat().st_mode):
        raise InputError("Input must be a regular file.")
    with target.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Unsupported("Input exceeds the 256 KiB limit.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        raise InputError("Input must contain UTF-8 text.") from None
