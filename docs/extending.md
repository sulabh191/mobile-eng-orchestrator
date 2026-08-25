# Extending the orchestrator

Four of the five common extensions need no Python at all.

## Add a skill (no code)

Drop a markdown file into `<repo>/.orchestrator/skills/` (team-wide, committed) or
`~/.config/engineering-orchestrator/skills/` (yours only):

```markdown
---
name: our-networking-layer
description: How this codebase does networking.
applies_to: [ios]
tags: [implementation, planning]
---

- All requests go through `APIClient`; never call `URLSession` directly.
- Endpoints are declared in `Endpoints.swift` as static factory methods.
- Errors surface as `APIError`; never leak `URLError` above the client layer.
```

`tags` decide which prompts it reaches (`requirements`, `planning`, `implementation`,
`review`, `delivery`, `testing`); `applies_to` restricts it to a platform. A file whose
`name` matches a built-in one replaces it. Verify with `orc skills` and
`orc skills --show our-networking-layer`.

## Add a validation check (no code)

```yaml
validation:
  extra_checks:
    - name: custom:no-print-statements
      description: Production code contains no stray print statements.
      command: ["./scripts/no-prints.sh"]
      required: true
      timeout: 120
```

`orc validate --list` shows the resulting plan; `orc validate` runs it.

## Add a slash command or subagent for Claude Code

They are generated from the registry and the skill library, so:

```bash
orc install --claude-code
```

regenerates `~/.claude/agents/orc-*.md`, `~/.claude/commands/orc-*.md` and
`~/.claude/skills/orc-*/SKILL.md`. `orc install uninstall` removes only the files the
generator owns and leaves your own alone.

## Add an agent

```python
# src/orchestrator/agents/security_review.py
from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.prompts import compose, skills_block, system_prompt
from orchestrator.core.models import StrictModel
from orchestrator.engine.base import EngineMode, EngineRequest


class SecurityFindings(StrictModel):
    issue_key: str
    findings: list[str] = []
    severity: str = "none"


class SecurityReviewAgent(Agent):
    name = "security-review"
    responsibility = "Flag security-relevant changes before delivery."
    output_model = SecurityFindings

    def run(self, ctx: AgentContext, **kwargs) -> SecurityFindings:
        findings, _ = ctx.engine.generate_structured(
            EngineRequest(
                task="security-review",
                system=system_prompt("review a diff for security-relevant changes"),
                prompt=compose(
                    skills_block(ctx.blackboard["skills"], "review", platform=ctx.profile.platform),
                    "## Task\n\nList security-relevant changes in the working tree.",
                ),
                mode=EngineMode.READ_ONLY,
                cwd=ctx.repo_path,
            ),
            SecurityFindings,
        )
        self.emit(ctx, findings)
        return findings
```

Register it:

```python
AGENT_REGISTRY["security-review"] = AgentSpec(
    SecurityReviewAgent, "review", "ImplementationResult", "SecurityFindings", True
)
```

It now appears in `orc agents` and gets a Claude Code subagent on the next
`orc install`. To put it in the pipeline, add a status to `WorkflowStatus`, an edge to
`TRANSITIONS`, and a handler to the dispatch dict in `Orchestrator.advance`.

## Add a platform

1. **Detection** — add markers to `MARKERS` in `inspection/detector.py` and a profile
   model in `inspection/profile.py`.
2. **Checks** — add `validation/<platform>.py` exposing `<platform>_checks(profile,
   settings)` and wire it into `build_check_plan`.
3. **Specialist** — add `agents/platform/<platform>.py` subclassing `PlatformAgent` with
   `preflight()` and `guidance()`, and register it in `get_platform_agent`.
4. **Skills** — add `<platform>-conventions.md` to the skill library with
   `applies_to: [<platform>]`.

Nothing in the workflow, the CLI or the agents changes.

## Add an issue tracker

Implement `JiraClientProtocol` — `get_issue`, `search`, `add_comment`,
`available_transitions`, `transition`, `health_check` — mapping onto `TrackerIssue`, raise
`IssueTrackerError` subclasses for every failure, and return it from `build_jira_client`
based on a config value. Because nothing outside `integrations/jira/` knows what Jira is,
that single class is the whole port. `MockJiraClient` is a complete worked example.

## Add an engine backend

Subclass `Engine`, implement `complete()` and `available()`, and add it to
`engine/factory.py`. Schema validation, repair-on-failure and transcript handling come
from the base class.

## Testing your extension

The suite is fully offline. Copy the patterns in `tests/`: build a synthetic repository
with the `ios_repo` / `android_repo` fixtures, use `MockEngine` and `MockJiraClient`, and
assert on the resulting state, artifacts and audit events. `MockEngine(fail_task=...)`
forces a failure so you can test recovery paths.
