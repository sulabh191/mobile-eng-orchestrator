"""Generate VS Code assets: tasks that call the same CLI.

Written into ``<repo>/.vscode/`` so a developer can run the orchestrator from
the command palette without learning the flags.
"""

from __future__ import annotations

import json
from pathlib import Path

TASKS_VERSION = "2.0.0"

TASKS: list[dict] = [
    {
        "label": "Orchestrator: plan a ticket",
        "type": "shell",
        "command": "orc run ${input:issueKey} --stop-after plan",
        "problemMatcher": [],
        "presentation": {"reveal": "always", "panel": "dedicated"},
    },
    {
        "label": "Orchestrator: run full workflow",
        "type": "shell",
        "command": "orc run ${input:issueKey}",
        "problemMatcher": [],
        "presentation": {"reveal": "always", "panel": "dedicated"},
    },
    {
        "label": "Orchestrator: status",
        "type": "shell",
        "command": "orc status --all",
        "problemMatcher": [],
    },
    {
        "label": "Orchestrator: approve current gate",
        "type": "shell",
        "command": "orc approve",
        "problemMatcher": [],
    },
    {
        "label": "Orchestrator: re-run validation",
        "type": "shell",
        "command": "orc validate",
        "problemMatcher": [],
    },
    {
        "label": "Orchestrator: inspect repository",
        "type": "shell",
        "command": "orc inspect",
        "problemMatcher": [],
    },
]

INPUTS = [
    {
        "id": "issueKey",
        "type": "promptString",
        "description": "Jira issue key (e.g. MOB-101)",
    }
]


def install_vscode_assets(repo: Path, *, dry_run: bool = False) -> Path:
    """Merge the orchestrator's tasks into ``<repo>/.vscode/tasks.json``."""
    target = Path(repo) / ".vscode" / "tasks.json"
    payload: dict = {"version": TASKS_VERSION, "tasks": [], "inputs": []}

    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8")) or payload
        except json.JSONDecodeError:
            # A malformed tasks.json is the user's file; do not clobber it silently.
            raise
        payload.setdefault("version", TASKS_VERSION)
        payload.setdefault("tasks", [])
        payload.setdefault("inputs", [])

    managed = {task["label"] for task in TASKS}
    payload["tasks"] = [t for t in payload["tasks"] if t.get("label") not in managed] + TASKS

    existing_inputs = {i.get("id") for i in payload["inputs"]}
    payload["inputs"] += [i for i in INPUTS if i["id"] not in existing_inputs]

    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target
