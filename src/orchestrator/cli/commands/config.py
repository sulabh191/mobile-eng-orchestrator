"""`orc config` — inspect and edit configuration and secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.prompt import Prompt
from rich.syntax import Syntax
from rich.table import Table

from orchestrator.cli.common import handle_errors, resolve_repo
from orchestrator.core.config import (
    CONFIG_FILENAME,
    Settings,
    global_config_dir,
    load_settings,
    write_global_config,
)
from orchestrator.core.credentials import CredentialStore, redact
from orchestrator.core.logging import console

app = typer.Typer(help="Inspect and edit orchestrator configuration.", no_args_is_help=True)

SECRET_KEYS = ("ORC_JIRA_API_TOKEN", "ORC_GITHUB_TOKEN", "ORC_GITLAB_TOKEN")


@app.command("show")
@handle_errors
def show(
    repo: Optional[Path] = typer.Option(None, "--repo", "-C"),
    resolved: bool = typer.Option(True, "--resolved/--file", help="Show merged config or the raw file."),
) -> None:
    """Print the effective configuration (secrets are never included)."""
    repo_path = resolve_repo(repo)
    settings = load_settings(repo_path)
    if resolved:
        console.print(f"[muted]Layers applied: {' → '.join(settings.sources)}[/muted]")
        console.print(Syntax(settings.to_yaml(), "yaml", theme="ansi_dark"))
    else:
        target = global_config_dir() / CONFIG_FILENAME
        if not target.exists():
            console.print(f"[muted]No global config at {target}. Run `orc config init`.[/muted]")
            return
        console.print(Syntax(target.read_text(encoding="utf-8"), "yaml", theme="ansi_dark"))


@app.command("path")
@handle_errors
def path() -> None:
    """Print the global configuration directory."""
    console.print(str(global_config_dir()))


@app.command("init")
@handle_errors
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
    interactive: bool = typer.Option(True, "--interactive/--defaults"),
) -> None:
    """Create the global configuration file."""
    target = global_config_dir() / CONFIG_FILENAME
    if target.exists() and not force:
        console.print(f"[muted]{target} already exists. Use --force to overwrite.[/muted]")
        return

    settings = Settings()
    if interactive:
        settings.jira.base_url = Prompt.ask(
            "Jira base URL", default=settings.jira.base_url or "https://your-domain.atlassian.net"
        )
        settings.jira.email = Prompt.ask("Jira account email", default=settings.jira.email or "")
        settings.jira.default_project = Prompt.ask("Default project key", default="") or None
        settings.git.provider = Prompt.ask(
            "Git host", choices=["github", "gitlab", "none"], default=settings.git.provider
        )
        settings.git.default_base_branch = Prompt.ask(
            "Default base branch", default=settings.git.default_base_branch
        )
        settings.engine.backend = Prompt.ask(
            "Engine backend", choices=["claude_code", "agent_sdk", "mock"], default=settings.engine.backend
        )

    written = write_global_config(settings)
    console.print(f"[ok]Wrote[/ok] {written}")
    console.print(
        "[muted]Now store your Jira token: "
        "`orc config set-secret ORC_JIRA_API_TOKEN`[/muted]"
    )


@app.command("set-secret")
@handle_errors
def set_secret(
    key: str = typer.Argument(..., help=f"One of: {', '.join(SECRET_KEYS)}"),
    value: Optional[str] = typer.Option(None, "--value", help="Read from a prompt if omitted."),
) -> None:
    """Store a secret in the OS keychain (falling back to a 0600 dotenv)."""
    store = CredentialStore(global_config_dir())
    secret = value or Prompt.ask(f"Value for {key}", password=True)
    if not secret:
        raise typer.BadParameter("Empty value.")
    source = store.set(key, secret)
    console.print(f"[ok]Stored[/ok] {key} in {source.name} ({redact(secret)})")


@app.command("get-secret")
@handle_errors
def get_secret(key: str = typer.Argument(...)) -> None:
    """Show where a secret is stored, redacted."""
    store = CredentialStore(global_config_dir())
    value, source = store.get(key)
    console.print(f"{key}: {redact(value)}  [muted]({source.name} {source.detail})[/muted]")


@app.command("delete-secret")
@handle_errors
def delete_secret(key: str = typer.Argument(...)) -> None:
    """Remove a secret from the keychain and dotenv."""
    CredentialStore(global_config_dir()).delete(key)
    console.print(f"[ok]Removed[/ok] {key}")


@app.command("secrets")
@handle_errors
def list_secrets() -> None:
    """List known secrets and where each one resolves from."""
    store = CredentialStore(global_config_dir())
    table = Table(title="Credentials")
    table.add_column("Key")
    table.add_column("Value", style="muted")
    table.add_column("Source")
    for key in SECRET_KEYS:
        value, source = store.get(key)
        table.add_row(key, redact(value), source.name)
    console.print(table)
