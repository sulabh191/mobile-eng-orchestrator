# Getting started

From nothing installed to a pull request opened by the orchestrator. Budget about fifteen
minutes for the first run, most of it spent reading the plan it produces.

---

## 1. Prerequisites

### Always required

| | Why | Check |
| --- | --- | --- |
| **Python 3.11+** | The orchestrator core | `python3 --version` |
| **git 2.30+** | Branching, committing, diffing | `git --version` |
| **A git checkout** of the app you want to work on | The orchestrator refuses to run outside a repository | `git -C <path> status` |
| **Jira Cloud access + an API token** | Reading the ticket | [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| **A reasoning engine** — Claude Code *or* the Claude Agent SDK | The four generative phases | `claude --version` |

You can defer the last two: `--offline --engine mock` walks the entire workflow with
fixture tickets and no model, which is the recommended way to see how it behaves before
pointing it at real work.

### For iOS repositories

| | Needed for | Without it |
| --- | --- | --- |
| macOS + Xcode | `xcodebuild` build and test checks | Those checks are **skipped**, with a reason, and the run continues |
| `swiftlint` | Lint check, if the repo has a `.swiftlint.yml` | Skipped |
| `swiftformat` | Format check, if the repo has a `.swiftformat` | Skipped |
| CocoaPods + a populated `Pods/` | Building a workspace-based project | Pre-flight warns you |

### For Android repositories

| | Needed for | Without it |
| --- | --- | --- |
| JDK 17+ | Anything Gradle | Gradle checks fail |
| The repo's `./gradlew` | Every Android check | Pre-flight **blocks** the run |
| Android SDK (`ANDROID_HOME` or `local.properties`) | `assembleDebug`, unit tests | Pre-flight warns; Gradle usually fails |

### Optional but recommended

| | Gives you |
| --- | --- |
| `pipx` | A clean, isolated global install |
| `gh` (authenticated) | Pull requests opened with your existing GitHub auth, no token needed |
| A keyring backend (macOS Keychain, GNOME libsecret) | Secrets in the OS keychain instead of a dotenv |

> Skipped is not passed. A check whose tooling is missing is reported as `skip` with the
> reason, and the review summary says so. It never counts as a green build.

---

## 2. Install

```bash
git clone https://github.com/sulabh191/mobile-eng-orchestrator ~/.mobile-eng-orchestrator
~/.mobile-eng-orchestrator/scripts/install.sh
```

The script checks your Python version, installs the `orc` command (pipx when available,
otherwise `pip install --user`), creates the global config, and registers agents, skills
and slash commands with Claude Code.

<details>
<summary>Manual install, or working on the orchestrator itself</summary>

```bash
# global, isolated
pipx install ~/.mobile-eng-orchestrator

# or into the current environment
python3 -m pip install ~/.mobile-eng-orchestrator

# or editable, with test and lint tooling, for hacking on it
cd ~/.mobile-eng-orchestrator
python3 -m pip install -e ".[dev]"
make test
```

If `orc: command not found`, your user bin directory is not on `PATH`. Add
`~/.local/bin` (Linux, pip `--user`) or the directory `pipx ensurepath` names.
</details>

Confirm:

```bash
orc version
orc --help
```

---

## 3. Configure

```bash
orc config init            # interactive: Jira URL, email, git host, base branch, engine
orc config set-secret ORC_JIRA_API_TOKEN
orc config set-secret ORC_GITHUB_TOKEN     # only if you do not use the gh CLI
```

`config init` writes `~/.config/mobile-eng-orchestrator/config.yaml`. Secrets never go
there — they go to your OS keychain, falling back to a `0600` dotenv. `orc config secrets`
shows where each one resolved from, redacted.

Everything the file can contain is documented in
[configuration.md](configuration.md). The three settings worth checking on day one:

```yaml
jira:
  base_url: https://your-domain.atlassian.net
  email: you@example.com
  default_project: MOB       # lets you type `orc run 123` instead of `orc run MOB-123`

git:
  provider: github           # github | gitlab | none
  default_base_branch: main

engine:
  backend: claude_code       # claude_code | agent_sdk | mock
```

---

## 4. Verify before you trust it

```bash
orc doctor          # tooling, credentials, engine, and what it sees in this repo
orc doctor --jira   # additionally proves your Jira credentials work
orc engines         # which engine backends are usable on this machine
```

Then, from inside the app repository you intend to use:

```bash
cd ~/code/your-ios-app
orc inspect             # platform, confidence, schemes/modules, git state
orc inspect --signals   # the raw evidence behind the platform verdict
orc validate --list     # exactly which checks would run, in order
```

If `orc inspect` names the wrong platform, see
[troubleshooting.md](troubleshooting.md#wrong-platform-detected) — usually it is a hybrid
repo and you want `--platform ios` or a narrower `--repo`.

---

## 5. Rehearse offline

Do this once. It exercises every phase, gate and state transition without a model, a
network call or a single real edit to your code:

```bash
cd ~/code/your-ios-app
orc run MOB-101 --offline --engine mock --dry-run
```

`--offline` uses built-in fixture tickets (`MOB-101`, a story; `MOB-102`, a bug),
`--engine mock` produces deterministic output, and `--dry-run` means nothing is committed
or pushed. You will see the plan gate, the delivery gate, and the artifacts the run wrote.

The mock engine does create one real file, `ORCHESTRATOR_MOCK_CHANGES.md`, so that
validation and git have something genuine to work on. Delete it afterwards.

---

## 6. Your first real run

```bash
cd ~/code/your-ios-app
git checkout main && git pull        # start from a clean, current tree
orc run MOB-123
```

What happens, in order:

**Pre-flight.** Platform detection and cheap sanity checks. Blockers stop the run
immediately; warnings are printed and recorded.

**Fetch → requirements.** The ticket is read and normalised, then turned into numbered
requirements (`R1`, `R2`, …) with non-goals and assumptions. If the agent found a genuine
ambiguity it stops at the requirements gate and shows you the questions. If not, it
carries on without interrupting you.

**Plan → the first gate.** The plan names real files, orders steps by dependency, maps each
step to requirement ids, and rates risk. **Nothing has been written to your repository at
this point.** You get three choices:

```
[a]pprove / [r]eject / request [c]hanges
```

Requesting changes re-plans with your comment in the prompt. Rejecting ends the run.

**Implement → validate.** The implementer edits files — the only agent that can. Then the
validator runs guard checks, then the project's own format, lint, build and test commands.
If required checks fail, the run loops back to implementation with the failure output
attached, up to twice by default, then stops rather than thrashing.

**Review → the second gate.** You see what changed, a requirement-by-requirement coverage
map, risk notes, and the proposed commit message and PR body — with the branch, base and
remote it is about to use. Approving here is what commits, pushes and opens the PR. It is
the only point where anything irreversible happens.

### If you would rather not sit and wait

The run stops cleanly at each gate and can be picked up later, from any terminal:

```bash
orc run MOB-123 --non-interactive     # exits at the first gate, exit code 75
orc status                            # where it is, what it produced
orc show plan.md                      # read the plan properly
orc approve -m "matches the design"   # records the decision and continues
```

Useful variants:

```bash
orc run MOB-123 --stop-after plan   # produce the plan and stop, before the gate
orc run MOB-123 --dry-run           # everything except commit/push/PR
orc run MOB-123 --platform android  # override detection
orc run MOB-123 --base develop --draft
```

---

## 7. After a run

Everything the run produced is on disk, in the repository you ran it in:

```
your-ios-app/.orchestrator/
├── CURRENT_RUN
└── runs/<run-id>/
    ├── state.json                     # status, approvals, history, attempts
    ├── audit.jsonl                    # append-only event log
    └── artifacts/
        ├── issue.md  requirements.md  plan.md  review.md
        ├── validation-attempt-1.md
        └── *.json                     # the typed output of every agent
```

```bash
orc status --all         # every run in this repository
orc show review.md       # the summary you (or a reviewer) should read
orc audit                # every event, with actor and timestamp
```

`.orchestrator/` is added to `.git/info/exclude` automatically, so it never lands in a
commit and your project's `.gitignore` is left alone. Delete old runs whenever you like.

---

## 8. Roll it out to your team

Two files, committed to the **app** repository, are usually all it takes:

```
your-ios-app/.orchestrator/
├── config.yaml          # schemes, gradle tasks, extra checks, base branch
└── skills/
    └── our-conventions.md
```

`config.yaml` is merged over each developer's global config, so the team shares build
settings without sharing credentials — see [configuration.md](configuration.md) and
`examples/repo-config.yaml`. A skill file is plain markdown with frontmatter and is
injected into the planning and implementation prompts, so your architecture rules are
applied to every run by everyone — see `examples/skill-example.md` and
[extending.md](extending.md).

Add VS Code tasks for the people who prefer the command palette:

```bash
orc install --vscode --repo ~/code/your-ios-app   # writes .vscode/tasks.json
```

---

## 9. Running it in CI

```bash
export ORC_ENGINE=agent_sdk
export ORC_JIRA_API_TOKEN=...            # from your CI secret store
export ORC_GITHUB_TOKEN=...
export ORC_I_UNDERSTAND_AUTO_APPROVE=1   # required acknowledgement

orc run "$ISSUE_KEY" --yes --non-interactive --base develop --draft
```

`--yes` skips every human gate including push, which is why it refuses to run without the
acknowledgement variable. Use it against draft PRs on a scratch branch, never against a
protected one. Exit codes are meaningful — `75` means "stopped at a gate", `65` means
"validation failed" — see [cli-reference.md](cli-reference.md#exit-codes).

---

## 10. Uninstall

```bash
orc install uninstall              # removes only the generated Claude Code files
pipx uninstall mobile-eng-orchestrator
rm -rf ~/.config/mobile-eng-orchestrator     # config, skills and the dotenv
```

Run state inside your app repositories is just files: `rm -rf .orchestrator`.

---

## Where to go next

- [workflow.md](workflow.md) — the states, the gates, resume and remediation in detail
- [cli-reference.md](cli-reference.md) — every command and flag
- [agents.md](agents.md) — what each agent is responsible for
- [troubleshooting.md](troubleshooting.md) — when a run stops and you are not sure why
