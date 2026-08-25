"""`orc doctor`, `orc inspect`, `orc agents`, `orc skills`, `orc validate`."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from orchestrator.agents.base import AgentContext
from orchestrator.agents.platform.base import get_platform_agent
from orchestrator.agents.registry import describe_agents
from orchestrator.agents.validator import ValidationAgent
from orchestrator.cli.common import (
    CHECK_ICON,
    get_settings,
    handle_errors,
    parse_platform,
    resolve_repo,
)
from orchestrator.core.config import global_config_dir, load_settings
from orchestrator.core.credentials import CredentialStore, redact
from orchestrator.core.logging import console
from orchestrator.core.process import which
from orchestrator.core.state import StateStore
from orchestrator.engine.factory import build_engine, known_backends
from orchestrator.inspection.detector import inspect_repository
from orchestrator.integrations.git.repo import GitRepo
from orchestrator.skills.loader import load_skills


@handle_errors
def doctor_command(
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    check_jira: bool = typer.Option(False, "--jira", help="Also probe Jira connectivity."),
) -> None:
    """Check that everything the orchestrator needs is present and configured."""
    repo_path = resolve_repo(repo)
    settings = load_settings(repo_path)

    table = Table(title="Environment")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail", style="muted")

    def row(name: str, ok: bool, detail: str = "") -> None:
        table.add_row(name, "[ok]ok[/ok]" if ok else "[fail]missing[/fail]", detail)

    row("git", GitRepo.available(), which("git") or "install git")
    for binary in ("claude", "gh", "swiftlint", "xcodebuild", "java"):
        location = which(binary)
        table.add_row(
            binary,
            "[ok]found[/ok]" if location else "[muted]absent[/muted]",
            location or "optional, depending on platform",
        )

    engine = build_engine(settings)
    available, reason = engine.available()
    row(f"engine:{engine.name}", available, reason)

    config_file = global_config_dir() / "config.yaml"
    row("global config", config_file.exists(), str(config_file))

    credentials = CredentialStore(global_config_dir())
    for key in ("ORC_JIRA_API_TOKEN", "ORC_GITHUB_TOKEN"):
        value, source = credentials.get(key)
        table.add_row(
            key,
            "[ok]set[/ok]" if value else "[muted]unset[/muted]",
            f"{redact(value)} ({source.name})",
        )

    console.print(table)

    profile = inspect_repository(repo_path)
    console.print(
        Panel(
            profile.as_prompt_block(),
            title=f"[bold]Repository[/bold] — {profile.summary_line()}",
            border_style="cyan",
        )
    )
    for note in profile.notes:
        console.print(f"[muted]note: {note}[/muted]")

    runs = StateStore(repo_path).list_runs()
    console.print(f"[muted]{len(runs)} orchestrator run(s) recorded in this repository.[/muted]")

    if check_jira:
        from orchestrator.integrations.jira.factory import build_jira_client

        client = build_jira_client(settings)
        info = client.health_check()
        console.print(Panel("\n".join(f"{k}: {v}" for k, v in info.items()), title="Jira"))


@handle_errors
def inspect_command(
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    platform: Optional[str] = typer.Option(None, "--platform"),
    as_json: bool = typer.Option(False, "--json"),
    signals: bool = typer.Option(False, "--signals", help="Show the raw detection evidence."),
) -> None:
    """Detect the platform and capabilities of a repository."""
    repo_path = resolve_repo(repo)
    profile = inspect_repository(repo_path, platform_override=parse_platform(platform))

    if as_json:
        console.print_json(profile.model_dump_json())
        return

    console.print(
        Panel(profile.as_prompt_block(), title=profile.summary_line(), border_style="cyan")
    )
    for note in profile.notes:
        console.print(f"[muted]note: {note}[/muted]")

    if signals:
        table = Table(title="Detection signals")
        table.add_column("Platform")
        table.add_column("Marker")
        table.add_column("Weight", justify="right")
        table.add_column("Path", style="muted")
        for signal in sorted(profile.signals, key=lambda s: -s.weight):
            table.add_row(signal.platform.display, signal.marker, str(signal.weight), signal.path or "")
        console.print(table)


@handle_errors
def agents_command(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List the agents, their responsibilities and their data contracts."""
    rows = describe_agents()
    if as_json:
        import json

        console.print_json(json.dumps(rows))
        return

    table = Table(title="Agents")
    table.add_column("Agent", style="agent")
    table.add_column("Phase")
    table.add_column("Consumes", style="muted")
    table.add_column("Produces", style="muted")
    table.add_column("Engine")
    table.add_column("Writes")
    table.add_column("Responsibility")
    for row in rows:
        table.add_row(
            row["name"],
            row["phase"],
            row["consumes"],
            row["produces"],
            row["engine"],
            "[fail]yes[/fail]" if row["writes"] == "yes" else "no",
            row["responsibility"],
        )
    console.print(table)


