"""Pull-request creation across git hosts.

Two strategies, in order of preference:

1. the host's own CLI (``gh``, ``glab``) — inherits the developer's existing
   auth, which is the least surprising thing on a laptop;
2. the REST API with a token from the credential store — for CI.

Every method is a no-op that returns ``None`` when ``dry_run`` is set, so the
delivery agent can be rehearsed safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from orchestrator.core.config import GitSettings
from orchestrator.core.credentials import CredentialStore
from orchestrator.core.errors import OrchestratorError
from orchestrator.core.logging import get_logger
from orchestrator.core.process import run_command, which

logger = get_logger("hosting")

_GITHUB_SSH = re.compile(r"git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
_GITHUB_HTTPS = re.compile(r"https://[^/]*github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")


@dataclass
class RepoSlug:
    owner: str
    repo: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.repo}"


def parse_github_slug(remote_url: str | None) -> RepoSlug | None:
    if not remote_url:
        return None
    for pattern in (_GITHUB_SSH, _GITHUB_HTTPS):
        if match := pattern.search(remote_url.strip()):
            return RepoSlug(match.group("owner"), match.group("repo"))
    return None


class PullRequestHost:
    """Base class: a host that can open a pull/merge request."""

    provider = "none"

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        repo_path: str,
        draft: bool = False,
    ) -> str | None:
        raise NotImplementedError


class NoopHost(PullRequestHost):
    """Used when no provider is configured — commit/push only."""

    provider = "none"

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        repo_path: str,
        draft: bool = False,
    ) -> str | None:
        logger.info("No git host configured; skipping pull-request creation for %s.", head)
        return None


class GitHubHost(PullRequestHost):
    provider = "github"

    def __init__(self, settings: GitSettings, credentials: CredentialStore, remote_url: str | None):
        self.settings = settings
        self.credentials = credentials
        self.slug = parse_github_slug(remote_url)

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        repo_path: str,
        draft: bool = False,
    ) -> str | None:
        if which("gh"):
            args = [
                "gh", "pr", "create",
                "--title", title,
                "--body", body,
                "--head", head,
                "--base", base,
            ]
            if draft:
                args.append("--draft")
            result = run_command(args, cwd=repo_path, timeout=180)
            if result.ok:
                url = next(
                    (line.strip() for line in result.stdout.splitlines() if line.startswith("http")),
                    None,
                )
                if url:
                    return url
            logger.debug("gh pr create failed (%s); falling back to REST", result.exit_code)

        token, _ = self.credentials.get("GITHUB_TOKEN")
        if not token or not self.slug:
            raise OrchestratorError(
                "Cannot open a GitHub pull request.",
                hint=(
                    "Install and authenticate the `gh` CLI, or set ORC_GITHUB_TOKEN with "
                    "`orc config set-secret ORC_GITHUB_TOKEN`."
                ),
            )

        response = httpx.post(
            f"https://api.github.com/repos/{self.slug.full}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
            timeout=60.0,
        )
        if response.status_code >= 400:
            raise OrchestratorError(
                f"GitHub rejected the pull request ({response.status_code}): "
                f"{response.text[:300]}"
            )
        return str(response.json().get("html_url"))


class GitLabHost(PullRequestHost):
    provider = "gitlab"

    def __init__(self, settings: GitSettings, credentials: CredentialStore, remote_url: str | None):
        self.settings = settings
        self.credentials = credentials
        self.remote_url = remote_url

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        repo_path: str,
        draft: bool = False,
    ) -> str | None:
        if not which("glab"):
            raise OrchestratorError(
                "GitLab support requires the `glab` CLI.",
                hint="Install glab and run `glab auth login`, or set git.provider to none.",
            )
        args = [
            "glab", "mr", "create",
            "--title", title,
            "--description", body,
            "--source-branch", head,
            "--target-branch", base,
            "--yes",
        ]
        if draft:
            args.append("--draft")
        result = run_command(args, cwd=repo_path, timeout=180)
        if not result.ok:
            raise OrchestratorError(f"glab mr create failed: {result.tail(1000)}")
        return next(
            (line.strip() for line in result.stdout.splitlines() if line.startswith("http")), None
        )


def build_host(
    settings: GitSettings, credentials: CredentialStore, remote_url: str | None
) -> PullRequestHost:
    provider = (settings.provider or "none").lower()
    if provider == "github":
        return GitHubHost(settings, credentials, remote_url)
    if provider == "gitlab":
        return GitLabHost(settings, credentials, remote_url)
    return NoopHost()


def describe_host(host: PullRequestHost) -> str:
    if isinstance(host, GitHubHost):
        return f"github:{host.slug.full}" if host.slug else "github:(unknown repo)"
    return host.provider


__all__ = [
    "GitHubHost",
    "GitLabHost",
    "NoopHost",
    "PullRequestHost",
    "RepoSlug",
    "build_host",
    "describe_host",
    "parse_github_slug",
]
