# Troubleshooting

Start with `orc doctor` — it reports tooling, credentials, engine availability and what it
detected about the current repository.

## The run stopped and asked for approval

That is the design. `orc status` shows the pending gate, `orc show plan.md` (or
`review.md`) shows what you are approving, then `orc approve` continues. Use
`orc reject --request-changes -m "..."` to have the phase redone with your comment.

## "Illegal transition X -> Y"

Something tried to skip a phase, which the state machine refuses. `orc status <run-id>`
shows the history. If a run's state is genuinely wrong, start a new one — the old run's
artifacts stay on disk.

## Validation fails on tooling my machine does not have

Missing tooling is *skipped*, not failed. If a check is genuinely failing, read the output
with `orc show validation-attempt-1.md`. To exclude a check permanently:

```yaml
validation:
  skip: ["ios:xcodebuild-test"]
```

## Validation keeps failing and the run gave up

The remediation budget (`validation.max_remediation_attempts`, default 2) is deliberate: a
model that has failed twice on the same check usually needs a human. Fix the repository by
hand, then `orc resume` — a resumed run re-runs the checks before consulting the budget
again.

## "Nothing to commit"

The implementation produced no changes. Read `orc show implementer.json`: the agent records
plan steps it skipped and why, and any mismatch between what the engine claimed and what
`git status` showed.

## Wrong platform detected

`orc inspect --signals` shows the evidence and the weights. Pin it:

```bash
orc run MOB-123 --platform ios
```

Low confidence usually means a hybrid repository (a KMP project with an `iosApp` folder, a
monorepo with both apps). Pointing `--repo` at the specific app directory is often better
than overriding.

## Jira returns 401/403

The token is wrong, expired, or belongs to a different Atlassian account than
`jira.email`. Check with `orc config get-secret ORC_JIRA_API_TOKEN` (shows the source and
a redacted value) and `orc doctor --jira`. Jira Cloud API tokens are created at
id.atlassian.com, not in Jira itself.

## The pull request was not created

The commit and push already succeeded — that is recorded, not lost. Either install and
authenticate `gh` (`gh auth login`), or set `ORC_GITHUB_TOKEN`. Then open the PR by hand;
the run's `review.md` contains the drafted title and body.

## `claude: command not found`

The `claude_code` backend needs Claude Code installed and on PATH. Alternatives:
`engine.claude_binary` to point at it, `--engine agent_sdk` with
`pip install 'mobile-eng-orchestrator[sdk]'`, or `--engine mock --offline` to rehearse the
workflow without a model. `orc engines` shows what is usable.

## Runs are cluttering my repository

They live in `<repo>/.orchestrator/runs/` and are excluded from git automatically. Delete
old ones freely — nothing else references them.

## I want to see exactly what happened

```bash
orc audit <run-id>        # every event, in order
orc status <run-id>       # approvals, artifacts, transitions
ls .orchestrator/runs/<run-id>/artifacts/
```
