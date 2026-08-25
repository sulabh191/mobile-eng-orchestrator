# CLI reference

`orc` is one client of the orchestrator core; Claude Code and VS Code call the same
commands. Every command accepts `--help`.

Two conventions apply throughout:

- **`--repo` / `-C`** defaults to the current directory and then walks up to the nearest
  git root, so you can run `orc` from anywhere inside a checkout.
- **`<run-id>`** is optional wherever it appears. Omitted, it means the most recent run in
  that repository (tracked in `.orchestrator/CURRENT_RUN`).

---

## Workflow commands

### `orc run <ISSUE-KEY>`

Run the workflow for one ticket. If a non-terminal run already exists for that issue, it
resumes it instead of starting a second one.

| Flag | Default | Effect |
| --- | --- | --- |
| `--repo`, `-C PATH` | cwd | Target repository |
| `--platform ios\|android\|generic` | detected | Override platform detection |
| `--engine claude_code\|agent_sdk\|mock` | config | Reasoning backend for this run |
| `--offline` | off | Use built-in fixture tickets instead of Jira |
| `--dry-run` | off | Run everything; never commit, push or open a PR |
| `--yes`, `-y` | off | Auto-approve every gate — requires `ORC_I_UNDERSTAND_AUTO_APPROVE=1` |
| `--non-interactive` | off | Stop at gates instead of prompting (exit 75) |
| `--stop-after PHASE` | — | `fetch`, `requirements`, `plan`, `implement`, `validate`, `review`, `deliver` |
| `--base BRANCH` | detected, then config | Base branch for the PR |
| `--branch NAME` | generated | Override the generated branch name |
| `--draft` | off | Open the pull request as a draft |
| `--fixtures DIR` | built-ins | Directory of offline issue fixtures (JSON) |
| `--log-level LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

```bash
orc run MOB-123
orc run MOB-123 --stop-after plan            # plan, then stop before the gate
orc run MOB-123 --dry-run --log-level DEBUG
orc run 123                                  # needs jira.default_project
orc run MOB-123 --offline --engine mock      # no network, no model
```

### `orc resume [<run-id>]`

Continue a run that stopped at a gate, failed, or was interrupted. Artifacts from earlier
phases are reloaded, so nothing is redone. A run that failed during validation re-enters at
`VALIDATING`, so hand-fixes get a fresh check run.

`--repo/-C`, `--engine`, `--non-interactive`, `--log-level`.

### `orc status [<run-id>]`

Run detail: status, engine, branch, approvals, artifacts and transition history.

| Flag | Effect |
| --- | --- |
| `--all`, `-a` | List every run in this repository instead |
| `--json` | Machine-readable output |

### `orc approve [<run-id>]`

Record approval for the gate the run is waiting on, then continue it.

| Flag | Default | Effect |
| --- | --- | --- |
| `--gate NAME` | the pending gate | `requirements`, `plan` or `delivery` |
| `--comment`, `-m TEXT` | — | Stored in the state and the audit log |
| `--no-resume` | resumes | Record the decision without continuing |

### `orc reject [<run-id>]`

| Flag | Effect |
| --- | --- |
| `--request-changes` | Redo the phase with your comment instead of ending the run |
| `--comment`, `-m TEXT` | What was wrong — goes into the re-run prompt |
| `--gate NAME` | Target a specific gate |

```bash
orc reject --request-changes -m "reuse RefreshCoordinator instead of a new one"
orc reject -m "wrong ticket"          # ends the run
```

### `orc show <artifact>`

Print an artifact from a run: `issue.md`, `requirements.md`, `plan.md`, `review.md`,
`validation-attempt-1.md`, or any of the `*.json` agent outputs. `--run <run-id>` selects a
specific run. An unknown name lists what is available.

### `orc audit [<run-id>]`

Print the append-only event log: every fetch, agent completion, approval decision, git
operation and failure, with actor and timestamp.

---

## Inspection and diagnostics

| Command | What it does |
| --- | --- |
| `orc doctor` | Tooling, credentials, engine availability, repository summary, run count. `--jira` also probes Jira auth |
| `orc inspect` | Platform, confidence, schemes/modules, linters, git state. `--signals` shows the raw detection evidence; `--json` for scripts |
| `orc validate` | Re-run the platform's checks for a run. `--list` prints the check plan without running it |
| `orc agents` | Every agent, its phase, its input/output contract, whether it uses a model and whether it may write. `--json` supported |
| `orc skills` | Skills visible here, with source and tags. `--platform` filters; `--show NAME` prints one in full |
| `orc engines` | Each backend and whether it is usable on this machine |
| `orc version` | Version string |

---

## Configuration

| Command | What it does |
| --- | --- |
| `orc config init` | Create the global config. `--force` overwrites; `--defaults` skips the prompts |
| `orc config show` | The merged, effective config plus the layers that produced it. `--file` shows the raw global file |
| `orc config path` | Print the global config directory |
| `orc config set-secret KEY` | Store a secret in the OS keychain (dotenv fallback). Prompts unless `--value` is given |
| `orc config get-secret KEY` | Show where a secret resolves from, redacted |
| `orc config delete-secret KEY` | Remove it from keychain and dotenv |
| `orc config secrets` | List all known secrets and their sources |

Known secret keys: `ORC_JIRA_API_TOKEN`, `ORC_GITHUB_TOKEN`, `ORC_GITLAB_TOKEN`.

---

## Installing client assets

| Command | What it does |
| --- | --- |
| `orc install` | Claude Code agents, slash commands and skills (the default) |
| `orc install --vscode --repo PATH` | Merge orchestrator tasks into that repo's `.vscode/tasks.json`, preserving your own |
| `orc install --all` | Both |
| `orc install --dry-run` | List what would be written |
| `orc install --claude-dir PATH` | Target a non-default Claude Code directory |
| `orc install uninstall` | Remove only the generated `orc-*` files |

Generated slash commands: `/orc-run`, `/orc-plan`, `/orc-status`, `/orc-approve`,
`/orc-validate`, `/orc-inspect`. Generated subagents are named `orc-<agent>`.

---

## Exit codes

| Code | Meaning | Typical cause |
| --- | --- | --- |
| `0` | Success — including a cleanly rejected gate | |
| `1` | Generic orchestrator error | Illegal state transition, corrupt state file |
| `65` | Validation failed | Required checks still failing after remediation |
| `66` | Repository problem | Not a git repo, pre-flight blocker, protected branch |
| `69` | Issue tracker unavailable | Jira unreachable, issue not found |
| `70` | Engine failure | `claude` missing, timeout, unusable output |
| `75` | **Approval required** | A gate was reached in non-interactive mode |
| `78` | Configuration or credential problem | Missing token, invalid YAML, unacknowledged `--yes` |

`75` is the one worth special-casing in a script: it means the run is healthy and waiting
for a human, not that anything went wrong.

```bash
orc run "$KEY" --non-interactive
case $? in
  0)  echo "done" ;;
  75) echo "waiting for approval: $(orc status --json | jq -r .status)" ;;
  65) echo "validation failed"; orc show validation-attempt-1.md; exit 1 ;;
  *)  exit 1 ;;
