"""The `orc` command-line entry point."""

from __future__ import annotations

import typer

from orchestrator import __version__
from orchestrator.cli.commands import config as config_cmd
from orchestrator.cli.commands import diagnostics, install as install_cmd, run as run_cmd
from orchestrator.core.logging import console

app = typer.Typer(
    name="orc",
    help=(
        "Engineering Orchestrator — drive a ticket through requirements, plan, "
        "implementation, validation, review and delivery, with a human gate at every "
        "irreversible step."
    ),
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)

# Workflow
app.command("run")(run_cmd.run_command)
app.command("resume")(run_cmd.resume_command)
app.command("status")(run_cmd.status_command)
app.command("approve")(run_cmd.approve_command)
app.command("reject")(run_cmd.reject_command)
app.command("show")(run_cmd.show_command)
app.command("audit")(run_cmd.audit_command)

# Diagnostics and discovery
app.command("doctor")(diagnostics.doctor_command)
app.command("inspect")(diagnostics.inspect_command)
app.command("agents")(diagnostics.agents_command)
app.command("skills")(diagnostics.skills_command)
app.command("validate")(diagnostics.validate_command)
app.command("engines")(diagnostics.engines_command)

# Sub-apps
app.add_typer(config_cmd.app, name="config")
app.add_typer(install_cmd.app, name="install")


@app.command("version")
def version() -> None:
    """Print the orchestrator version."""
    console.print(f"mobile-eng-orchestrator {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
