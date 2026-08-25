"""Delivery agent — branch, commit, push, pull request.

Every destructive operation happens here and nowhere else, behind the delivery
approval gate, and each one is skippable via ``--dry-run``. The agent refuses to
work on a protected branch and never force-pushes or rewrites history.
"""

from __future__ import annotations

import re
from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.core.errors import OrchestratorError, RepositoryError
from orchestrator.core.models import DeliveryResult, ReviewSummary, TrackerIssue
from orchestrator.integrations.git.hosting import build_host, describe_host
from orchestrator.integrations.git.repo import GitRepo

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_length: int = 40) -> str:
    slug = _SLUG_STRIP.sub("-", text.lower()).strip("-")
    if len(slug) <= max_length:
        return slug or "change"
    return slug[:max_length].rstrip("-") or "change"


class DeliveryAgent(Agent):
    name = "delivery"
    responsibility = "Create the branch, commit, push and open the pull request."
    output_model = DeliveryResult
    mutates_repository = True

    def branch_name(self, ctx: AgentContext, issue: TrackerIssue) -> str:
        git = ctx.settings.git
        prefix = git.branch_prefix_by_type.get(issue.issue_type.lower(), "feature")
        return git.branch_template.format(
            prefix=prefix,
            issue_key=issue.key,
            issue_key_lower=issue.key.lower(),
            slug=slugify(issue.summary),
            type=prefix,
        )

    def run(self, ctx: AgentContext, **kwargs: Any) -> DeliveryResult:
        log = ctx.log(self.name)
        review: ReviewSummary = kwargs.get("review") or ctx.blackboard["review"]
        issue: TrackerIssue = kwargs.get("issue") or ctx.blackboard["issue"]
        dry_run = bool(kwargs.get("dry_run", ctx.state.dry_run))

        repo = GitRepo(ctx.repo_path)
        repo.require_repo()

        settings = ctx.settings.git
        base = ctx.state.base_branch or ctx.profile.git.default_branch or settings.default_base_branch
        branch = ctx.state.branch or self.branch_name(ctx, issue)
        current = repo.current_branch()

        if current in settings.protected_branches and branch != current:
            log.info("on protected branch %s; creating %s", current, branch)
        elif current in settings.protected_branches and branch == current:
            raise RepositoryError(
                f"Refusing to commit directly to protected branch '{current}'.",
                hint="Configure git.branch_template or check out a feature branch first.",
            )

        result = DeliveryResult(
            branch=branch,
            base_branch=base,
            provider=settings.provider,
            dry_run=dry_run,
        )

        if dry_run:
            log.info("dry run: would create %s, commit, push to %s and open a PR", branch, settings.push_remote)
            ctx.audit(self.name, "delivery.dry_run", branch=branch, base=base)
            self.emit(ctx, result)
            ctx.blackboard["delivery"] = result
            return result

        # 1. branch
        if current != branch:
            if repo.branch_exists(branch):
                checkout = repo.checkout(branch)
                if not checkout.ok:
                    raise RepositoryError(f"Could not switch to {branch}: {checkout.tail(500)}")
            else:
                created = repo.create_branch(branch)
                if not created.ok:
                    raise RepositoryError(f"Could not create {branch}: {created.tail(500)}")
            ctx.audit(self.name, "git.branch_created", branch=branch, base=base)

        # 2. stage + commit
        staged = repo.stage()
        if not staged.ok:
            raise RepositoryError(f"git add failed: {staged.tail(500)}")

        commit = repo.commit(review.commit_message, sign=settings.sign_commits)
        if not commit.ok:
            if "nothing to commit" in (commit.stdout + commit.stderr).lower():
                raise RepositoryError(
                    "Nothing to commit — the implementation produced no changes.",
                    hint="Inspect the run's implementation artifact with `orc show`.",
                )
            raise RepositoryError(f"git commit failed: {commit.tail(800)}")
        result.commit_sha = repo.head_sha()
        ctx.audit(self.name, "git.committed", sha=result.commit_sha, branch=branch)

        # 3. push
        push = repo.push(remote=settings.push_remote, branch=branch)
        if not push.ok:
            raise RepositoryError(
                f"git push failed: {push.tail(800)}",
                hint="The commit is safe locally; push manually or resume after fixing access.",
            )
        result.pushed = True
        ctx.audit(self.name, "git.pushed", remote=settings.push_remote, branch=branch)

        # 4. pull request
        host = build_host(settings, ctx.settings.credentials, ctx.profile.git.remote_url)
        try:
            result.pr_url = host.create_pull_request(
                title=review.pr_title or f"{issue.key}: {issue.summary}",
                body=review.pr_body,
                head=branch,
                base=base,
                repo_path=str(ctx.repo_path),
                draft=bool(kwargs.get("draft", False)),
            )
        except OrchestratorError as exc:
            # The push already succeeded; a PR failure must not lose that work.
            log.warning("pull request not created: %s", exc)
            result.pr_url = None
            ctx.audit(self.name, "pr.failed", reason=str(exc), host=describe_host(host))
        else:
            if result.pr_url:
                ctx.audit(self.name, "pr.created", url=result.pr_url, host=describe_host(host))

        self.emit(ctx, result)
        ctx.blackboard["delivery"] = result
        return result
