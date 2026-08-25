# Configuration

## Layers

Highest precedence wins:

```
command-line flags  >  ORC_* environment  >  <repo>/.orchestrator/config.yaml  >  ~/.config/mobile-eng-orchestrator/config.yaml  >  defaults
```

Merging is deep, so a repository can override one key without restating a section.
`orc config show` prints the resolved result and the layers that produced it.

Secrets are never part of this file. See [security.md](security.md).

## Locations

| What | Where |
| --- | --- |
| Global config | `~/.config/mobile-eng-orchestrator/config.yaml` (`orc config path`) |
| Secrets | OS keychain, or `~/.config/mobile-eng-orchestrator/.env` (mode 0600) |
| User skills | `~/.config/mobile-eng-orchestrator/skills/*.md` |
| Per-repo config | `<repo>/.orchestrator/config.yaml` |
| Per-repo skills | `<repo>/.orchestrator/skills/*.md` |
| Run state | `<repo>/.orchestrator/runs/<run-id>/` |

`ORC_CONFIG_DIR` relocates the global directory (used by the test suite).

## Reference

```yaml
jira:
  base_url: https://your-domain.atlassian.net
  email: you@example.com
  default_project: MOB              # lets you type `orc run 123`
  acceptance_criteria_field: customfield_10101   # auto-discovered if omitted
  timeout_seconds: 30
  max_comments: 20
  verify_ssl: true

git:
  provider: github                  # github | gitlab | none
  default_base_branch: main
  branch_template: "{prefix}/{issue_key_lower}-{slug}"
  branch_prefix_by_type:
    bug: fix
    story: feature
    task: chore
  commit_template: "{type}({scope}): {summary}\n\n{body}\n\nRefs: {issue_key}"
  sign_commits: false
  push_remote: origin
  protected_branches: [main, master, develop]

engine:
  backend: claude_code              # claude_code | agent_sdk | mock
  model: null                       # backend default when unset
  timeout_seconds: 900
  claude_binary: claude
  extra_args: []
  record_transcripts: true

validation:
  fail_fast: false
  per_check_timeout_seconds: 1800
  max_remediation_attempts: 2
  skip: []                          # check names to skip entirely
  extra_checks:
    - name: custom:api-contract
      command: ["./scripts/check-api-contract.sh"]
      required: true
      timeout: 600
  ios:
    scheme: MyApp                   # default: first non-test shared scheme
    test_scheme: MyAppTests
    configuration: Debug
    destination: "generic/platform=iOS Simulator"
    test_destination: "platform=iOS Simulator,name=iPhone 15"
    swiftlint_strict: true
  android:
    assemble_task: assembleDebug
    test_task: testDebugUnitTest
    lint_task: lintDebug
    run_android_lint: true

behaviour:
  auto_approve: false               # also needs ORC_I_UNDERSTAND_AUTO_APPROVE=1
  interactive: true
  log_level: INFO
  max_touched_files: 60
  protected_paths:
    - ".git/"
    - ".orchestrator/"
    - "**/*.keystore"
    - "**/*.jks"
    - "**/*.mobileprovision"
    - "**/google-services.json"
    - "**/GoogleService-Info.plist"
    - "**/.env"
```

## Environment variables

| Variable | Maps to |
| --- | --- |
| `ORC_JIRA_BASE_URL`, `ORC_JIRA_EMAIL`, `ORC_JIRA_DEFAULT_PROJECT`, `ORC_JIRA_AC_FIELD` | `jira.*` |
| `ORC_GIT_PROVIDER`, `ORC_GIT_DEFAULT_BASE_BRANCH`, `ORC_GIT_PUSH_REMOTE` | `git.*` |
| `ORC_ENGINE`, `ORC_ENGINE_MODEL`, `ORC_ENGINE_TIMEOUT`, `ORC_CLAUDE_BINARY` | `engine.*` |
| `ORC_AUTO_APPROVE`, `ORC_INTERACTIVE`, `ORC_LOG_LEVEL` | `behaviour.*` |
| `ORC_JIRA_API_TOKEN`, `ORC_GITHUB_TOKEN`, `ORC_GITLAB_TOKEN` | secrets (never in YAML) |
| `ORC_I_UNDERSTAND_AUTO_APPROVE` | required acknowledgement for auto-approval |
| `ORC_CONFIG_DIR`, `ORC_STATE_DIR` | relocate config/state directories |

## A per-repository example

`<repo>/.orchestrator/config.yaml`, committed so the whole team shares it:

```yaml
git:
  default_base_branch: develop
  protected_branches: [develop, main, "release/*"]

validation:
  ios:
    scheme: Shopping
    test_scheme: ShoppingUnitTests
  extra_checks:
    - name: custom:snapshot-tests
      command: ["bundle", "exec", "fastlane", "snapshot_test"]
      required: false

behaviour:
  max_touched_files: 25
```