@handle_errors
def skills_command(
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    platform: Optional[str] = typer.Option(None, "--platform"),
    show: Optional[str] = typer.Option(None, "--show", help="Print one skill's full text."),
) -> None:
    """List (or print) the skills available to this repository."""
    repo_path = resolve_repo(repo)
    library = load_skills(
        user_dir=global_config_dir() / "skills",
        repo_dir=repo_path / ".orchestrator" / "skills",
    )

    if show:
        skill = library.get(show)
        if skill is None:
            raise typer.BadParameter(f"Unknown skill '{show}'. Known: {', '.join(library.names())}")
        console.print(Panel(skill.body.strip(), title=skill.name, border_style="cyan"))
        return

    wanted = parse_platform(platform)
    table = Table(title="Skills")
    table.add_column("Name")
    table.add_column("Applies to")
    table.add_column("Tags", style="muted")
    table.add_column("Source", style="muted")
    table.add_column("Description")
    for skill in sorted(library, key=lambda s: s.name):
        if wanted and not skill.matches(wanted):
            continue
        table.add_row(
            skill.name,
            ", ".join(p.display for p in skill.applies_to) or "any",
            ", ".join(skill.tags),
            skill.source,
            skill.description,
        )
    console.print(table)


@handle_errors
def validate_command(
    run_id: Optional[str] = typer.Argument(None, help="Run id (default: most recent)."),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    platform: Optional[str] = typer.Option(None, "--platform"),
    list_only: bool = typer.Option(False, "--list", help="Show the check plan without running it."),
) -> None:
    """Run (or list) the platform's validation checks for this repository."""
    from orchestrator.validation.base import build_check_plan

    repo_path = resolve_repo(repo)
    settings = get_settings(repo_path)
    profile = inspect_repository(repo_path, platform_override=parse_platform(platform))

    if list_only:
        plan = build_check_plan(profile, settings)
        table = Table(title=f"Check plan — {profile.platform.display}")
        table.add_column("Check")
        table.add_column("Category")
        table.add_column("Required")
        table.add_column("Command", style="muted")
        for check in plan.checks:
            table.add_row(
                check.name,
                check.category,
                "yes" if check.required else "no",
                check.display_command,
            )
        console.print(table)
        return

    store = StateStore(repo_path)
    state = store.resolve(run_id)
    ctx = AgentContext(
        settings=settings,
        store=store,
        state=state,
        profile=profile,
        engine=build_engine(settings, backend="mock"),
    )
    ctx.blackboard["platform_agent"] = get_platform_agent(profile.platform)
    report = ValidationAgent().run(ctx, attempt=state.attempts.get("validation", 0) + 1)

    table = Table(title=f"Validation — {'passed' if report.passed else 'FAILED'}")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Exit", justify="right")
    table.add_column("Duration", justify="right")
    for check in report.checks:
        table.add_row(
            check.name,
            CHECK_ICON[check.status],
            str(check.exit_code if check.exit_code is not None else "–"),
            f"{check.duration_seconds:.1f}s",
        )
    console.print(table)
    if not report.passed:
        raise typer.Exit(code=1)


@handle_errors
def engines_command() -> None:
    """List engine backends and whether each is usable here."""
    settings = load_settings(None)
    table = Table(title="Engine backends")
    table.add_column("Backend")
    table.add_column("Status")
    table.add_column("Detail", style="muted")
    for name in known_backends():
        engine = build_engine(settings, backend=name)
        ok, reason = engine.available()
        table.add_row(
            name + (" (default)" if name == settings.engine.backend else ""),
            "[ok]available[/ok]" if ok else "[muted]unavailable[/muted]",
            reason,
        )
    console.print(table)
