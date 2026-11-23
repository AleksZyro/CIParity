"""Compare observed host properties with declarations at their precise scope."""

from dataclasses import dataclass
from typing import Any

from .local import SHELLS
from .matrix import expand, resolve
from .models import (
    Evidence,
    Finding,
    InputError,
    Job,
    LocalEnvironment,
    Report,
    Status,
    Step,
    Unsupported,
    Workflow,
)
from .semantics import (
    MANIFESTS,
    action_kind,
    ci_environment,
    command_kind,
    compare_python,
    condition_value,
    portable_directory,
    python_selector,
    shell_for,
)


@dataclass(frozen=True)
class LocalContext:
    shell: str | None = None
    directory: str | None = None
    command: str | None = None

    def validate(self) -> None:
        if self.shell is not None and self.shell not in SHELLS:
            raise InputError("Choose a supported local shell (for example bash, sh or pwsh).")
        if self.directory is not None and portable_directory(self.directory) is None:
            raise InputError("Use a portable project-relative local working directory.")
        if self.command is not None and not command_kind(self.command):
            raise InputError("Local command is outside the recognised command list. See README.")


def select_job(workflows: list[Workflow], job_id: str | None) -> tuple[Workflow, Job]:
    matches = [(wf, job) for wf in workflows for job in wf.jobs if job_id in (None, job.id)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise InputError("No matching job. Run 'ciparity inspect ci' to list job IDs.")
    choices = ", ".join(f"{wf.path} / {job.id}" for wf, job in matches[:12])
    raise InputError(f"Ambiguous job. Select --workflow and --job. Choices: {choices}.")


def select_variant(job: Job, number: int | None) -> tuple[int, dict[str, Any]]:
    if job.reusable:
        raise Unsupported("Reusable workflow call. Inspect the called workflow separately.")
    variants = expand(job.matrix)
    if number is None and len(variants) > 1:
        raise InputError(
            f"Job has {len(variants)} variants. Run 'ciparity inspect ci' and select --variant N."
        )
    index = number if number is not None else 1
    if not 1 <= index <= len(variants):
        raise InputError(f"Variant must be in the range 1..{len(variants)}.")
    return index, variants[index - 1]


def compare_values(left: str | None, right: str | None) -> Status:
    if left is None or right is None:
        return Status.UNKNOWN
    return Status.MATCH if left == right else Status.DIFFERENCE


def compare(
    local: LocalEnvironment,
    workflow: Workflow,
    job: Job,
    number: int | None,
    context: LocalContext = LocalContext(),
) -> Report:
    context.validate()
    index, variant = select_variant(job, number)
    ci = ci_environment(job, variant)
    findings: list[Finding] = []
    notes = ["CI evidence describes declarations. Runtime and failure cause are unobserved."]
    # GitHub evaluates the job-level guard before expanding its matrix.
    job_guard = condition_value(job.condition, {})
    if job_guard is False:
        notes.append("The job condition is false. No comparison applies.")
        return Report(workflow.path, job.id, index, ci, True, [], notes)
    if job_guard is None:
        notes.append("Job execution depends on an unresolved condition.")
    guards = {s.number: condition_value(s.condition, variant) for s in job.steps}
    steps = [s for s in job.steps if guards[s.number] is not False]
    for step in job.steps:
        if guards[step.number] is False:
            notes.append(f"Step {step.number} is excluded by its condition in this variant.")
        elif guards[step.number] is None:
            notes.append(f"Step {step.number} has an unresolved condition.")
    if ci.container:
        notes.append("Container image contents are unknown. Runner properties describe its host.")
    if job.dynamic_env or any(s.dynamic_env for s in steps):
        notes.append("An expression supplies environment names that cannot be enumerated.")
    if any(step.uses for step in steps):
        notes.append(
            "Action internals and changes to later steps are outside this static comparison."
        )

    def record(
        key: str,
        category: str,
        title: str,
        left: Evidence,
        value: str | None,
        location: str,
        reason: str,
        impact: str,
        check: str,
        status: Status | None = None,
        *,
        step_number: int | None = None,
    ) -> None:
        if len(findings) >= 2000:
            raise Unsupported("The report exceeds the 2,000 finding limit.")
        outcome = status or compare_values(left.value, value)
        if job_guard is None or (step_number is not None and guards[step_number] is None):
            if outcome is Status.MATCH:
                outcome = Status.UNKNOWN
            elif outcome is Status.DIFFERENCE:
                outcome = Status.POTENTIAL
            reason += " Applicability depends on an unresolved condition."
        findings.append(
            Finding(
                key,
                category,
                title,
                outcome,
                "warning" if outcome is Status.DIFFERENCE else "info",
                left,
                Evidence("declared_ci", f"{workflow.path} / {job.id} / {location}", value),
                reason,
                impact,
                check,
            )
        )

    def observed(value: str | None, location: str) -> Evidence:
        return Evidence(
            "local_observation" if value is not None else "unavailable", location, value
        )

    def declared(value: str | None, location: str) -> Evidence:
        return Evidence("user_declaration" if value is not None else "unavailable", location, value)

    record(
        "os",
        "platform",
        "Operating system family",
        observed(local.os, "platform.system"),
        ci.os,
        "runs-on",
        "Compares the host OS family with the selected runner declaration.",
        "Platform-specific APIs and path rules can affect a test.",
        "Run the same test on the declared OS.",
    )
    record(
        "architecture",
        "platform",
        "System architecture",
        observed(local.architecture, "platform.machine"),
        ci.architecture,
        "runs-on",
        "Only recognised runner architectures are comparable.",
        "Native wheels or binaries can differ.",
        "Check the machine architecture in the job log.",
    )
    setups: list[Step | None] = [s for s in steps if action_kind(s.uses) == "actions/setup-python"]
    if not setups:
        setups.append(None)
    for setup in setups:
        version = python_selector(setup, variant) if setup else None
        location = (
            f"step {setup.number} / setup-python"
            if setup
            else "no applicable setup-python declaration"
        )
        record(
            f"python.{setup.number}" if setup else "python",
            "python",
            "Python version selector",
            declared(local.project_python, "--use-current-python and running interpreter"),
            version,
            location,
            "Compares the specified release components; a minor selector leaves the patch open.",
            "Different releases can support different syntax or wheels.",
            "Confirm the test interpreter with python -VV in each environment.",
            compare_python(local.project_python, version),
            step_number=setup.number if setup else None,
        )
    # Prefer the first unconditional declaration over an earlier conditional one.
    env_source: dict[str, int] = {}
    for step in steps:
        for name in step.env:
            if name not in env_source or (
                guards[env_source[name]] is None and guards[step.number] is True
            ):
                env_source[name] = step.number
    local_command = command_kind(context.command)
    for step in steps:
        loc = f"step {step.number}"
        if action_kind(step.uses) == "opaque action":
            record(
                f"action.{step.number}",
                "action",
                "Action internals",
                observed(None, "no local equivalent"),
                "opaque action",
                loc,
                "Only the action boundary is available.",
                "An action can change later steps.",
                "Inspect the referenced action at its resolved revision.",
                Status.UNSUPPORTED,
                step_number=step.number,
            )
        for name in sorted(n for n in step.env if env_source[n] == step.number):
            present = local.environment_presence.get(name)
            presence = {True: "present", False: "absent", None: None}[present]
            record(
                f"env.{name}",
                "environment",
                f"Declaration of {name}",
                observed(presence, "process environment membership"),
                "declared",
                loc + " / effective env names",
                "Only presence of the variable name is compared.",
                "A command can branch on variable presence.",
                "Check presence in the failing step without logging its value.",
                {True: Status.MATCH, False: Status.POTENTIAL, None: Status.UNKNOWN}[present],
                step_number=step.number,
            )
        if step.command is None:
            continue
        cmd = command_kind(step.command)
        same_purpose = cmd is not None and local_command is not None and cmd[1] == local_command[1]
        local_scope = local_command is None or same_purpose
        shell = shell_for(step, ci, variant)
        record(
            f"shell.{step.number}",
            "shell",
            "Run shell",
            declared(context.shell if local_scope else None, "--local-shell"),
            shell,
            loc,
            "Uses the effective run defaults and step override.",
            "Quoting and expansion vary by shell.",
            "Check the shell used to start the local command.",
            step_number=step.number,
        )
        directory = (
            portable_directory(resolve(step.directory, variant))
            if step.directory is not None
            else "."
        )
        local_dir = portable_directory(context.directory) if local_scope else None

        def directory_label(path: str | None) -> str | None:
            return ("workspace root" if path == "." else "relative subdirectory") if path else None

        dir_status = compare_values(local_dir, directory)
        if (
            dir_status is Status.DIFFERENCE
            and local_dir
            and directory
            and local_dir.casefold() == directory.casefold()
        ):
            dir_status = Status.POTENTIAL
        record(
            f"directory.{step.number}",
            "directory",
            "Working directory",
            declared(directory_label(local_dir), "--local-workdir"),
            directory_label(directory),
            loc,
            "Compares portable workspace-relative path declarations. Custom paths are withheld.",
            "Relative file lookups can start in another directory.",
            "Compare the two workspace-relative directories.",
            dir_status,
            step_number=step.number,
        )
        record(
            f"command.{step.number}",
            "command",
            "Command invocation",
            declared(
                context.command.strip() if context.command and local_command else None,
                "--local-command",
            ),
            step.command.strip() if cmd else None,
            loc,
            "Only whole recognised invocations with the same purpose are compared.",
            "Different launchers can select different environments.",
            "Compare the exact invocation in the step.",
            (
                compare_values(
                    context.command.strip() if context.command else None, step.command.strip()
                )
                if same_purpose
                else Status.UNKNOWN
            ),
            step_number=step.number,
        )
        if cmd:
            available = local.tools[cmd[2]]["available"]
            record(
                f"tool.{step.number}",
                "tools",
                f"Tool requested: {cmd[2]}",
                observed("available" if available else "not found", "host PATH inventory"),
                "requested",
                loc,
                "An invocation requests a tool. Installation in CI is unverified.",
                "Tool lookup can fail before tests begin.",
                "Check the executable and its version in both environments.",
                Status.UNKNOWN if available else Status.POTENTIAL,
                step_number=step.number,
            )
        if cmd and cmd[0] in MANIFESTS:
            files = MANIFESTS[cmd[0]]
            present_files = sorted(set(files) & set(local.dependency_files))
            record(
                f"dependency.{step.number}",
                "dependencies",
                "Dependency manifest",
                observed(
                    ", ".join(present_files) or "not detected at root", "project file inventory"
                ),
                cmd[0],
                loc,
                "Compares an install declaration with conventional root filenames.",
                "A missing input can affect installation.",
                "Check the manifest in this step's working directory.",
                Status.POTENTIAL if directory == "." and not present_files else Status.UNKNOWN,
                step_number=step.number,
            )
    if not any(f.category == "dependencies" for f in findings):
        record(
            "dependencies",
            "dependencies",
            "Dependency installation",
            observed(", ".join(local.dependency_files) or None, "project file inventory"),
            None,
            "steps",
            "No recognised install declaration.",
            "Installed dependency versions remain unknown.",
            "Compare lockfiles and resolved package versions.",
        )
    return Report(workflow.path, job.id, index, ci, job.condition is not None, findings, notes)
