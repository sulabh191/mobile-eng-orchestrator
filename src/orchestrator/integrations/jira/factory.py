"""Chooses the tracker implementation for a run."""

from __future__ import annotations

from pathlib import Path

from orchestrator.core.config import Settings
from orchestrator.core.logging import get_logger
from orchestrator.integrations.jira.mock import MockJiraClient
from orchestrator.integrations.jira.protocol import JiraClientProtocol

logger = get_logger("jira.factory")


def build_jira_client(
    settings: Settings,
    *,
    offline: bool = False,
    fixtures_dir: Path | None = None,
) -> JiraClientProtocol:
    """Return a real Jira client, or the mock when offline / unconfigured.

    ``offline`` is set by ``--offline`` and by the mock engine, so the entire
    workflow can be exercised without network access or credentials.
    """
    if offline:
        logger.debug("using MockJiraClient (offline requested)")
        return MockJiraClient(fixtures_dir=fixtures_dir)

    from orchestrator.integrations.jira.client import JiraClient

    return JiraClient(settings.jira, credentials=settings.credentials)
