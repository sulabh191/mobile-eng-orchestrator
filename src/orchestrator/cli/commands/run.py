"""`orc run`, `orc resume`, `orc status`, `orc approve`, `orc reject`, `orc show`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.syntax import Syntax

from orchestrator.cli.common import (
    get_settings,
    handle_errors,
    parse_platform,
    render_run_detail,
    render_runs,
    resolve_repo,
)
from orchestrator.core.errors import StateError
from orchestrator.core.logging import console
from orchestrator.core.models import Decision
from orchestrator.core.state import StateStore, WorkflowStatus
from orchestrator.workflow.orchestrator import STOP_POINTS, Orchestrator, RunOptions

app = typer.Typer(no_args_is_help=True)


@handle_errors
def run_command(
    issue_key: str = typer.Argument(..., help="Issue key, e.g. MOB-101."),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C", help="Target repository (default: cwd)."),
    platform: Optional[str] = typer.Option(None, "--platform", help="Force ios | android | generic."),
    engine: Optional[str] = typer.Option(None, "--engine", help="claude_code | agent_sdk | mock."),
    offline: bool = typer.Option(False, "--offline", help="Use fixture issues instead of Jira."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Never commit, push or open a PR."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve every gate (requires the safety env var)."),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Stop at gates instead of prompting."
    ),
    stop_after: str = typer.Option(
        None, "--stop-after", help=f"Stop after a phase: {', '.join(STOP_POINTS)}."
    ),
    base: Optional[str] = typer.Option(None, "--base", help="Base branch for the pull request."),
    branch: Optional[str] = typer.Option(None, "--branch", help="Override the generated branch name."),
    draft: bool = typer.Option(False, "--draft", help="Open the pull request as a draft."),
    fixtures: Optional[Path] = typer.Option(None, "--fixtures", help="Directory of offline issue fixtures."),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="DEBUG | INFO | WARNING | ERROR."),
) -> None:
    """Run the orchestration workflow for one ticket."""
    if stop_after and stop_after not in STOP_POINTS:
        raise typer.BadParameter(f"--stop-after must be one of: {', '.join(STOP_POINTS)}")

    repo_path = resolve_repo(repo)
    settings = get_settings(repo_path, log_level=log_level)
    orchestrator = Orchestrator(settings, repo_path=repo_path)

    options = RunOptions(
        issue_key=issue_key.upper(),
        repo_path=repo_path,
        platform_override=parse_platform(platform),
        engine_backend=engine,
        offline=offline,
        dry_run=dry_run,
        interactive=not non_interactive,
        auto_approve=yes,
        stop_after=stop_after,
        fixtures_dir=fixtures,
        base_branch=base,
        branch=branch,
        draft_pr=draft,
    )
    state = orchestrator.start(options)
    console.print(f"[muted]Run {state.run_id} is at {state.status.value}.[/muted]")


@handle_errors
def resume_command(
    run_id: Optional[str] = typer.Argument(None, help="Run id (default: the most recent run)."),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    engine: Optional[str] = typer.Option(None, "--engine"),
    log_level: Optional[str] = typer.Option(None, "--log-level"),
) -> None:
    """Continue a run that stopped at a gate, failed, or was interrupted."""
    repo_path = resolve_repo(repo)
    settings = get_settings(repo_path, log_level=log_level)
    store = StateStore(repo_path)
    state = store.resolve(run_id)

    orchestrator = Orchestrator(settings, repo_path=repo_path)
    options = RunOptions(
        issue_key=state.issue_key,
        repo_path=repo_path,
        engine_backend=engine,
        interactive=not non_interactive,
        dry_run=state.dry_run,
        offline=state.engine == "mock",
    )
    state = orchestrator.resume(state.run_id, options)
    console.print(f"[muted]Run {state.run_id} is at {state.status.value}.[/muted]")


@handle_errors
def status_command(
    run_id: Optional[str] = typer.Argument(None, help="Run id (default: the most recent run)."),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    all_runs: bool = typer.Option(False, "--all", "-a", help="List every run in this repository."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show the state of a run (or list them all)."""
    repo_path = resolve_repo(repo)
    store = StateStore(repo_path)

    if all_runs:
        runs = store.list_runs()
        if as_json:
            console.print_json(json.dumps([r.model_dump(mode="json") for r in runs]))
            return
        if not runs:
            console.print(f"[muted]No orchestrator runs in {repo_path}.[/muted]")
            return
        console.print(render_runs(runs))
        return

    state = store.resolve(run_id)
    if as_json:
        console.print_json(state.model_dump_json())
        return
    render_run_detail(store, state)


