"""Shared CLI plumbing: option parsing, error rendering, repo resolution."""

from __future__ import annotations

import functools
import inspect
import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.panel import Panel
from rich.table import Table

from orchestrator.core.config import Settings, load_settings
from orchestrator.core.errors import OrchestratorError
from orchestrator.core.logging import configure_logging, console, err_console
from orchestrator.core.models import CheckStatus, Platform
from orchestrator.core.state import StateStore, WorkflowState


def resolve_repo(repo: Path | None) -> Path:
    """Default to the current working directory, then walk up to a git root."""
    start = Path(repo).expanduser().resolve() if repo else Path.cwd().resolve()
    if (start / ".git").exists():
        return start
    for parent in start.parents:
        if (parent / ".git").exists():
            return parent
    return start


def get_settings(repo: Path, *, log_level: str | None = None) -> Settings:
    settings = load_settings(repo)
    configure_logging(log_level or settings.behaviour.log_level)
    return settings


def parse_platform(value: str | None) -> Platform | None:
    if not value:
        return None
    try:
        return Platform(value.lower())
    except ValueError as exc:
        raise typer.BadParameter(
            f"Unknown platform '{value}'. Choose ios, android or generic."
        ) from exc


def fail(error: OrchestratorError) -> NoReturn:
    err_console.print(
        Panel(str(error), title="[fail]Error[/fail]", border_style="red", padding=(0, 2))
    )
    raise typer.Exit(code=error.exit_code)


def handle_errors(func):
    """Render an OrchestratorError as a readable panel instead of a traceback.

    Typer builds a command's options from the callback's signature, so the
    wrapper must expose the wrapped function's signature verbatim — hence both
    ``functools.wraps`` and the explicit ``__signature__``.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OrchestratorError as exc:
            fail(exc)
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            err_console.print("\n[muted]Interrupted. The run's state is saved; "
                              "use `orc resume` to continue.[/muted]")
            sys.exit(130)

    wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
    return wrapper


STATUS_STYLE = {
    "COMPLETED": "ok",
    "PR_CREATED": "ok",
    "VALIDATION_PASSED": "ok",
    "FAILED": "fail",
    "VALIDATION_FAILED": "fail",
    "REJECTED": "muted",
}


def render_runs(runs: list[WorkflowState]) -> Table:
    table = Table(title="Orchestrator runs", show_lines=False)
    table.add_column("Run", style="bold")
    table.add_column("Issue")
    table.add_column("Platform")
    table.add_column("Status")
    table.add_column("Updated", style="muted")
    for state in runs:
        style = STATUS_STYLE.get(state.status.value, "phase")
        table.add_row(
            state.run_id,
            state.issue_key,
            state.platform.display,
            f"[{style}]{state.status.value}[/{style}]",
            state.updated_at.strftime("%Y-%m-%d %H:%M"),
        )
    return table


def render_run_detail(store: StateStore, state: WorkflowState) -> None:
    style = STATUS_STYLE.get(state.status.value, "phase")
    console.print(
        Panel(
            "\n".join(
                [
                    f"Issue:      {state.issue_key}",
                    f"Repository: {state.repo_path}",
                    f"Platform:   {state.platform.display}",
                    f"Status:     [{style}]{state.status.value}[/{style}]",
                    f"Engine:     {state.engine}",
                    f"Branch:     {state.branch or '(not created)'}",
                    f"Dry run:    {'yes' if state.dry_run else 'no'}",
                    f"Created:    {state.created_at:%Y-%m-%d %H:%M}",
                    f"Updated:    {state.updated_at:%Y-%m-%d %H:%M}",
                ]
                + ([f"Last error: {state.last_error}"] if state.last_error else [])
            ),
            title=f"[bold]Run {state.run_id}[/bold]",
            border_style="cyan",
            padding=(0, 2),
        )
    )

    if state.approvals:
        table = Table(title="Approvals", show_header=True)
        table.add_column("Gate")
        table.add_column("Decision")
        table.add_column("Actor")
        table.add_column("When", style="muted")
        table.add_column("Comment")
        for record in state.approvals:
            table.add_row(
                record.gate,
                record.decision.value,
                record.actor,
                record.at.strftime("%Y-%m-%d %H:%M"),
                record.comment or "",
            )
        console.print(table)

    if state.artifacts:
        table = Table(title="Artifacts", show_header=True)
        table.add_column("Name")
        table.add_column("Path", style="muted")
        run_root = store.paths(state.run_id).root
        for name, rel in sorted(state.artifacts.items()):
            table.add_row(name, str(run_root / rel))
        console.print(table)

    history = Table(title="History", show_header=True)
    history.add_column("When", style="muted")
    history.add_column("Transition")
    history.add_column("Note")
    for change in state.history[-15:]:
        arrow = f"{change.from_status.value if change.from_status else '—'} → {change.to_status.value}"
        history.add_row(change.at.strftime("%H:%M:%S"), arrow, change.note or "")
    console.print(history)


CHECK_ICON = {
    CheckStatus.PASSED: "[ok]pass[/ok]",
    CheckStatus.FAILED: "[fail]fail[/fail]",
    CheckStatus.SKIPPED: "[muted]skip[/muted]",
    CheckStatus.ERRORED: "[fail]error[/fail]",
}
