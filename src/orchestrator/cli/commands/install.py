"""`orc install` — register the orchestrator with Claude Code and VS Code."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from orchestrator.cli.common import handle_errors, resolve_repo
from orchestrator.clients.claude_code import (
    claude_home,
    install_claude_code_assets,
    uninstall_claude_code_assets,
)
from orchestrator.clients.vscode import install_vscode_assets
from orchestrator.core.logging import console

app = typer.Typer(help="Register agents, skills and commands with your editors.", no_args_is_help=False)


@app.callback(invoke_without_command=True)
@handle_errors
def install(
    ctx: typer.Context,
    all_clients: bool = typer.Option(False, "--all", help="Install for every supported client."),
    claude: bool = typer.Option(False, "--claude-code", help="Install Claude Code agents/skills/commands."),
    vscode: bool = typer.Option(False, "--vscode", help="Install VS Code tasks into the target repo."),
    repo: Optional[Path] = typer.Option(None, "--repo", "-C", help="Repository for VS Code tasks."),
    target: Optional[Path] = typer.Option(None, "--claude-dir", help="Override the Claude Code config directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be written."),
) -> None:
    """Install client assets. With no flags, installs Claude Code assets."""
    if ctx.invoked_subcommand is not None:
        return

    do_claude = all_clients or claude or not (claude or vscode)
    do_vscode = all_clients or vscode

    if do_claude:
        assets = install_claude_code_assets(target=target, dry_run=dry_run)
        table = Table(title=f"Claude Code assets → {target or claude_home()}")
        table.add_column("Kind")
        table.add_column("Name")
        table.add_column("Path", style="muted")
        for asset in assets:
            table.add_row(asset.kind, asset.name, str(asset.path))
        console.print(table)

    if do_vscode:
        repo_path = resolve_repo(repo)
        written = install_vscode_assets(repo_path, dry_run=dry_run)
        console.print(f"[ok]VS Code tasks[/ok] → {written}")

    if dry_run:
        console.print("[muted]Dry run: nothing was written.[/muted]")
    else:
        console.print(
            "[muted]Restart Claude Code (or reload VS Code) to pick up the new "
            "agents, skills and commands.[/muted]"
        )


@app.command("uninstall")
@handle_errors
def uninstall(
    target: Optional[Path] = typer.Option(None, "--claude-dir"),
) -> None:
    """Remove only the files the orchestrator generated."""
    removed = uninstall_claude_code_assets(target=target)
    for path in removed:
        console.print(f"[muted]removed[/muted] {path}")
    console.print(f"[ok]Removed {len(removed)} generated file(s).[/ok]")
