"""Jira Cloud REST v3 client.

Authentication, retries, pagination and payload mapping all live here. The
client is synchronous on purpose: the orchestrator is a CLI, and a predictable
call stack is worth more than concurrency at this scale.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from orchestrator.core.config import JiraSettings
from orchestrator.core.credentials import CredentialStore, redact
from orchestrator.core.errors import (
    CredentialError,
    IssueNotFoundError,
    IssueTrackerError,
)
from orchestrator.core.logging import get_logger
from orchestrator.core.models import IssueAttachment, IssueComment, TrackerIssue
from orchestrator.integrations.jira.adf import adf_to_text, extract_bullets, text_to_adf

logger = get_logger("jira")

API_ROOT = "/rest/api/3"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3

#: Field names commonly used for acceptance criteria when no custom field id is
#: configured. Checked case-insensitively against the field *names* endpoint.
AC_FIELD_NAME_HINTS = ("acceptance criteria", "acceptance_criteria", "ac")

#: Headings inside a description that usually introduce acceptance criteria.
AC_HEADING_HINTS = (
    "acceptance criteria",
    "acceptance criterion",
    "definition of done",
    "ac:",
)


class JiraClient:
    """A real Jira Cloud client speaking REST v3."""

    name = "jira-cloud"

    def __init__(
        self,
        settings: JiraSettings,
        *,
        credentials: CredentialStore,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not settings.base_url:
            raise CredentialError(
                "Jira base URL is not configured.",
                hint="Set ORC_JIRA_BASE_URL or run `orc config init`.",
            )
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self._email = settings.email or credentials.require(
            "JIRA_EMAIL", purpose="Jira authentication"
        )
        self._token = credentials.require("JIRA_API_TOKEN", purpose="Jira authentication")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self.base_url,
            auth=(self._email, self._token),
            timeout=settings.timeout_seconds,
            verify=settings.verify_ssl,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "mobile-eng-orchestrator/0.1",
            },
        )
        self._field_cache: dict[str, str] | None = None

    # -- lifecycle ----------------------------------------------------------- #

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"JiraClient(base_url={self.base_url!r}, token={redact(self._token)})"

    # -- transport ------------------------------------------------------------ #

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{API_ROOT}{path}"
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.debug("jira transport error (attempt %s): %s", attempt, exc)
                if attempt == MAX_RETRIES:
                    raise IssueTrackerError(
                        f"Could not reach Jira at {self.base_url}: {exc}",
                        hint="Check the base URL, your VPN and network access.",
                    ) from exc
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                delay = float(response.headers.get("Retry-After", min(2**attempt, 8)))
                logger.debug("jira %s -> %s, retrying in %ss", url, response.status_code, delay)
                time.sleep(delay)
                continue
            return self._check(response)

        raise IssueTrackerError(f"Jira request failed: {last_error}")  # pragma: no cover

    @staticmethod
    def _check(response: httpx.Response) -> httpx.Response:
        if response.status_code in (401, 403):
            raise CredentialError(
                f"Jira rejected the credentials ({response.status_code}).",
                hint=(
                    "Rotate the API token at id.atlassian.com and store it with "
                    "`orc config set-secret ORC_JIRA_API_TOKEN`."
                ),
            )
        if response.status_code == 404:
            raise IssueNotFoundError("Jira returned 404 for that resource.")
        if response.status_code >= 400:
            detail = response.text[:400]
            raise IssueTrackerError(f"Jira error {response.status_code}: {detail}")
        return response

    # -- fields ---------------------------------------------------------------- #

    def _fields(self) -> dict[str, str]:
        """Map lowercase field name -> field id (cached per client)."""
        if self._field_cache is None:
            try:
                payload = self._request("GET", "/field").json()
                self._field_cache = {
                    str(item.get("name", "")).lower(): str(item.get("id"))
                    for item in payload
                    if item.get("id")
                }
            except IssueTrackerError:  # non-fatal: AC extraction just falls back
                self._field_cache = {}
        return self._field_cache

    def _acceptance_criteria_field_id(self) -> str | None:
        if self.settings.acceptance_criteria_field:
            return self.settings.acceptance_criteria_field
        fields = self._fields()
        for hint in AC_FIELD_NAME_HINTS:
            if hint in fields:
                return fields[hint]
        return None

    # -- mapping ---------------------------------------------------------------- #

    def _to_issue(self, payload: dict[str, Any]) -> TrackerIssue:
        fields: dict[str, Any] = payload.get("fields", {}) or {}
        description = adf_to_text(fields.get("description")).strip()

        acceptance = self._extract_acceptance_criteria(fields, description)

        comments_payload = (fields.get("comment") or {}).get("comments", [])
        comments = [
            IssueComment(
                author=(c.get("author") or {}).get("displayName"),
                created=c.get("created"),
                body=adf_to_text(c.get("body")).strip(),
            )
            for c in comments_payload[-self.settings.max_comments :]
        ]

        attachments = [
            IssueAttachment(
                filename=a.get("filename", "attachment"),
                url=a.get("content", ""),
                mime_type=a.get("mimeType"),
                size_bytes=a.get("size"),
            )
            for a in (fields.get("attachment") or [])
        ]

        parent = fields.get("parent") or {}
        return TrackerIssue(
            key=payload.get("key", ""),
            summary=fields.get("summary", ""),
            description=description,
            issue_type=((fields.get("issuetype") or {}).get("name")) or "Task",
            status=((fields.get("status") or {}).get("name")) or "Unknown",
            priority=((fields.get("priority") or {}).get("name")),
            labels=list(fields.get("labels") or []),
            components=[c.get("name", "") for c in (fields.get("components") or [])],
            assignee=((fields.get("assignee") or {}).get("displayName")),
            reporter=((fields.get("reporter") or {}).get("displayName")),
            parent_key=parent.get("key"),
            subtask_keys=[s.get("key", "") for s in (fields.get("subtasks") or [])],
            acceptance_criteria=acceptance,
            attachments=attachments,
            comments=comments,
            url=f"{self.base_url}/browse/{payload.get('key', '')}",
        )

    def _extract_acceptance_criteria(
        self, fields: dict[str, Any], description: str
    ) -> list[str]:
        """Prefer a dedicated field; fall back to parsing the description."""
        field_id = self._acceptance_criteria_field_id()
        if field_id and fields.get(field_id):
            text = adf_to_text(fields[field_id]).strip()
            bullets = extract_bullets(text)
            if bullets:
                return bullets
            if text:
                return [line.strip() for line in text.splitlines() if line.strip()]

        lowered = description.lower()
        for hint in AC_HEADING_HINTS:
            index = lowered.find(hint)
            if index != -1:
                tail = description[index + len(hint) :]
                bullets = extract_bullets(tail)
                if bullets:
                    return bullets
        return []

    # -- protocol surface --------------------------------------------------------- #

    def get_issue(self, key: str) -> TrackerIssue:
        key = self._qualify(key)
        response = self._request(
            "GET",
            f"/issue/{key}",
            params={"expand": "renderedFields", "fields": "*all"},
        )
        return self._to_issue(response.json())

    def search(self, jql: str, *, limit: int = 25) -> list[TrackerIssue]:
        issues: list[TrackerIssue] = []
        start_at = 0
        while len(issues) < limit:
            page = self._request(
                "POST",
                "/search",
                json={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": min(50, limit - len(issues)),
                    "fields": ["*all"],
                },
            ).json()
            batch = page.get("issues", [])
            issues.extend(self._to_issue(item) for item in batch)
            start_at += len(batch)
            if not batch or start_at >= page.get("total", 0):
                break
        return issues[:limit]

    def add_comment(self, key: str, body: str) -> None:
        self._request("POST", f"/issue/{self._qualify(key)}/comment", json={"body": text_to_adf(body)})

    def available_transitions(self, key: str) -> dict[str, str]:
        payload = self._request("GET", f"/issue/{self._qualify(key)}/transitions").json()
        return {t.get("name", ""): str(t.get("id")) for t in payload.get("transitions", [])}

    def transition(self, key: str, transition: str) -> None:
        transitions = self.available_transitions(key)
        transition_id = transitions.get(transition) or (
            transition if transition in transitions.values() else None
        )
        if transition_id is None:
            raise IssueTrackerError(
                f"Transition '{transition}' is not available for {key}.",
                hint=f"Available: {', '.join(transitions) or 'none'}",
            )
        self._request(
            "POST",
            f"/issue/{self._qualify(key)}/transitions",
            json={"transition": {"id": transition_id}},
        )

    def health_check(self) -> dict[str, str]:
        payload = self._request("GET", "/myself").json()
        return {
            "backend": self.name,
            "base_url": self.base_url,
            "account": payload.get("displayName", "unknown"),
            "email": payload.get("emailAddress", self._email),
            "token": redact(self._token),
        }

    # -- helpers ------------------------------------------------------------------ #

    def _qualify(self, key: str) -> str:
        """Allow bare numbers when a default project is configured (``123`` -> ``MOB-123``)."""
        key = key.strip().upper()
        if key.isdigit() and self.settings.default_project:
            return f"{self.settings.default_project.upper()}-{key}"
        return key
