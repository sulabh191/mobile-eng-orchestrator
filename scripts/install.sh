#!/usr/bin/env bash
#
# One-shot installer for the Engineering Orchestrator.
#
#   git clone https://github.com/your-org/engineering-orchestrator ~/.engineering-orchestrator
#   ~/.engineering-orchestrator/scripts/install.sh
#
# Installs the `orc` CLI globally (pipx when available, otherwise pip --user),
# then registers agents, skills and slash commands with Claude Code.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

info()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null 2>&1 || die "python3 is required."

PY_VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$PY_VERSION" in
  3.1[1-9]|3.[2-9][0-9]|[4-9].*) ;;
  *) die "Python 3.11+ is required (found $PY_VERSION)." ;;
esac

command -v git >/dev/null 2>&1 || die "git is required."

info "Installing the orchestrator from $REPO_ROOT"
if command -v pipx >/dev/null 2>&1; then
  pipx install --force "$REPO_ROOT"
else
  warn "pipx not found; falling back to 'pip install --user'."
  "$PYTHON" -m pip install --user --upgrade "$REPO_ROOT"
fi

command -v orc >/dev/null 2>&1 || warn \
  "The 'orc' command is not on your PATH yet. Add your user bin directory (often ~/.local/bin) to PATH."

info "Creating the global configuration"
orc config init --defaults || warn "Config already exists; leaving it alone."

info "Registering agents, skills and commands with Claude Code"
orc install --claude-code

cat <<'NEXT'

Installed. Next steps:

  1. Store your Jira token (goes to the OS keychain, never a config file):
       orc config set-secret ORC_JIRA_API_TOKEN

  2. Point it at your Jira instance:
       $EDITOR "$(orc config path)/config.yaml"

  3. Check the environment:
       orc doctor

  4. From any iOS or Android checkout:
       orc run MOB-123

NEXT
