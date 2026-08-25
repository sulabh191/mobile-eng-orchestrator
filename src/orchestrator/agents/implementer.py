"""Implementation agent — the only agent permitted to modify the repository.

It runs strictly after the plan gate has been approved, in EDIT mode, and its
reported file changes are reconciled against `git status` afterwards: what the
engine *claims* it changed is never trusted over what the working tree shows.
"""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.prompts import compose, repository_block, skills_block, system_prompt
from orchestrator.core.errors import EngineError
from orchestrator.core.models import ImplementationPlan, ImplementationResult, ValidationReport
from orchestrator.engine.base import EngineMode, EngineRequest
from orchestrator.integrations.git.repo import GitRepo

ROLE = (
    "implement an approved plan inside this repository, one step at a time, changing as "
    "little as possible. The plan has already been reviewed by a human; deviating from it "
    "silently is the single worst thing you can do."
)


class ImplementationAgent(Agent):
    name = "implementer"
    responsibility = "Apply the approved plan to the repository, and nothing beyond it."
    output_model = ImplementationResult
    mutates_repository = True

    def run(self, ctx: AgentContext, **kwargs: Any) -> ImplementationResult:
        log = ctx.log(self.name)
        plan: ImplementationPlan = kwargs.get("plan") or ctx.blackboard["plan"]
        library = ctx.blackboard["skills"]
        remediation: ValidationReport | None = kwargs.get("remediation_for")
        feedback: str | None = kwargs.get("feedback")

        if not ctx.engine.supports_editing:
            raise EngineError(
                f"Engine '{ctx.engine.name}' cannot edit files.",
                hint="Use the claude_code or agent_sdk backend for implementation.",
            )

        repo = GitRepo(ctx.repo_path)
        before = repo.head_sha() if repo.is_repo else None

        prompt = compose(
            "## Approved plan\n\n" + self._plan_block(plan),
            repository_block(ctx.profile),
            skills_block(library, "implementation", platform=ctx.profile.platform),
            self._remediation_block(remediation),
            self._feedback_block(feedback),
            "## Task\n\n"
            "Work through the steps in order and make the edits. Then report exactly which "
            "steps you completed, which you skipped and why, and every file you touched. "
            "Do not commit, do not stage, do not run git. Do not touch files outside the "
            "plan's scope.",
        )

        log.info("implementing %d step(s) for %s", len(plan.steps), plan.issue_key)
        result, response = ctx.engine.generate_structured(
            EngineRequest(
                task="implement",
                system=system_prompt(ROLE),
                prompt=prompt,
                mode=EngineMode.EDIT,
                cwd=ctx.repo_path,
                max_turns=kwargs.get("max_turns", 80),
                metadata={
                    "issue_key": plan.issue_key,
                    "platform": ctx.profile.platform.value,
                    "plan": plan.model_dump(mode="json"),
                },
            ),
            ImplementationResult,
        )
        result.issue_key = plan.issue_key

        # Ground truth beats self-report.
        if repo.is_repo:
            actual = repo.diff_stat()
            claimed = {fc.path for fc in result.file_changes}
            observed = {fc.path for fc in actual}
            result.file_changes = actual
            if missing := observed - claimed:
                result.notes.append(
                    "Files changed but not reported by the engine: " + ", ".join(sorted(missing))
                )
            if phantom := claimed - observed:
                result.notes.append(
                    "Files reported but not actually changed: " + ", ".join(sorted(phantom))
                )
            if before and before != repo.head_sha():
                result.notes.append(
                    "HEAD moved during implementation — the engine committed something it "
                    "should not have."
                )

        skipped = set(result.skipped_step_ids)
        expected = {step.id for step in plan.steps}
        if unaddressed := expected - set(result.completed_step_ids) - skipped:
            result.notes.append("Plan steps neither completed nor skipped: " + ", ".join(sorted(unaddressed)))

        ctx.audit(
            self.name,
            "implementation.completed",
            steps_completed=len(result.completed_step_ids),
            steps_skipped=len(result.skipped_step_ids),
            files_changed=len(result.file_changes),
            engine_seconds=round(response.duration_seconds, 2),
        )
        self.emit(ctx, result)
        ctx.blackboard["implementation"] = result
        return result

    # -- prompt fragments ------------------------------------------------------ #

    @staticmethod
    def _plan_block(plan: ImplementationPlan) -> str:
        lines = [plan.summary, ""]
        for step in plan.ordered_steps():
            lines.append(f"**{step.id} — {step.title}** (risk: {step.risk})")
            lines.append(step.intent)
            if step.target_files:
                lines.append("Files: " + ", ".join(step.target_files))
            if step.verification:
                lines.append(f"Verified by: {step.verification}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _remediation_block(report: ValidationReport | None) -> str:
        if report is None:
            return ""
        lines = [
            "## Validation failures to fix",
            "",
            "Your previous attempt did not pass validation. Fix these specific failures "
            "without changing anything else:",
            "",
        ]
        for check in report.failures:
            lines.append(f"### {check.name} (exit {check.exit_code})")
            lines.append("```")
            lines.append(check.output_tail[-3000:])
            lines.append("```")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _feedback_block(feedback: str | None) -> str:
        if not feedback:
            return ""
        return f"## Developer feedback\n\n{feedback}\n\nAddress this before anything else."
