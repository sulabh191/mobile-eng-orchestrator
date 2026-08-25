# Engineering Orchestrator

A cross-platform AI engineering orchestration framework for iOS and Android teams.

Clone it once, install it globally, and run it from inside *any* app repository. It reads
the ticket, works out whether it is looking at an iOS or an Android project, plans the
change, asks you to approve the plan, implements it, validates it with the project's own
build and lint tooling, summarises what it did, and asks you again before it commits,
pushes or opens a pull request.

The orchestration repository never becomes part of your app repository, and your app
repository never has to know the orchestrator exists.

```
Developer
    │  orc run MOB-123
    ▼
Engineering Orchestrator ──────────────────────────────────────────┐
    │                                                              │
    ├── Jira Fetcher ────► TrackerIssue                            │
    ├── Requirements ────► RequirementsDoc                         │
    ├── Plan ────────────► ImplementationPlan ──► ⛔ developer approval
    ├── Implementation ──► ImplementationResult                    │
    ├── Validation ──────► ValidationReport (build · lint · test)  │
    ├── Review ──────────► ReviewSummary                           │
    └── Git / PR ────────► DeliveryResult ───────► ⛔ developer approval
                                                                   │
    Shared skills ── Platform specialists ── iOS │ Android ────────┘
```

## Why it is built this way

- **Many narrow agents, not one large one.** Each agent has a single responsibility, a
  typed input and a typed output. The agent that plans cannot write files; the agent that
  writes files cannot commit; the agent that commits cannot decide whether the work is
  good.
- **The orchestration is deterministic Python.** State transitions, approval gates, git
  operations and validation are ordinary code with tests. Only the genuinely open-ended
  steps go to a model.
- **Validation is not a model's opinion.** A build compiles or it does not. The review
  agent describes the change; it does not decide whether it passed.
- **Every run is resumable and auditable.** State lives in the target repository under
  `.orchestrator/runs/<run-id>/`, alongside an append-only audit log and every artifact
  the run produced.
- **One core, several front doors.** The Python package is the system. The `orc` CLI,
  the generated Claude Code subagents and slash commands, and the VS Code tasks are all
  thin clients that call it, so they cannot drift apart.

## Install

```bash
git clone https://github.com/sulabh191/mobile-eng-orchestrator ~/.mobile-eng-orchestrator
~/.mobile-eng-orchestrator/scripts/install.sh
```

The installer puts the `orc` command on your PATH (via pipx when available), creates the
global config, and registers agents, skills and slash commands with Claude Code.

Then configure credentials — they go to your OS keychain, never to a config file:

```bash
orc config set-secret ORC_JIRA_API_TOKEN
orc config init                 # Jira URL, git host, default branch, engine
orc doctor                      # verify everything is wired up
```

## Use

From inside any iOS or Android checkout:

```bash
orc run MOB-123                 # the full workflow, with gates
orc run MOB-123 --stop-after plan   # just look at the plan
orc run MOB-123 --dry-run       # everything except commit/push/PR
orc run MOB-123 --offline --engine mock   # rehearse with fixtures, no network
```

The run pauses at each gate. In another terminal, or later that day:

```bash
orc status                      # where is this run, what did it produce
orc show plan.md                # read the plan
orc approve                     # approve the pending gate and continue
orc reject --request-changes -m "use the existing RefreshCoordinator"
orc resume                      # pick up after a crash or an interruption
orc audit                       # the append-only event log
```

Other useful commands:

| Command | What it does |
| --- | --- |
| `orc inspect` | Detect the platform and capabilities of the current repository |
| `orc agents` | List the agents, their contracts and which may write |
| `orc skills` | List the skills that will be injected into prompts |
| `orc validate --list` | Show the check plan without running it |
| `orc validate` | Re-run the platform's checks |
| `orc engines` | Show which engine backends are usable here |
| `orc install --all` | Re-register Claude Code and VS Code assets |

## The workflow

