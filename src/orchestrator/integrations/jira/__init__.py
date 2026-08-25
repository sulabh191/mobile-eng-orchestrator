"""Jira integration.

Everything Jira-specific — authentication, REST paths, ADF payloads, custom
field ids — is confined to this package. The rest of the orchestrator only ever
sees :class:`orchestrator.core.models.TrackerIssue` and the
:class:`JiraClientProtocol` interface, so swapping in another tracker means
adding one module here and nothing else.
"""

from orchestrator.integrations.jira.factory import build_jira_client
from orchestrator.integrations.jira.mock import MockJiraClient
from orchestrator.integrations.jira.protocol import JiraClientProtocol

__all__ = ["JiraClientProtocol", "MockJiraClient", "build_jira_client"]
