# Architecture

## Layers

```
                 ┌──────────────────────────────────────────────┐
  clients        │  orc CLI   ·   Claude Code   ·   VS Code      │
                 └───────────────────────┬──────────────────────┘
                                         │  all call the same core
                 ┌───────────────────────▼──────────────────────┐
  workflow       │  Orchestrator: status → handler → transition │
                 │  approval gates · resume · audit             │
                 └───────────────────────┬──────────────────────┘
                 ┌───────────────────────▼──────────────────────┐
  agents         │  fetcher · requirements · planner ·          │
                 │  implementer · validator · reviewer ·        │
                 │  delivery  (+ iOS / Android specialists)     │
                 └───────┬───────────────┬──────────────┬───────┘
                         │               │              │
        ┌────────────────▼───┐  ┌────────▼───────┐  ┌───▼────────────────┐
  infra │ engines            │  │ integrations   │  │ validation         │
        │ claude_code · sdk  │  │ jira · git     │  │ ios · android ·    │
        │ mock               │  │ github/gitlab  │  │ generic · guards   │
        └────────────────────┘  └────────────────┘  └────────────────────┘
                 ┌──────────────────────────────────────────────┐
  core           │ models · state machine · config · secrets ·  │
                 │ process · logging · approvals · skills       │
                 └──────────────────────────────────────────────┘
```

Dependencies point downwards only. `core` imports nothing from the layers above it, which
is why the state machine and the models can be tested without a repository, a network or a
model.

## The three boundaries that matter

**1. Deterministic vs. generative.** Everything that can be decided by code is decided by
code: which platform this is, which checks to run, whether they passed, what the branch is
called, whether a transition is legal. The model is asked four questions only —
requirements, plan, implementation, review — and each answer must satisfy a JSON schema
before it is allowed into the state.

**2. Read vs. write.** Only two agents may change anything. The implementer edits files
inside the repository; the delivery agent runs git. Every other agent runs with a
read-only tool allowlist at the engine level, so "the planner must not edit code" is
enforced by the sandbox rather than by a sentence in a prompt.

**3. Orchestrator vs. target repository.** The orchestrator is installed globally and
holds no project state. Per-run state lives in the *target* repository under
`.orchestrator/`, which is added to `.git/info/exclude` automatically so it never lands in
a commit. Two engineers working on the same project therefore each have their own runs,
and a project can be orchestrated without adding a single tracked file to it.

## Data flow

Agents never hand each other prose. Each phase produces a pydantic model that is persisted
as an artifact and reloaded on resume:

| Phase | Produces | Artifacts written |
| --- | --- | --- |
| fetch | `TrackerIssue` | `jira-fetcher.json`, `issue.md` |
| requirements | `RequirementsDoc` | `requirements.json`, `requirements.md` |
| plan | `ImplementationPlan` | `planner.json`, `plan.md` |
| implement | `ImplementationResult` | `implementer.json` |
| validate | `ValidationReport` | `validator.json`, `validation-attempt-N.md` |
| review | `ReviewSummary` | `reviewer.json`, `review.md` |
| deliver | `DeliveryResult` | `delivery.json` |

Because every intermediate result is on disk in a typed form, a resumed run does not have
to redo earlier phases, and a human can read exactly what the system believed at each
step.

## Repository inspection

`inspect_repository()` walks the target repository to a bounded depth, skipping build
output and dependency caches, and collects weighted signals. The winning platform's score
relative to the runner-up becomes a confidence value; a hybrid repository therefore
reports low confidence rather than a confident guess. The resulting `RepoProfile` carries
the schemes, modules, linters, package managers, git state and convention files that later
prompts and check plans are built from.

## Engines

An engine answers one bounded question at a time. Three backends implement the same
interface:

- `claude_code` — drives the `claude` CLI headlessly with an explicit tool allowlist per
  mode, and `--permission-mode plan` for read-only phases.
- `agent_sdk` — the same thing in-process via the Claude Agent SDK, for CI machines
  without the CLI.
- `mock` — deterministic, offline, schema-valid output. The whole test suite and
  `--offline` runs use it.

`generate_structured()` validates every response against the phase's schema and, on
failure, re-asks once with the validation errors attached before giving up with a typed
error.

## Extensibility

The registry, the skill library, the check plan and the platform specialists are all data
rather than control flow:

- a new **skill** is a markdown file in `~/.config/mobile-eng-orchestrator/skills/` or
  `<repo>/.orchestrator/skills/`;
- a new **check** is an entry in `validation.extra_checks` or a `Check` in a platform
  module;
- a new **agent** is a class plus one line in `AGENT_REGISTRY`, and it becomes visible in
  `orc agents` and in Claude Code automatically;
- a new **platform** is a `PlatformAgent`, a detection marker set and a checks module;
- a new **tracker** is one class satisfying `JiraClientProtocol` — nothing outside
  `integrations/jira/` knows what Jira is.

See [extending.md](extending.md) for worked examples.
