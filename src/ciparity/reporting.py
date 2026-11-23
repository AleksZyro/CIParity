"""Explicit public projections keep arbitrary workflow literals out of every output format."""

import json
import re
from collections import Counter
from dataclasses import asdict
from typing import Any

from rich.console import Console
from rich.table import Table

from . import __version__
from .matrix import IDENT, expand
from .models import LocalEnvironment, Report, Status, Unsupported, Workflow
from .semantics import (
    action_kind,
    ci_environment,
    command_kind,
    condition_value,
    python_selector,
    shell_for,
)

EVENTS = {
    "push",
    "pull_request",
    "pull_request_target",
    "workflow_dispatch",
    "workflow_call",
    "workflow_run",
    "schedule",
    "release",
    "merge_group",
    "issues",
    "issue_comment",
    "create",
    "delete",
    "repository_dispatch",
}


def ci_view(workflow: Workflow) -> dict[str, Any]:
    jobs = []
    for job in workflow.jobs:
        item: dict[str, Any] = {
            "id": job.id,
            "conditional": job.condition is not None,
            "needs": job.needs,
            "container": job.container,
            "reusable": job.reusable,
            "env_names": sorted(job.env),
            "dynamic_env": job.dynamic_env,
            "steps": [],
            "variants": [],
        }
        ids = {s.id for s in job.steps if s.id and IDENT.fullmatch(s.id)}
        for step in job.steps:
            # Only expose references to structural step IDs inside Actions expressions.
            fields = [step.command, *step.inputs.values()]
            expressions = [step.condition] if isinstance(step.condition, str) else []
            for field in fields:
                if isinstance(field, str):
                    expressions.extend(re.findall(r"\$\{\{([^{}]*)\}\}", field))
            refs = {
                ref
                for expression in expressions
                for ref in re.findall(r"\bsteps\.([A-Za-z_][\w-]*)\.", expression)
            }
            command = command_kind(step.command)
            item["steps"].append(
                {
                    "number": step.number,
                    "id": step.id if step.id in ids else None,
                    "kind": "run" if step.command is not None else "action",
                    "action": action_kind(step.uses),
                    "command": command[0] if command else None,
                    "conditional": step.condition is not None,
                    "references_steps": sorted(refs & ids),
                    "env_names": sorted(step.env),
                    "dynamic_env": step.dynamic_env,
                }
            )
        try:
            if job.reusable:
                raise Unsupported("Reusable workflow call; target contents are unresolved.")
            variants = expand(job.matrix)
            job_guard = condition_value(job.condition, {})
            for index, variant in enumerate(variants, 1):
                ci = ci_environment(job, variant)
                # The inventory lists declarations for a job that meets its own guard.
                guards = {s.number: condition_value(s.condition, variant) for s in job.steps}
                active = [
                    s for s in job.steps if job_guard is not False and guards[s.number] is not False
                ]
                item["variants"].append(
                    {
                        "number": index,
                        "runner": ci.runner,
                        "os": ci.os,
                        "job_condition": job_guard,
                        "excluded_steps": [
                            n for n, permitted in guards.items() if permitted is False
                        ],
                        "unresolved_step_conditions": [
                            n for n, permitted in guards.items() if permitted is None
                        ],
                        "python_selectors": [
                            python_selector(s, variant) if guards[s.number] is True else None
                            for s in active
                            if action_kind(s.uses) == "actions/setup-python"
                        ],
                        "run_shells": sorted(
                            {
                                (shell_for(s, ci, variant) or "unknown")
                                if guards[s.number] is True
                                else "unknown"
                                for s in active
                                if s.command is not None
                            }
                        ),
                    }
                )
        except Unsupported as exc:
            item["limitation"] = str(exc)
        jobs.append(item)
    return {
        "workflow": workflow.path,
        "events": [t if t in EVENTS else "other" for t in workflow.triggers],
        "jobs": jobs,
    }


def envelope(kind: str, data: Any) -> dict[str, Any]:
    return {"schema_version": 1, "tool_version": __version__, "kind": kind, "data": data}


