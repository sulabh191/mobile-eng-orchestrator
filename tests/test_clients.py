"""Generated client assets for Claude Code and VS Code."""

from __future__ import annotations

import json

from orchestrator.clients.claude_code import (
    install_claude_code_assets,
    uninstall_claude_code_assets,
)
from orchestrator.clients.vscode import install_vscode_assets


def test_claude_assets_cover_every_pipeline_agent(tmp_path):
    assets = install_claude_code_assets(target=tmp_path)
    agent_names = {a.name for a in assets if a.kind == "agent"}
    assert "orc-planner" in agent_names
    assert "orc-delivery" in agent_names
    assert (tmp_path / "agents" / "orc-planner.md").exists()


def test_claude_agent_file_has_frontmatter(tmp_path):
    install_claude_code_assets(target=tmp_path)
    text = (tmp_path / "agents" / "orc-planner.md").read_text()
    assert text.startswith("---\n")
    assert "name: orc-planner" in text
    assert "description:" in text


def test_read_only_agents_do_not_get_write_tools(tmp_path):
    install_claude_code_assets(target=tmp_path)
    planner = (tmp_path / "agents" / "orc-planner.md").read_text()
    implementer = (tmp_path / "agents" / "orc-implementer.md").read_text()
    assert "Edit" not in planner.split("---")[1]
    assert "Edit" in implementer.split("---")[1]


def test_commands_and_skills_are_installed(tmp_path):
    install_claude_code_assets(target=tmp_path)
    assert (tmp_path / "commands" / "orc-run.md").exists()
    assert (tmp_path / "skills" / "orc-ios-conventions" / "SKILL.md").exists()


def test_dry_run_writes_nothing(tmp_path):
    assets = install_claude_code_assets(target=tmp_path, dry_run=True)
    assert assets
    assert not (tmp_path / "agents").exists()


def test_uninstall_only_removes_generated_files(tmp_path):
    install_claude_code_assets(target=tmp_path)
    keep = tmp_path / "agents" / "my-own-agent.md"
    keep.write_text("mine", encoding="utf-8")

    removed = uninstall_claude_code_assets(target=tmp_path)
    assert keep.exists()
    assert not (tmp_path / "agents" / "orc-planner.md").exists()
    assert len(removed) > 5


def test_vscode_tasks_are_created(generic_repo):
    path = install_vscode_assets(generic_repo)
    payload = json.loads(path.read_text())
    labels = {task["label"] for task in payload["tasks"]}
    assert "Orchestrator: run full workflow" in labels
    assert payload["inputs"][0]["id"] == "issueKey"


def test_vscode_tasks_preserve_existing_user_tasks(generic_repo):
    target = generic_repo / ".vscode" / "tasks.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"version": "2.0.0", "tasks": [{"label": "My build", "type": "shell"}]}),
        encoding="utf-8",
    )
    install_vscode_assets(generic_repo)
    labels = {t["label"] for t in json.loads(target.read_text())["tasks"]}
    assert "My build" in labels
    assert "Orchestrator: status" in labels


def test_vscode_install_is_idempotent(generic_repo):
    install_vscode_assets(generic_repo)
    path = install_vscode_assets(generic_repo)
    tasks = json.loads(path.read_text())["tasks"]
    assert len(tasks) == len({t["label"] for t in tasks})