esac
```

---

## Environment variables

Everything below overrides the config files. See
[configuration.md](configuration.md#environment-variables) for the full mapping.

| Variable | Purpose |
| --- | --- |
| `ORC_JIRA_BASE_URL`, `ORC_JIRA_EMAIL`, `ORC_JIRA_DEFAULT_PROJECT`, `ORC_JIRA_AC_FIELD` | Jira connection |
| `ORC_JIRA_API_TOKEN`, `ORC_GITHUB_TOKEN`, `ORC_GITLAB_TOKEN` | Secrets (never read from YAML) |
| `ORC_GIT_PROVIDER`, `ORC_GIT_DEFAULT_BASE_BRANCH`, `ORC_GIT_PUSH_REMOTE` | Git behaviour |
| `ORC_ENGINE`, `ORC_ENGINE_MODEL`, `ORC_ENGINE_TIMEOUT`, `ORC_CLAUDE_BINARY` | Engine selection |
| `ORC_AUTO_APPROVE`, `ORC_INTERACTIVE`, `ORC_LOG_LEVEL` | Behaviour |
| `ORC_I_UNDERSTAND_AUTO_APPROVE` | Required before any auto-approval works |
| `ORC_CONFIG_DIR`, `ORC_STATE_DIR` | Relocate config and state directories |
| `CLAUDE_CONFIG_DIR` | Where `orc install` writes Claude Code assets |

---

## Files a run touches

| Path | Written by | Committed? |
| --- | --- | --- |
| `~/.config/mobile-eng-orchestrator/config.yaml` | `orc config init` | no |
| `~/.config/mobile-eng-orchestrator/.env` | `orc config set-secret` (fallback), mode `0600` | no |
| `~/.claude/{agents,commands,skills}/orc-*` | `orc install` | no |
| `<repo>/.orchestrator/runs/<id>/` | every run | no — auto-excluded |
| `<repo>/.orchestrator/config.yaml` | you | **yes**, share it with the team |
| `<repo>/.orchestrator/skills/*.md` | you | **yes** |
| `<repo>/.vscode/tasks.json` | `orc install --vscode` | your call |