def emit_json(kind: str, data: Any) -> None:
    # One document, independent of terminal width and colouring.
    print(json.dumps(envelope(kind, data), ensure_ascii=True, sort_keys=True, indent=2))


def text_console() -> Console:
    return Console(markup=False, highlight=False, emoji=False, soft_wrap=True)


def local_output(local: LocalEnvironment, as_json: bool) -> None:
    data = asdict(local)
    if as_json:
        emit_json("local", data)
        return
    console = text_console()
    console.print(f"CIParity {__version__} | Local environment", style="bold")
    for key, value in data.items():
        if key == "tools":
            console.print("Tools on the host PATH", style="bold")
            for name, tool in local.tools.items():
                state = "available" if tool["available"] else "not found"
                console.print(f"  {name}: {state} ({tool['version'] or 'version unknown'})")
        elif key == "dependency_files":
            console.print(
                "Dependency files: " + (", ".join(local.dependency_files) or "none detected")
            )
        elif key == "pyproject":
            meta = local.pyproject
            console.print(f"Pyproject metadata: {meta.state}")
            if meta.state == "declared":
                count = meta.declared_dependencies
                console.print(f"  Direct dependencies: {count if count is not None else 'unknown'}")
                compatible = {True: "yes", False: "no", None: "unknown"}[
                    meta.project_python_compatible
                ]
                console.print(f"  Project Python meets requires-python: {compatible}")
        elif key == "environment_presence":
            console.print(
                "Environment names: "
                + ", ".join(f"{k}={'present' if v else 'absent'}" for k, v in value.items())
            )
        else:
            console.print(f"{key.replace('_', ' ')}: {value if value is not None else 'unknown'}")


def workflow_output(workflows: list[Workflow], as_json: bool) -> int:
    views = [ci_view(wf) for wf in workflows]
    if as_json:
        emit_json("workflows", views)
    else:
        console = text_console()
        for view in views:
            console.print(view["workflow"], style="bold")
            for job in view["jobs"]:
                console.print(
                    f"  {job['id']} | {len(job['steps'])} steps"
                    + (" | conditional" if job["conditional"] else "")
                )
                if "limitation" in job:
                    console.print(f"    Unsupported: {job['limitation']}")
                for row in job["variants"]:
                    versions = (
                        ", ".join(x or "unknown" for x in row["python_selectors"]) or "unknown"
                    )
                    console.print(
                        f"    --variant {row['number']}  {row['runner']}  Python {versions}"
                    )
    return 3 if any("limitation" in j for wf in views for j in wf["jobs"]) else 0


def report_output(report: Report, as_json: bool, verbose: bool, doctor: bool) -> int:
    counts = Counter(f.status.value for f in report.findings)
    if as_json:
        emit_json("comparison", asdict(report) | {"counts": dict(counts)})
    else:
        console = text_console()
        console.print(
            f"CIParity {__version__} | {report.workflow} / {report.job} / variant {report.variant}",
            style="bold",
        )
        console.print("  ".join(f"{status.value}: {counts[status.value]}" for status in Status))
        table = Table(box=None, padding=(0, 1), expand=False)
        for title in ("Finding", "Local", "Declared CI", "Status"):
            table.add_column(title)
        priority = [
            Status.DIFFERENCE,
            Status.POTENTIAL,
            Status.UNSUPPORTED,
            Status.UNKNOWN,
            Status.MATCH,
        ]
        ordered = sorted(report.findings, key=lambda f: priority.index(f.status))
        for finding in ordered:
            table.add_row(
                finding.id,
                finding.local.value or "unknown",
                finding.ci.value or "unknown",
                finding.status.value,
            )
        console.print(table)
        for note in report.notes:
            console.print(note)
        if doctor or verbose:
            checks = dict.fromkeys(f.next_check for f in ordered if f.status is not Status.MATCH)
            if checks:
                console.print("Next checks", style="bold")
                for number, check in enumerate(checks, 1):
                    console.print(f"{number}. {check}")
        if verbose:
            for finding in ordered:
                console.print(f"{finding.id} | {finding.local.location} | {finding.ci.location}")
                console.print(f"{finding.reason} {finding.possible_impact}")
    return 1 if counts[Status.DIFFERENCE.value] else 0
