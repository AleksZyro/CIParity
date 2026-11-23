"""Bounded static GitHub Actions matrix expansion."""

import itertools
import math
import re
from typing import Any

from .models import InputError, Unsupported

MAX_VARIANTS = 256
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,79}")
REFERENCE = re.compile(r"\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")


def expand(matrix: Any) -> list[dict[str, Any]]:
    if matrix is None:
        return [{}]
    if not isinstance(matrix, dict):
        raise Unsupported("Dynamic matrix. Inspect the resolved matrix in a workflow run.")
    axes = {k: v for k, v in matrix.items() if k not in {"include", "exclude"}}
    if len(axes) > 16:
        raise Unsupported("Matrix exceeds the 16-axis limit.")

    def scalar(value: Any) -> bool:
        return type(value) in (bool, int, float, str) and not (
            isinstance(value, str) and ("${{" in value or len(value) > 256)
        )

    for key, entries in axes.items():
        if not IDENT.fullmatch(key) or not isinstance(entries, list) or not entries:
            raise Unsupported("Each static matrix axis needs a name and a non-empty list.")
        if not all(scalar(v) for v in entries):
            raise Unsupported("Nested or dynamic matrix values require a workflow run.")
    include, exclude = matrix.get("include", []), matrix.get("exclude", [])
    for rules in (include, exclude):
        if not isinstance(rules, list) or len(rules) > MAX_VARIANTS:
            raise Unsupported("Matrix rules must be a bounded list of objects.")
        if any(
            not isinstance(rule, dict)
            or len(rule) > 32
            or any(not IDENT.fullmatch(k) or not scalar(v) for k, v in rule.items())
            for rule in rules
        ):
            raise Unsupported("Nested or dynamic matrix rules require a workflow run.")
    if math.prod(len(values) for values in axes.values()) > MAX_VARIANTS:
        raise Unsupported("Matrix exceeds 256 combinations before exclusions.")

    def equal(a: Any, b: Any) -> bool:
        return type(a) is type(b) and a == b

    bases = []
    if axes:
        for values in itertools.product(*axes.values()):
            base = dict(zip(axes, values, strict=True))
            if not any(
                all(k in base and equal(base[k], v) for k, v in rule.items()) for rule in exclude
            ):
                bases.append(base)
    result = [base.copy() for base in bases]
    for rule in include:
        added = False
        for index, base in enumerate(bases):
            if all(k not in base or equal(base[k], v) for k, v in rule.items()):
                result[index].update(rule)
                added = True
        if not added:
            result.append(rule.copy())
        if len(result) > MAX_VARIANTS:
            raise Unsupported("Expanded matrix exceeds 256 combinations.")
    if not result:
        raise InputError("Matrix has no selectable combinations.")
    return result


def resolve(value: Any, variant: dict[str, Any]) -> str | None:
    if not isinstance(value, str):
        return None
    match = REFERENCE.fullmatch(value)
    if match:
        selected = variant.get(match[1])
        return selected if isinstance(selected, str) else None
    return None if "${{" in value else value
