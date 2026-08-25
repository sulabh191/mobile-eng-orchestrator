"""Client surfaces.

Claude Code, VS Code and the terminal are three front doors to the same Python
core. The generators in this package emit thin definition files for each
surface; the definitions call the `orc` CLI, so behaviour can never diverge
between them.
"""

from orchestrator.clients.claude_code import install_claude_code_assets
from orchestrator.clients.vscode import install_vscode_assets

__all__ = ["install_claude_code_assets", "install_vscode_assets"]
