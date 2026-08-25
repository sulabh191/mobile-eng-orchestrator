"""Jira layer: ADF conversion, the mock client, and the real client's mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.core.config import JiraSettings
from orchestrator.core.credentials import CredentialStore
from orchestrator.core.errors import CredentialError, IssueNotFoundError
from orchestrator.integrations.jira.adf import adf_to_text, extract_bullets, text_to_adf
from orchestrator.integrations.jira.client import JiraClient
from orchestrator.integrations.jira.mock import MockJiraClient
from orchestrator.integrations.jira.protocol import JiraClientProtocol


# -- ADF --------------------------------------------------------------------- #


def test_adf_paragraph_and_marks():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Refresh "},
                    {"type": "text", "text": "must", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": " work."},
                ],
            }
        ],
    }
    assert adf_to_text(doc).strip() == "Refresh **must** work."


def test_adf_bullet_list_and_code_block():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "one"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "two"}]}
                        ],
                    },
                ],
            },
            {
                "type": "codeBlock",
                "attrs": {"language": "swift"},
                "content": [{"type": "text", "text": "let x = 1"}],
            },
        ],
    }
    text = adf_to_text(doc)
    assert "- one" in text and "- two" in text
    assert "```swift" in text and "let x = 1" in text


def test_text_to_adf_round_trips():
    adf = text_to_adf("first para\n\nsecond para")
    assert adf["type"] == "doc"
    assert adf_to_text(adf).strip().splitlines()[0] == "first para"


def test_extract_bullets_handles_checkboxes_and_numbers():
    text = "- [ ] first\n* second\n3. third\nnot a bullet"
    assert extract_bullets(text) == ["first", "second", "third"]


# -- Mock client --------------------------------------------------------------- #


def test_mock_client_satisfies_protocol():
    assert isinstance(MockJiraClient(), JiraClientProtocol)


def test_mock_client_returns_fixture_issue():
    issue = MockJiraClient().get_issue("mob-101")
    assert issue.key == "MOB-101"
    assert "pull-to-refresh" in issue.summary.lower()


def test_mock_client_unknown_issue():
    with pytest.raises(IssueNotFoundError):
        MockJiraClient().get_issue("NOPE-1")


def test_mock_client_records_comments_and_transitions():
    client = MockJiraClient()
    client.add_comment("MOB-101", "PR opened")
    client.transition("MOB-101", "In Review")
    assert client.comments == [("MOB-101", "PR opened")]
    assert client.get_issue("MOB-101").status == "In Review"


def test_mock_client_loads_the_repository_fixtures():
    """The fixtures shipped in tests/fixtures/ stay loadable and well formed."""
    from pathlib import Path as _Path

    client = MockJiraClient(fixtures_dir=_Path(__file__).parent / "fixtures")
    issue = client.get_issue("MOB-201")
    assert issue.summary.startswith("Persist the selected store")
    assert "The selected store survives an app restart" in issue.acceptance_criteria


def test_mock_client_loads_fixture_directory(tmp_path):
    payload = {"key": "ABC-1", "summary": "From disk", "description": "body"}
    (tmp_path / "abc-1.json").write_text(json.dumps(payload), encoding="utf-8")
    client = MockJiraClient(fixtures_dir=tmp_path)
    assert client.get_issue("ABC-1").summary == "From disk"


# -- Real client (transport mocked) ---------------------------------------------- #


ISSUE_PAYLOAD = {
    "key": "MOB-201",
    "fields": {
        "summary": "Fix the crash",
        "description": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Acceptance criteria:"}],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "No crash on cold start"}],
                                }
                            ],
                        }
                    ],
                },
            ],
        },
        "issuetype": {"name": "Bug"},
        "status": {"name": "Ready"},
        "priority": {"name": "High"},
        "labels": ["mobile"],
        "components": [{"name": "Auth"}],
        "assignee": {"displayName": "Sam"},
        "comment": {"comments": [{"author": {"displayName": "Priya"}, "body": {"type": "doc", "content": []}}]},
        "attachment": [],
        "subtasks": [],
    },
}


def _client(handler, tmp_path) -> JiraClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://example.atlassian.net")
    settings = JiraSettings(base_url="https://example.atlassian.net", email="me@example.com")
    store = CredentialStore(tmp_path)
    store.set("ORC_JIRA_API_TOKEN", "secret-token", prefer_keyring=False)
    return JiraClient(settings, credentials=store, http_client=http)


def test_real_client_maps_payload_to_tracker_issue(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=ISSUE_PAYLOAD)

    issue = _client(handler, tmp_path).get_issue("MOB-201")
    assert issue.key == "MOB-201"
    assert issue.issue_type == "Bug"
    assert issue.components == ["Auth"]
    assert issue.acceptance_criteria == ["No crash on cold start"]
    assert issue.url.endswith("/browse/MOB-201")


def test_real_client_raises_credential_error_on_401(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "nope"})

    with pytest.raises(CredentialError):
        _client(handler, tmp_path).get_issue("MOB-201")


def test_real_client_raises_not_found_on_404(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    with pytest.raises(IssueNotFoundError):
        _client(handler, tmp_path).get_issue("MOB-999")


def test_bare_number_is_qualified_with_default_project(tmp_path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/field"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=ISSUE_PAYLOAD)

    settings = JiraSettings(
        base_url="https://example.atlassian.net", email="me@example.com", default_project="mob"
    )
    store = CredentialStore(tmp_path)
    store.set("ORC_JIRA_API_TOKEN", "t", prefer_keyring=False)
    client = JiraClient(
        settings,
        credentials=store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.atlassian.net"),
    )
    client.get_issue("42")
    assert any("MOB-42" in path for path in seen)


def test_credentials_are_never_repr_ed_in_full(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ISSUE_PAYLOAD)

    client = _client(handler, tmp_path)
    assert "secret-token" not in repr(client)
