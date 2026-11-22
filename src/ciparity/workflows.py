"""Parse a small Actions YAML dialect without constructors or implicit YAML 1.1 booleans."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from yaml.events import (
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    Event,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
)

from .matrix import IDENT
from .models import InputError, Job, Step, Unsupported, Workflow
from .repository import read_project_text, workflows

ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def parse_yaml(source: str) -> Any:
    events: Iterator[Event] = iter(yaml.parse(source, Loader=yaml.BaseLoader))
    count = 0

    def item(event: Event, depth: int, *, key: bool = False) -> Any:
        nonlocal count
        count += 1
        if depth > 32 or count > 10000:
            raise Unsupported("YAML exceeds the depth or node limit.")
        if (
            isinstance(event, AliasEvent)
            or getattr(event, "tag", None)
            or getattr(event, "anchor", None)
        ):
            raise Unsupported("YAML aliases, anchors and explicit tags are unsupported.")
        if isinstance(event, ScalarEvent):
            value = event.value
            if key or event.style is not None:
                return value
            if value in {"", "~", "null", "Null", "NULL"}:
                return None
            if value.lower() in {"true", "false"}:
                return value.lower() == "true"
            if re.fullmatch(r"-?(?:0|[1-9]\d{0,15})(?:\.\d{1,15})?", value):
                return float(value) if "." in value else int(value)
            return value
        if key:
            raise InputError("Workflow mapping keys must be scalar names.")
        if isinstance(event, MappingStartEvent):
            mapping = {}
            while not isinstance(child := next(events), MappingEndEvent):
                name = item(child, depth + 1, key=True)
                if name in mapping:
                    mark = child.start_mark
                    location = f" at line {mark.line + 1}" if mark else ""
                    raise InputError(f"Duplicate YAML key{location}.")
                mapping[name] = item(next(events), depth + 1)
            return mapping
        if isinstance(event, SequenceStartEvent):
            result = []
            while not isinstance(child := next(events), SequenceEndEvent):
                result.append(item(child, depth + 1))
            return result
        raise InputError("Expected a YAML scalar, list or mapping.")

    try:
        next(events)  # stream start
        if not isinstance(next(events), DocumentStartEvent):
            raise InputError("Workflow file is empty.")
        value = item(next(events), 0)
        if not isinstance(next(events), DocumentEndEvent) or not isinstance(
            next(events), StreamEndEvent
        ):
            raise InputError("Expected a single YAML document.")
        return value
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f" near line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise InputError(f"Invalid workflow YAML{where}.") from None
    except (yaml.YAMLError, RecursionError, StopIteration, ValueError):
        raise InputError("Invalid workflow YAML.") from None


def mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(f"Expected a mapping for {field}.")
    return value


def optional_text(data: dict[str, Any], key: str) -> str | None:
    if key not in data:
        return None
    if not isinstance(data[key], str):
        raise InputError(f"Expected text for {key}.")
    return str(data[key])


def condition(data: dict[str, Any]) -> str | bool | None:
    if isinstance(data.get("if"), bool):
        return bool(data["if"])
    return optional_text(data, "if")


def env_scope(scope: dict[str, Any]) -> tuple[set[str], bool]:
    value = scope.get("env", {})
    if isinstance(value, str) and "${{" in value:
        return set(), True
    env = mapping(value, "env")
    if len(env) > 128:
        raise Unsupported("A single scope exceeds the 128 environment name limit.")
    if not all(ENV_NAME.fullmatch(name) for name in env):
        raise Unsupported("Environment declarations use unsupported variable names.")
    return set(env), False


def defaults(scope: dict[str, Any]) -> dict[str, Any]:
    return mapping(mapping(scope.get("defaults", {}), "defaults").get("run", {}), "defaults.run")


def parse_workflow(source: str, path: str) -> Workflow:
    doc = mapping(parse_yaml(source), "workflow")
    trigger = doc.get("on")
    if isinstance(trigger, str):
        triggers = [trigger]
    elif isinstance(trigger, dict):
        triggers = list(trigger)
    elif isinstance(trigger, list) and all(isinstance(x, str) for x in trigger):
        triggers = trigger
    else:
        raise InputError("Expected an event in the workflow 'on' field.")
    entries = mapping(doc.get("jobs"), "jobs")
    if not entries:
        raise InputError("Workflow must declare at least one job.")
    if len(entries) > 128:
        raise Unsupported("Workflow exceeds the 128-job limit.")
    workflow_env, workflow_dynamic = env_scope(doc)
    jobs = []
    for job_id, raw in entries.items():
        if not IDENT.fullmatch(job_id):
            raise InputError("Invalid or unsupported job ID.")
        job = mapping(raw, "job")
        job_env, job_dynamic = env_scope(job)
        base_env = workflow_env | job_env
        base_dynamic = workflow_dynamic or job_dynamic
        run_defaults = defaults(doc) | defaults(job)
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        if not isinstance(needs, list) or not all(
            isinstance(x, str) and IDENT.fullmatch(x) for x in needs
        ):
            raise InputError("Expected job IDs in needs.")
        reusable = optional_text(job, "uses") is not None
        if reusable and ("steps" in job or "runs-on" in job):
            raise InputError("A reusable workflow call cannot also declare steps or runs-on.")
        if not reusable and "runs-on" not in job:
            raise InputError("Job requires runs-on or a reusable workflow.")
        if not reusable and not isinstance(job["runs-on"], (str, list, dict)):
            raise InputError("Expected a runner label, label list or runner mapping.")
        sequence = job.get("steps", [])
        if not isinstance(sequence, list) or (not reusable and not sequence):
            raise InputError("Expected a non-empty list of job steps.")
        if len(sequence) > 256:
            raise Unsupported("Job exceeds the 256-step limit.")
        steps = []
        for number, raw_step in enumerate(sequence, 1):
            step = mapping(raw_step, "step")
            command, uses = optional_text(step, "run"), optional_text(step, "uses")
            if (command is None) == (uses is None):
                raise InputError("Each step must specify exactly one of run or uses.")
            effective = run_defaults | step
            env, dynamic = env_scope(step)
            step_id = optional_text(step, "id")
            steps.append(
                Step(
                    number,
                    step_id,
                    condition(step),
                    uses,
                    command,
                    mapping(step.get("with", {}), "with"),
                    optional_text(effective, "shell"),
                    optional_text(effective, "working-directory"),
                    base_env | env,
                    base_dynamic or dynamic,
                )
            )
        strategy = mapping(job.get("strategy", {}), "strategy")
        if "matrix" in strategy and strategy["matrix"] is None:
            raise InputError("Expected a matrix declaration.")
        jobs.append(
            Job(
                job_id,
                optional_text(job, "name"),
                job.get("runs-on"),
                strategy.get("matrix"),
                condition(job),
                needs,
                "container" in job,
                reusable,
                base_env,
                base_dynamic,
                steps,
            )
        )
    return Workflow(path, optional_text(doc, "name"), triggers, jobs)


def load_workflows(root: Path, selected: Path | None) -> list[Workflow]:
    result = []
    for path in workflows(root, selected):
        label = path.relative_to(root).as_posix()
        if not re.fullmatch(r"[\w ./-]{1,240}", label):
            raise InputError("Workflow filename contains unsupported characters.")
        try:
            result.append(parse_workflow(read_project_text(root, path), label))
        except InputError as exc:
            raise type(exc)(f"{label}: {exc}") from None
    return result