| State | Meaning |
| --- | --- |
| `INITIALIZED` | Run created |
| `JIRA_FETCHED` | Ticket fetched and normalised |
| `REQUIREMENTS_REVIEW` | Requirements derived (gate, only if questions are open) |
| `PLAN_GENERATED` | Plan ready for review |
| `PLAN_APPROVED` | **Developer approved the plan** |
| `IMPLEMENTING` | Editing the repository |
| `IMPLEMENTATION_COMPLETE` | Edits made and reconciled against `git status` |
| `VALIDATING` | Build, lint, test and guard checks running |
| `VALIDATION_FAILED` | Checks failed; remediation loop (bounded) |
| `VALIDATION_PASSED` | All required checks passed |
| `REVIEW_READY` | Summary, coverage map, commit and PR text drafted |
| `READY_FOR_PR` | **Waiting for delivery approval** |
| `PR_CREATED` | Branch pushed, pull request opened |
| `COMPLETED` | Done |
| `REJECTED` / `FAILED` | Stopped; `FAILED` is resumable |

Illegal transitions raise rather than proceed, which is what makes "the plan gate cannot
be skipped" a property of the code and not a convention.

## Platform support

Detection is evidence-weighted rather than first-match, so a hybrid repository reports low
confidence instead of guessing. Pin it with `--platform ios|android` when you know better.

| | iOS | Android |
| --- | --- | --- |
| Detected from | `*.xcodeproj`, `*.xcworkspace`, `Package.swift`, `Podfile`, `Project.swift` | `settings.gradle[.kts]`, `build.gradle[.kts]`, `gradlew`, `AndroidManifest.xml` |
| Also profiled | schemes, SPM/CocoaPods/Carthage/Tuist, SwiftLint, deployment target | modules, Kotlin DSL, Compose, ktlint/detekt/Spotless, application id, AGP |
| Validation | SwiftFormat, SwiftLint, `swift build`, `xcodebuild build`, `xcodebuild test` | Spotless, ktlint, detekt, `assembleDebug`, `testDebugUnitTest`, `lintDebug` |

Missing tooling is reported as *skipped*, never as passed. Anything else — a backend
service, a web app — runs as `generic`: guard checks plus whatever you configure.

## Safety model

- **Two gates that cannot be bypassed in code**: the plan, and anything irreversible
  (commit, push, PR). `--yes` exists but refuses to work unless you also export
  `ORC_I_UNDERSTAND_AUTO_APPROVE=1`.
- **Two agents may write.** The implementer edits files; the delivery agent runs git.
  Everything else runs read-only — enforced by the engine's tool allowlist, not by asking
  nicely.
- **Guard checks before build checks**: protected paths (signing assets, keystores,
  `.env`, `google-services.json`), a blast-radius cap, and leftover conflict markers.
- **No force-push, no history rewriting, no direct commits to a protected branch.**
- **Self-reports are verified.** What the engine claims it changed is reconciled against
  `git status`; discrepancies are recorded in the run.
- **Secrets** resolve from environment → OS keychain → a `0600` dotenv, are redacted in
  every log line, and never appear in prompts or the audit log.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Layers, data flow, why the boundaries sit where they do |
| [docs/workflow.md](docs/workflow.md) | States, transitions, gates, resume and remediation |
| [docs/agents.md](docs/agents.md) | Each agent's contract and prompt inputs |
| [docs/configuration.md](docs/configuration.md) | Every setting, layering rules, per-repo overrides |
| [docs/extending.md](docs/extending.md) | Add a skill, a check, an agent, a platform or a tracker |
| [docs/security.md](docs/security.md) | Credential handling, permissions, threat model |
| [docs/troubleshooting.md](docs/troubleshooting.md) | What to do when a run stops |

## Development

```bash
make dev        # install with dev extras
make test       # pytest — fully offline, no API key required
make lint       # ruff
make typecheck  # mypy
```

The test suite runs the whole pipeline end to end against synthetic iOS and Android
repositories using the offline engine and a fixture-backed tracker, including the approval
gates, the remediation loop, resume-after-failure, and a real commit and push to a local
bare remote.

## Licence

MIT — see [LICENSE](LICENSE).
