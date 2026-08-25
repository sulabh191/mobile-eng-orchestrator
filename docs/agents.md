# Agents

Every agent has one responsibility, one output model, and no knowledge of any other agent.
`orc agents` prints this table live from the registry.

| Agent | Phase | Consumes | Produces | Uses a model | May write |
| --- | --- | --- | --- | --- | --- |
| `jira-fetcher` | fetch | issue key | `TrackerIssue` | no | no |
| `requirements` | requirements | `TrackerIssue` | `RequirementsDoc` | yes | no |
| `planner` | plan | `RequirementsDoc` | `ImplementationPlan` | yes | no |
| `implementer` | implement | `ImplementationPlan` | `ImplementationResult` | yes | **yes** |
| `validator` | validate | `ImplementationResult` | `ValidationReport` | no | no |
| `reviewer` | review | `ValidationReport` | `ReviewSummary` | yes | no |
| `delivery` | deliver | `ReviewSummary` | `DeliveryResult` | no | **yes** |
| `platform-ios` | advise | `RepoProfile` | `PreflightReport` | no | no |
| `platform-android` | advise | `RepoProfile` | `PreflightReport` | no | no |

## jira-fetcher

The only component that talks to the tracker. Deterministic: no model is involved in
reading a ticket. It normalises whatever the tracker returned into a `TrackerIssue` —
tracker-specific shapes (ADF, custom fields, transition ids) never leave
`integrations/jira/`. It refuses a ticket with an empty summary rather than working from
nothing, and it is also what posts the PR link back to the ticket at the end.

## requirements

Turns ticket prose into numbered, independently testable requirements, plus non-goals,
assumptions and open questions. Anything it had to infer goes to `assumptions`; anything
that would change the implementation and cannot be inferred goes to `open_questions` —
which is the only thing that triggers the requirements gate. Requirement ids (`R1`, `R2`,
…) are referenced by plan steps and by the review's coverage map, so they are the thread
that runs through the whole pipeline.

## planner

Reads the repository (read-only, enforced by the tool allowlist) so the plan names real
files. Produces ordered steps with dependencies, target files, the requirement ids each
step satisfies, per-step verification and a risk rating. After the model responds, the
agent computes which requirements no step satisfies and appends them as a risk — a coverage
gap is surfaced rather than left for review to notice.

The plan is what you approve. Nothing has been written to the repository at this point.

## implementer

The only agent that edits code, running only after the plan gate. It works step by step
through the approved plan in EDIT mode.

Its self-report is not trusted: after the engine returns, the agent runs `git diff
--numstat` plus `git status` and **replaces** the reported file list with what actually
changed, recording any discrepancy as a note on the run. It also notices if the engine
committed something (HEAD moved) — which it should never do — and flags plan steps that
were neither completed nor explicitly skipped.

## validator

No model. It assembles the platform's check plan and runs it. Guard checks first
(protected paths, blast radius, conflict markers, working-tree sanity), then format, lint,
build and test. A check whose tooling is absent is *skipped with a reason*. Optional checks
can fail without failing the run; required ones cannot.

## reviewer

Reads the diff, the requirements and the validation report and writes the summary a human
needs: what changed, a requirement-by-requirement coverage map (`covered` / `partial` /
`missing`), risk notes, a reviewer checklist of things only a human can confirm, and the
proposed commit message and PR body.

## delivery

Every destructive operation in the system lives here, behind the delivery gate: branch,
stage, commit, push, open the pull request. It refuses to commit to a protected branch,
never force-pushes and never rewrites history. If the push succeeds but the PR call fails,
it records the failure and keeps the pushed branch rather than unwinding work. Under
`--dry-run` it reports the plan and touches nothing.

## Platform specialists

These do not own a phase. They contribute platform guidance to prompts and run cheap
pre-flight checks before any expensive work starts — missing Gradle wrapper, missing
`Pods/`, not running on macOS, SwiftLint configured but not installed. A blocker stops the
run immediately; warnings are printed and recorded.

## Skills

Prompts are assembled from four parts: role, repository context, applicable skills, and
the task payload. Skills are markdown files with frontmatter (`name`, `description`,
`applies_to`, `tags`) loaded from three places, later winning on name collision:

1. the built-in library shipped with the orchestrator,
2. `~/.config/engineering-orchestrator/skills/`,
3. `<repo>/.orchestrator/skills/`.

So a team encodes its own conventions by dropping a file into the repository — no fork, no
code change. `orc skills` lists them; `orc skills --show <name>` prints one.
