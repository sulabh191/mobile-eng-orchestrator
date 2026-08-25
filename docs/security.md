# Security model

## What this tool does with your machine

It reads a ticket, edits files inside one repository, runs that repository's build and
lint commands, and — after you approve — commits, pushes and opens a pull request. Those
are the only side effects outside its own state directory.

## Credentials

Resolution order, highest first:

1. **Environment** (`ORC_*`) — for CI.
2. **OS keychain** via `keyring` (macOS Keychain, libsecret, Windows Credential Manager) —
   the recommended place for a laptop; `orc config set-secret` writes here.
3. **Dotenv** at `~/.config/mobile-eng-orchestrator/.env`, created with mode `0600` —
   the fallback when no keyring backend exists.

Guarantees:

- Secrets are never written to `config.yaml`, and `orc config show` cannot print one.
- Every displayed value goes through `redact()`: first and last four characters only.
- Credentials are never included in engine prompts or audit events.
- `git remote` URLs are sanitised of embedded credentials before display or logging.
- `JiraClient.__repr__` shows a redacted token.

Rotate a token with `orc config set-secret ORC_JIRA_API_TOKEN`; remove one with
`orc config delete-secret`.

## Permissions given to the model

| Phase | Mode | Tools |
| --- | --- | --- |
| requirements, plan, review | read-only | `Read`, `Grep`, `Glob` (plus `--permission-mode plan`) |
| implement | edit | the above, plus `Edit`, `Write`, `MultiEdit` |

The allowlist is passed to the backend on every call. A read-only agent is structurally
incapable of writing to the repository — it is not a matter of the prompt asking it not to.

## Guard rails before any build runs

- **Protected paths.** Signing assets, keystores, provisioning profiles, `.env`,
  `google-services.json`, `GoogleService-Info.plist`, `.git/` and `.orchestrator/` — a
  change to any of them fails validation. Configurable per repository, additive.
- **Blast radius.** More than `behaviour.max_touched_files` (default 60) changed files
  fails the run.
- **Conflict markers.** Left-behind `<<<<<<<` / `>>>>>>>` fails the run.
- **Working tree sanity.** A repository mid-merge or mid-rebase is refused.
- **Oversized files.** Anything above 5 MB in the change set is flagged.

## Git safety

- Never commits to a branch in `git.protected_branches`.
- Never force-pushes, rebases, resets or deletes branches — those operations are not
  implemented at all.
- Pushes only the branch it created, only to the configured remote, only after the delivery
  gate.
- If the push succeeds and the PR call fails, the branch stays pushed and the failure is
  recorded; work is never unwound behind your back.
- Every subprocess is invoked as an argument list — never a shell string — so nothing from
  a ticket or a model response can be interpolated into a shell.

## Auto-approval

`--yes` / `behaviour.auto_approve` bypasses every human gate including commit and push. It
refuses to run unless `ORC_I_UNDERSTAND_AUTO_APPROVE=1` is exported, so it cannot be
enabled by an inherited config file alone. Use it in CI against a scratch branch; do not
put it in your shell profile.

## Auditability

Each run writes `audit.jsonl` — an append-only log of every fetch, agent completion,
approval decision, git operation and failure, with actor and timestamp — plus every
artifact the run produced. `orc audit` prints it. The state directory is added to
`.git/info/exclude`, so it stays out of your commits without modifying a tracked
`.gitignore`.

## What it deliberately does not do

- No telemetry, no network calls other than to your tracker, your git host and your chosen
  model backend.
- No writes outside the target repository and its own config/state directories.
- No modification of your global git config, shell profile or CI configuration.
- No deletion of branches or files it did not create.
