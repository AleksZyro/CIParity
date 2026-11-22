"""Public command line. Unexpected errors never expose raw exception text or locals."""

from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .compare import LocalContext, compare, select_job
from .local import inspect_local
from .models import InputError, Unsupported
from .reporting import local_output, report_output, workflow_output
from .repository import root_path
from .workflows import load_workflows

app = typer.Typer(
    help="Compare local development conditions with declared GitHub Actions jobs.",
    add_completion=False,
    pretty_exceptions_enable=False,
    no_args_is_help=True,
)
inspect_app = typer.Typer(
    help="Inspect the local host or declared workflows.", no_args_is_help=True
)
app.add_typer(inspect_app, name="inspect")

Root = Annotated[Path, typer.Option(help="Project directory to inspect.")]
WorkflowOption = Annotated[
    Path | None, typer.Option("--workflow", help="YAML file relative to the project root.")
]
Format = Annotated[str, typer.Option("--format", help="Output format: text or json.")]
Current = Annotated[
    bool,
    typer.Option(
        "--use-current-python", help="Identify CIParity's interpreter as the project's interpreter."
    ),
]


def is_json(value: str) -> bool:
    if value not in {"text", "json"}:
        raise InputError("Choose --format text or --format json.")
    return value == "json"


@app.callback(invoke_without_command=True)
def start(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", is_eager=True, help="Print the installed version.")
    ] = False,
) -> None:
    if version:
        typer.echo(f"CIParity {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@inspect_app.command("local")
def local(
    root: Root = Path("."),
    format_: Format = "text",
    use_current_python: Current = False,
    tool_versions: Annotated[
        bool,
        typer.Option("--tool-versions", help="Probe recognised native host tools with timeouts."),
    ] = False,
) -> None:
    """Inspect the running interpreter, host tools and project manifests."""
    as_json = is_json(format_)
    local_output(inspect_local(root_path(root), use_current_python, tool_versions), as_json)


@inspect_app.command("ci")
def ci(root: Root = Path("."), workflow: WorkflowOption = None, format_: Format = "text") -> None:
    """List workflows, job IDs and selectable matrix variants."""
    as_json = is_json(format_)
    raise typer.Exit(workflow_output(load_workflows(root_path(root), workflow), as_json))


@app.command("compare", help="Compare local properties with one declared job variant.")
@app.command("report", help="Write a comparison report to stdout in text or JSON.")
@app.command("doctor", help="Summarise differences, unknowns and practical next checks.")
def comparison(
    ctx: typer.Context,
    root: Root = Path("."),
    workflow: WorkflowOption = None,
    job: Annotated[str | None, typer.Option(help="Job ID shown by inspect ci.")] = None,
    variant: Annotated[
        int | None, typer.Option(help="One-based matrix variant from inspect ci.")
    ] = None,
    format_: Format = "text",
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show evidence, impact and next checks.")
    ] = False,
    use_current_python: Current = False,
    local_shell: Annotated[
        str | None, typer.Option(help="Known shell of the local command.")
    ] = None,
    local_workdir: Annotated[
        str | None, typer.Option(help="Known workspace-relative local command directory.")
    ] = None,
    local_command: Annotated[
        str | None, typer.Option(help="Known local invocation, such as 'python -m pytest'.")
    ] = None,
) -> None:
    as_json = is_json(format_)
    project = root_path(root)
    wf, selected = select_job(load_workflows(project, workflow), job)
    result = compare(
        inspect_local(
            project, use_current_python, env_names=set().union(*(s.env for s in selected.steps))
        ),
        wf,
        selected,
        variant,
        LocalContext(local_shell, local_workdir, local_command),
    )
    raise typer.Exit(report_output(result, as_json, verbose, ctx.info_name == "doctor"))


def main() -> None:
    try:
        code = app(standalone_mode=False)
    except Unsupported as exc:
        typer.echo(f"Unsupported: {exc}", err=True)
        code = 3
    except InputError as exc:
        typer.echo(f"Error: {exc}", err=True)
        code = 2
    except OSError:
        typer.echo(
            "Error: Could not access a required path or stream. Check permissions.", err=True
        )
        code = 2
    except Exception as exc:
        # Both Typer and Click usage errors expose an exit code. Raw parser errors can quote inputs.
        if getattr(exc, "exit_code", None) == 2:
            typer.echo(
                "Invalid command or option. Use --help to see supported arguments.", err=True
            )
            code = 2
        else:
            typer.echo("Internal analysis error.", err=True)
            code = 4
    raise SystemExit(code or 0)
