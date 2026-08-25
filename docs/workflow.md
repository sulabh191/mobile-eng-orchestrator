# Workflow, gates and recovery

## The loop

`Orchestrator.advance()` is a loop over one dictionary: current status → handler. Each
handler does one phase's work and requests a transition. `WorkflowState.transition_to()`
consults the legal-transition graph and raises `StateError` on anything else.

That design has one consequence worth stating plainly: **the approval gates cannot be
skipped by a bug in an agent.** `PLAN_GENERATED → IMPLEMENTING` is not an edge in the
graph. The only way into `IMPLEMENTING` is through `PLAN_APPROVED`, and the only way into
`PLAN_APPROVED` is a recorded approval.

## States

```
INITIALIZED
    └─► JIRA_FETCHED
            └─► REQUIREMENTS_REVIEW ──(gate, only when questions are open)
                    └─► PLAN_GENERATED ──(gate: plan)──► PLAN_APPROVED
                            └─► IMPLEMENTING
                                    └─► IMPLEMENTATION_COMPLETE
                                            └─► VALIDATING
                                                   ├─► VALIDATION_FAILED ──► IMPLEMENTING (bounded)
                                                   └─► VALIDATION_PASSED
                                                           └─► REVIEW_READY
                                                                   └─► READY_FOR_PR ──(gate: delivery)
                                                                           ├─► PR_CREATED ─► COMPLETED
                                                                           └─► COMPLETED (commit/push only, or dry run)
REJECTED   terminal — a gate was rejected
FAILED     recoverable — `orc resume` re-enters the phase that failed
```

## Gates

| Gate | When | What you see | Default when non-interactive |
| --- | --- | --- | --- |
| `requirements` | Only if the requirements agent raised open questions | Requirements doc and the questions | stop |
| `plan` | Always, before any file is touched | Full plan: steps, files, risks, test strategy | stop |
| `delivery` | Always, before commit/push/PR | Review summary, coverage map, branch, remote, commit and PR text | stop |

Three ways through a gate:

1. **Interactively** — approve, reject, or request changes with a comment. Requesting
   changes re-runs the phase with your comment in the prompt.
2. **Out of band** — the run stops with `ApprovalRequired`; later, `orc approve` records
   the decision and continues.
3. **Auto-approval** — `--yes`, which refuses to work unless `ORC_I_UNDERSTAND_AUTO_APPROVE=1`
   is exported. It skips every human check including push; use it in CI, not on a laptop.

Every decision is written to the state and to the audit log with actor, timestamp and
comment.

## Validation and remediation

Validation runs the guard checks first (cheap, in-process), then the platform's format,
lint, build and test commands in cheapest-first order. Missing tooling is *skipped* with a
reason, never silently passed.

On failure the workflow re-enters `IMPLEMENTING` with the failing check output in the
prompt, up to `validation.max_remediation_attempts` times (default 2). When the budget is
exhausted the run fails with a `ValidationFailed` error naming the failing checks — it does
not loop, and it does not proceed.

## Resume

Run state, artifacts and the audit log live in
`<repo>/.orchestrator/runs/<run-id>/`. Resuming:

1. loads the state file;
2. rehydrates every artifact the run already produced into the blackboard;
3. re-runs pre-flight;
4. re-enters the loop at the stored status.

A run that failed re-enters the phase that failed — except a failed *validation*, which
re-enters at `VALIDATING`, because the developer has probably just fixed something by hand
and the checks deserve a fresh run before the remediation budget is consulted again.

```bash
orc status --all          # every run in this repository
orc status <run-id>       # detail: approvals, artifacts, history
orc resume <run-id>       # continue
orc audit <run-id>        # the full event log
```

## Stopping early

`--stop-after <phase>` halts cleanly after `fetch`, `requirements`, `plan`, `implement`,
`validate`, `review` or `deliver`. `--stop-after plan` is the common one: it produces the
plan and stops *before* the approval gate, so you can read `orc show plan.md` and decide in
your own time.

`--dry-run` runs everything including validation, then reports what it would have
committed and pushed without doing either.
