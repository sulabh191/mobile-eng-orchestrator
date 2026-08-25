"""The orchestration engine.

One method — :meth:`Orchestrator.advance` — repeatedly looks at the run's
current :class:`WorkflowStatus`, executes the handler for that status, and
transitions. Every handler is small and idempotent-by-phase, which is what
makes ``orc resume`` possible: a crashed or approval-blocked run reloads its
state, rehydrates the artifacts it already produced, and carries on from the
same status it stopped at.

Nothing in here decides *what* the code should be — that is the agents' job.
This module decides what happens next, and who has to say yes first.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.agents.base import AgentContext
from orchestrator.agents.git_pr import DeliveryAgent
from orchestrator.agents.implementer import ImplementationAgent
from orchestrator.agents.jira_fetcher import JiraFetcherAgent
from orchestrator.agents.planner import PlanAgent
from orchestrator.agents.platform.base import get_platform_agent
from orchestrator.agents.requirements import RequirementsAgent
from orchestrator.agents.reviewer import ReviewAgent
from orchestrator.agents.validator import ValidationAgent
from orchestrator.core.approvals import ApprovalManager, Gate, GateRequest
from orchestrator.core.config import Settings, global_config_dir
from orchestrator.core.errors import (
    ApprovalRejected,
    ApprovalRequired,
    ConfigurationError,
    OrchestratorError,
    RepositoryError,
    ValidationFailed,
)
from orchestrator.core.logging import console, get_logger
from orchestrator.core.models import (
    Decision,
    DeliveryResult,
    ImplementationPlan,
    ImplementationResult,
    Platform,
    RequirementsDoc,
    ReviewSummary,
    TrackerIssue,
    ValidationReport,
)
from orchestrator.core.state import StateStore, WorkflowState, WorkflowStatus
from orchestrator.engine.base import Engine
from orchestrator.engine.factory import build_engine
from orchestrator.inspection.detector import inspect_repository
from orchestrator.inspection.profile import RepoProfile
from orchestrator.integrations.jira.factory import build_jira_client
from orchestrator.skills.loader import load_skills

logger = get_logger("workflow")

#: Phase names a caller may stop after, in pipeline order.
STOP_POINTS = (
    "fetch",
    "requirements",
    "plan",
    "implement",
    "validate",
    "review",
    "deliver",
)


@dataclass
class RunOptions:
    issue_key: str
    repo_path: Path
    platform_override: Platform | None = None
    engine_backend: str | None = None
    offline: bool = False
    dry_run: bool = False
    interactive: bool = True
    auto_approve: bool = False
    stop_after: str | None = None
    fixtures_dir: Path | None = None
    base_branch: str | None = None
    branch: str | None = None
    draft_pr: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """Drives one repository through the workflow."""

    def __init__(
        self,
        settings: Settings,
        *,
        repo_path: Path,
        engine: Engine | None = None,
        tracker: Any | None = None,
        profile: RepoProfile | None = None,
    ) -> None:
        self.settings = settings
        self.repo_path = Path(repo_path).resolve()
        self.store = StateStore(self.repo_path)
        self._engine = engine
        self._tracker = tracker
        self._profile = profile

    # -- lifecycle ------------------------------------------------------------- #

    def start(self, options: RunOptions) -> WorkflowState:
        """Create (or reuse) a run for an issue and drive it as far as it will go."""
        existing = self.store.find_by_issue(options.issue_key)
        if existing is not None:
            console.print(
                f"[muted]Resuming existing run {existing.run_id} for {options.issue_key} "
                f"at {existing.status.value}.[/muted]"
            )
            return self.advance(existing, options)

        if options.auto_approve and os.environ.get("ORC_I_UNDERSTAND_AUTO_APPROVE") != "1":
            raise ConfigurationError(
                "Auto-approval was requested but the safety acknowledgement is not set.",
                hint=(
                    "--yes skips every human gate, including commit and push. Export "
                    "ORC_I_UNDERSTAND_AUTO_APPROVE=1 to confirm you intend this."
                ),
            )

        profile = self._resolve_profile(options.platform_override)
        state = WorkflowState(
            issue_key=options.issue_key.upper(),
            repo_path=str(self.repo_path),
            platform=profile.platform,
            engine=options.engine_backend or self.settings.engine.backend,
            dry_run=options.dry_run,
            auto_approve=options.auto_approve or self.settings.behaviour.auto_approve,
            base_branch=options.base_branch or profile.git.default_branch,
            branch=options.branch,
            metadata=dict(options.metadata),
        )
        self.store.create(state)
        return self.advance(state, options)

    def resume(self, run_id: str | None, options: RunOptions) -> WorkflowState:
        state = self.store.resolve(run_id)
        if state.status.is_terminal:
            console.print(f"[muted]Run {state.run_id} is already {state.status.value}.[/muted]")
            return state
        if state.status is WorkflowStatus.FAILED:
            recovery = self._recovery_status(state)
            console.print(f"[muted]Recovering failed run at {recovery.value}.[/muted]")
            state.transition_to(recovery, note="resumed after failure")
            self.store.save(state)
        return self.advance(state, options)

    # -- the loop ---------------------------------------------------------------- #

    def advance(self, state: WorkflowState, options: RunOptions) -> WorkflowState:
        profile = self._resolve_profile(options.platform_override or state.platform)
        ctx = self._context(state, profile, options)
        self._hydrate(ctx)
        self._preflight(ctx)

        handlers: dict[WorkflowStatus, Callable[[AgentContext, RunOptions], None]] = {
            WorkflowStatus.INITIALIZED: self._phase_fetch,
            WorkflowStatus.JIRA_FETCHED: self._phase_requirements,
            WorkflowStatus.REQUIREMENTS_REVIEW: self._phase_plan,
            WorkflowStatus.PLAN_GENERATED: self._gate_plan,
            WorkflowStatus.PLAN_APPROVED: self._phase_implement,
            WorkflowStatus.IMPLEMENTING: self._phase_implement,
            WorkflowStatus.IMPLEMENTATION_COMPLETE: self._phase_validate,
            WorkflowStatus.VALIDATING: self._phase_validate,
            WorkflowStatus.VALIDATION_FAILED: self._phase_remediate,
            WorkflowStatus.VALIDATION_PASSED: self._phase_review,
            WorkflowStatus.REVIEW_READY: self._phase_prepare_delivery,
            WorkflowStatus.READY_FOR_PR: self._phase_deliver,
            WorkflowStatus.PR_CREATED: self._phase_finish,
        }

        guard = 0
        while not state.status.is_terminal:
            guard += 1
            if guard > 40:  # pragma: no cover - defensive
                raise OrchestratorError("Workflow made no progress; aborting to avoid a loop.")

            handler = handlers.get(state.status)
            if handler is None:
                break

            phase = self._phase_for_status(state.status)
            try:
                handler(ctx, options)
            except ApprovalRequired:
                self.store.save(state)
                raise
            except ApprovalRejected as exc:
                state.transition_to(WorkflowStatus.REJECTED, note=str(exc))
                self.store.save(state)
                ctx.audit("workflow", "run.rejected", reason=str(exc))
                console.print(f"[fail]Run stopped:[/fail] {exc}")
                return state
            except OrchestratorError as exc:
                state.last_error = str(exc)
                state.transition_to(WorkflowStatus.FAILED, note=str(exc))
                self.store.save(state)
                ctx.audit("workflow", "run.failed", phase=phase, error=str(exc))
                raise

            self.store.save(state)

            if options.stop_after and phase == options.stop_after:
                console.print(f"[muted]Stopping after '{phase}' as requested.[/muted]")
                break

        return state

    # -- phases ------------------------------------------------------------------ #

    def _phase_fetch(self, ctx: AgentContext, options: RunOptions) -> None:
        issue = JiraFetcherAgent().run(ctx, issue_key=ctx.state.issue_key)
        ctx.state.issue_key = issue.key
        ctx.state.transition_to(WorkflowStatus.JIRA_FETCHED, note=f"fetched {issue.key}")
        console.print(f"[phase]Fetched[/phase] {issue.key} — {issue.summary}")

    def _phase_requirements(self, ctx: AgentContext, options: RunOptions) -> None:
        doc = RequirementsAgent().run(ctx)
        ctx.state.transition_to(
            WorkflowStatus.REQUIREMENTS_REVIEW,
            note=f"{len(doc.requirements)} requirement(s)",
        )
        console.print(
            f"[phase]Requirements[/phase] {len(doc.requirements)} derived"
            + (f", {len(doc.open_questions)} open question(s)" if doc.open_questions else "")
        )

        # Only interrupt the developer when there is something genuinely unresolved.
        if doc.open_questions:
            self._approvals(ctx, options).request(
                GateRequest(
                    gate=Gate.REQUIREMENTS,
                    title=f"Requirements for {doc.issue_key}",
                    body=(
                        RequirementsAgent.render_markdown(doc)
                        + "\n\nThese questions are unresolved. Approve to proceed on the stated "
                        "assumptions, or reject and clarify the ticket."
                    ),
                    facts=tuple(("open question", q) for q in doc.open_questions[:5]),
                )
            )
        else:
            ctx.state.record_approval(
                Gate.REQUIREMENTS, Decision.APPROVED, actor="orchestrator", comment="no open questions"
            )

    def _phase_plan(self, ctx: AgentContext, options: RunOptions) -> None:
        feedback = self._last_change_request(ctx, Gate.PLAN)
        plan = PlanAgent().run(ctx, feedback=feedback)
        ctx.state.transition_to(WorkflowStatus.PLAN_GENERATED, note=f"{len(plan.steps)} step(s)")
        console.print(f"[phase]Plan[/phase] {len(plan.steps)} step(s) generated")

    def _gate_plan(self, ctx: AgentContext, options: RunOptions) -> None:
        plan: ImplementationPlan = ctx.blackboard["plan"]
        decision = self._approvals(ctx, options).request(
            GateRequest(
                gate=Gate.PLAN,
                title=f"Implementation plan for {plan.issue_key}",
                body=PlanAgent.render_markdown(plan),
                facts=(
                    ("repository", str(ctx.repo_path)),
                    ("platform", ctx.profile.platform.display),
                    ("steps", str(len(plan.steps))),
                    ("high risk steps", str(sum(1 for s in plan.steps if s.risk == "high"))),
                ),
            )
        )
        if decision is Decision.CHANGES_REQUESTED:
            ctx.state.transition_to(WorkflowStatus.PLAN_GENERATED, note="changes requested")
            self._phase_plan(ctx, options)
            return
        ctx.state.transition_to(WorkflowStatus.PLAN_APPROVED, note="plan approved")

    def _phase_implement(self, ctx: AgentContext, options: RunOptions) -> None:
        if ctx.state.status is not WorkflowStatus.IMPLEMENTING:
            ctx.state.transition_to(WorkflowStatus.IMPLEMENTING)
            self.store.save(ctx.state)

        remediation = ctx.blackboard.get("failed_validation")
        result = ImplementationAgent().run(
            ctx,
            remediation_for=remediation,
            feedback=self._last_change_request(ctx, Gate.IMPLEMENTATION),
        )
        ctx.blackboard.pop("failed_validation", None)
        ctx.state.transition_to(
            WorkflowStatus.IMPLEMENTATION_COMPLETE,
            note=f"{len(result.file_changes)} file(s) changed",
        )
        console.print(
            f"[phase]Implementation[/phase] {len(result.completed_step_ids)} step(s), "
            f"{len(result.file_changes)} file(s) changed"
        )

    def _phase_validate(self, ctx: AgentContext, options: RunOptions) -> None:
        if ctx.state.status is not WorkflowStatus.VALIDATING:
            ctx.state.transition_to(WorkflowStatus.VALIDATING)
            self.store.save(ctx.state)

        attempt = ctx.state.attempts.get("validation", 0) + 1
        ctx.state.attempts["validation"] = attempt
        report = ValidationAgent().run(ctx, attempt=attempt)

        if report.passed:
            ctx.state.transition_to(WorkflowStatus.VALIDATION_PASSED, note=f"attempt {attempt}")
            console.print(f"[ok]Validation passed[/ok] ({len(report.checks)} check(s))")
        else:
            ctx.blackboard["failed_validation"] = report
            ctx.state.transition_to(
                WorkflowStatus.VALIDATION_FAILED, note=report.failure_summary()
            )
            console.print(f"[fail]Validation failed:[/fail] {report.failure_summary()}")

    def _phase_remediate(self, ctx: AgentContext, options: RunOptions) -> None:
        report: ValidationReport = ctx.blackboard.get("failed_validation") or ctx.blackboard["validation"]
        attempts = ctx.state.attempts.get("remediation", 0)
        budget = self.settings.validation.max_remediation_attempts

        if attempts >= budget:
            raise ValidationFailed(
                f"Validation still failing after {attempts} remediation attempt(s): "
                f"{report.failure_summary()}",
                hint=(
                    "Inspect the validation artifact, fix the repository by hand, then "
                    f"`orc resume {ctx.state.run_id}`."
                ),
            )

        ctx.state.attempts["remediation"] = attempts + 1
        console.print(
            f"[phase]Remediation[/phase] attempt {attempts + 1}/{budget} for "
            f"{report.failure_summary()}"
        )
        ctx.state.transition_to(WorkflowStatus.IMPLEMENTING, note="remediating validation failures")

    def _phase_review(self, ctx: AgentContext, options: RunOptions) -> None:
        summary = ReviewAgent().run(ctx)
        ctx.state.transition_to(WorkflowStatus.REVIEW_READY, note=summary.headline[:80])
        console.print(f"[phase]Review[/phase] {summary.headline}")

    def _phase_prepare_delivery(self, ctx: AgentContext, options: RunOptions) -> None:
        issue: TrackerIssue = ctx.blackboard["issue"]
        if not ctx.state.branch:
            ctx.state.branch = DeliveryAgent().branch_name(ctx, issue)
        ctx.state.transition_to(WorkflowStatus.READY_FOR_PR, note=f"branch {ctx.state.branch}")

    def _phase_deliver(self, ctx: AgentContext, options: RunOptions) -> None:
        review: ReviewSummary = ctx.blackboard["review"]
        validation: ValidationReport = ctx.blackboard["validation"]
        decision = self._approvals(ctx, options).request(
            GateRequest(
                gate=Gate.DELIVERY,
                title=f"Commit, push and open a PR for {ctx.state.issue_key}",
                body=ReviewAgent.render_markdown(review, validation),
                facts=(
                    ("repository", str(ctx.repo_path)),
                    ("branch", ctx.state.branch or "(to be created)"),
                    ("base", ctx.state.base_branch or self.settings.git.default_base_branch),
                    ("remote", ctx.profile.git.remote_display or "(none)"),
                    ("provider", self.settings.git.provider),
                    ("dry run", "yes" if ctx.state.dry_run else "no"),
                ),
            )
        )
        if decision is Decision.CHANGES_REQUESTED:
            ctx.state.transition_to(WorkflowStatus.IMPLEMENTING, note="changes requested at delivery")
            return

        result: DeliveryResult = DeliveryAgent().run(
            ctx, dry_run=ctx.state.dry_run, draft=options.draft_pr
        )
        if result.dry_run:
            ctx.state.transition_to(WorkflowStatus.COMPLETED, note="dry run complete")
            console.print("[ok]Dry run complete[/ok] — nothing was committed or pushed.")
            return

        if result.pr_url:
            ctx.state.transition_to(WorkflowStatus.PR_CREATED, note=result.pr_url)
            console.print(f"[ok]Pull request opened:[/ok] {result.pr_url}")
        else:
            ctx.state.transition_to(WorkflowStatus.COMPLETED, note="committed and pushed, no PR")
            console.print("[ok]Committed and pushed[/ok] (no pull request created).")

    def _phase_finish(self, ctx: AgentContext, options: RunOptions) -> None:
        delivery: DeliveryResult | None = ctx.blackboard.get("delivery")
        if delivery and delivery.pr_url and ctx.tracker is not None:
            JiraFetcherAgent().comment_on_issue(
                ctx,
                f"Pull request opened by the engineering orchestrator: {delivery.pr_url}",
            )
        ctx.state.transition_to(WorkflowStatus.COMPLETED, note="run complete")
        ctx.audit("workflow", "run.completed", pr_url=delivery.pr_url if delivery else None)
        console.print(f"[ok]Run {ctx.state.run_id} complete.[/ok]")

    # -- context -------------------------------------------------------------------- #

    def _resolve_profile(self, platform_override: Platform | None) -> RepoProfile:
        if self._profile is not None:
            return self._profile
        override = platform_override if platform_override is not Platform.GENERIC else None
        self._profile = inspect_repository(self.repo_path, platform_override=override)
        return self._profile

    def _context(self, state: WorkflowState, profile: RepoProfile, options: RunOptions) -> AgentContext:
        engine = self._engine or build_engine(
            self.settings, backend=options.engine_backend or state.engine
        )
        tracker = self._tracker
        if tracker is None:
            tracker = build_jira_client(
                self.settings,
                offline=options.offline or engine.name == "mock",
                fixtures_dir=options.fixtures_dir,
            )
        skills = load_skills(
            user_dir=global_config_dir() / "skills",
            repo_dir=self.repo_path / ".orchestrator" / "skills",
        )
        ctx = AgentContext(
            settings=self.settings,
            store=self.store,
            state=state,
            profile=profile,
            engine=engine,
            tracker=tracker,
        )
        ctx.blackboard["skills"] = skills
        ctx.blackboard["platform_agent"] = get_platform_agent(profile.platform)
        return ctx

    def _approvals(self, ctx: AgentContext, options: RunOptions) -> ApprovalManager:
        return ApprovalManager(
            self.store,
            ctx.state,
            interactive=options.interactive and self.settings.behaviour.interactive,
        )

    def _preflight(self, ctx: AgentContext) -> None:
        if ctx.blackboard.get("preflight_done"):
            return
        if not ctx.profile.git.is_repo:
            raise RepositoryError(
                f"{self.repo_path} is not a git repository.",
                hint="The orchestrator commits its work, so it needs a git checkout.",
            )
        agent = ctx.blackboard["platform_agent"]
        report = agent.preflight(ctx)
        for warning in report.warnings:
            console.print(f"[muted]preflight: {warning}[/muted]")
        if report.blockers:
            raise RepositoryError(
                "Pre-flight checks failed:\n" + "\n".join(f"- {b}" for b in report.blockers)
            )
        ctx.audit(agent.name, "preflight.completed", warnings=report.warnings, facts=report.facts)
        ctx.blackboard["preflight_done"] = True

    def _hydrate(self, ctx: AgentContext) -> None:
        """Reload artifacts produced by earlier phases so a resumed run has context."""
        mapping = {
            "jira-fetcher.json": ("issue", TrackerIssue),
            "requirements.json": ("requirements", RequirementsDoc),
            "planner.json": ("plan", ImplementationPlan),
            "implementer.json": ("implementation", ImplementationResult),
            "validator.json": ("validation", ValidationReport),
            "reviewer.json": ("review", ReviewSummary),
            "delivery.json": ("delivery", DeliveryResult),
        }
        for artifact, (key, model) in mapping.items():
            raw = self.store.read_artifact(ctx.state, artifact)
            if raw:
                try:
                    ctx.blackboard[key] = model.model_validate(json.loads(raw))
                except Exception as exc:  # pragma: no cover - corrupt artifact
                    logger.warning("could not rehydrate %s: %s", artifact, exc)
        validation = ctx.blackboard.get("validation")
        if (
            ctx.state.status is WorkflowStatus.VALIDATION_FAILED
            and isinstance(validation, ValidationReport)
            and not validation.passed
        ):
            ctx.blackboard["failed_validation"] = validation

    # -- helpers -------------------------------------------------------------------- #

    @staticmethod
    def _phase_for_status(status: WorkflowStatus) -> str:
        return {
            WorkflowStatus.INITIALIZED: "fetch",
            WorkflowStatus.JIRA_FETCHED: "requirements",
            WorkflowStatus.REQUIREMENTS_REVIEW: "plan",
            WorkflowStatus.PLAN_GENERATED: "plan",
            WorkflowStatus.PLAN_APPROVED: "implement",
            WorkflowStatus.IMPLEMENTING: "implement",
            WorkflowStatus.IMPLEMENTATION_COMPLETE: "validate",
            WorkflowStatus.VALIDATING: "validate",
            WorkflowStatus.VALIDATION_FAILED: "validate",
            WorkflowStatus.VALIDATION_PASSED: "review",
            WorkflowStatus.REVIEW_READY: "deliver",
            WorkflowStatus.READY_FOR_PR: "deliver",
            WorkflowStatus.PR_CREATED: "deliver",
        }.get(status, "unknown")

    @staticmethod
    def _recovery_status(state: WorkflowState) -> WorkflowStatus:
        """Where a FAILED run should re-enter the pipeline.

        A run that died with validation failing re-enters at VALIDATING rather
        than VALIDATION_FAILED: the developer has had a chance to fix things by
        hand, so the checks deserve another honest run before the remediation
        budget is consulted again.
        """
        for change in reversed(state.history):
            if change.from_status and change.to_status is WorkflowStatus.FAILED:
                if change.from_status is WorkflowStatus.VALIDATION_FAILED:
                    return WorkflowStatus.VALIDATING
                return change.from_status
        return WorkflowStatus.INITIALIZED

    @staticmethod
    def _last_change_request(ctx: AgentContext, gate: str) -> str | None:
        record = ctx.state.latest_approval(gate)
        if record and record.decision is Decision.CHANGES_REQUESTED:
            return record.comment
        return None