@handle_errors
def approve_command(
    run_id: Optional[str] = typer.Argument(None),
    gate: Optional[str] = typer.Option(None, "--gate", help="Gate name (default: the one the run is waiting on)."),
    comment: Optional[str] = typer.Option(None, "--comment", "-m"),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    then_resume: bool = typer.Option(True, "--resume/--no-resume", help="Continue the run afterwards."),
) -> None:
    """Approve the gate a run is waiting on, then continue it."""
    repo_path = resolve_repo(repo)
    settings = get_settings(repo_path)
    store = StateStore(repo_path)
    state = store.resolve(run_id)

    target = gate or _pending_gate(state.status)
    if target is None:
        raise StateError(f"Run {state.run_id} is at {state.status.value} and is not awaiting approval.")

    state.record_approval(target, Decision.APPROVED, comment=comment)
    store.save(state)
    console.print(f"[ok]Approved[/ok] gate '{target}' for run {state.run_id}.")

    if then_resume:
        Orchestrator(settings, repo_path=repo_path).resume(
            state.run_id,
            RunOptions(
                issue_key=state.issue_key,
                repo_path=repo_path,
                interactive=False,
                dry_run=state.dry_run,
                offline=state.engine == "mock",
            ),
        )


@handle_errors
def reject_command(
    run_id: Optional[str] = typer.Argument(None),
    gate: Optional[str] = typer.Option(None, "--gate"),
    comment: Optional[str] = typer.Option(None, "--comment", "-m", help="What was wrong."),
    changes: bool = typer.Option(
        False, "--request-changes", help="Ask for a revision instead of stopping the run."
    ),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
) -> None:
    """Reject a gate, or request changes so the phase is redone."""
    repo_path = resolve_repo(repo)
    store = StateStore(repo_path)
    state = store.resolve(run_id)

    target = gate or _pending_gate(state.status)
    if target is None:
        raise StateError(f"Run {state.run_id} is at {state.status.value} and is not awaiting approval.")

    decision = Decision.CHANGES_REQUESTED if changes else Decision.REJECTED
    state.record_approval(target, decision, comment=comment)
    if not changes:
        state.transition_to(WorkflowStatus.REJECTED, note=comment or "rejected by developer")
    store.save(state)
    console.print(f"[muted]Recorded {decision.value} on gate '{target}' for run {state.run_id}.[/muted]")


@handle_errors
def show_command(
    artifact: str = typer.Argument(..., help="Artifact name, e.g. plan.md, requirements.md, review.md."),
    run_id: Optional[str] = typer.Option(None, "--run"),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
) -> None:
    """Print an artifact produced by a run."""
    repo_path = resolve_repo(repo)
    store = StateStore(repo_path)
    state = store.resolve(run_id)

    content = store.read_artifact(state, artifact)
    if content is None:
        available = ", ".join(sorted(state.artifacts)) or "none yet"
        raise StateError(f"No artifact '{artifact}' in run {state.run_id}.", hint=f"Available: {available}")

    lexer = "markdown" if artifact.endswith(".md") else "json"
    console.print(Syntax(content, lexer, theme="ansi_dark", word_wrap=True))


@handle_errors
def audit_command(
    run_id: Optional[str] = typer.Argument(None),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
) -> None:
    """Print the append-only audit log for a run."""
    repo_path = resolve_repo(repo)
    store = StateStore(repo_path)
    state = store.resolve(run_id)
    for event in store.read_audit(state.run_id):
        detail = json.dumps(event.detail, default=str)
        console.print(
            f"[muted]{event.at:%Y-%m-%d %H:%M:%S}[/muted] "
            f"[agent]{event.actor}[/agent] {event.event} {detail}"
        )


def _pending_gate(status: WorkflowStatus) -> str | None:
    from orchestrator.core.approvals import Gate

    return {
        WorkflowStatus.REQUIREMENTS_REVIEW: Gate.REQUIREMENTS,
        WorkflowStatus.PLAN_GENERATED: Gate.PLAN,
        WorkflowStatus.READY_FOR_PR: Gate.DELIVERY,
        WorkflowStatus.REVIEW_READY: Gate.DELIVERY,
    }.get(status)
