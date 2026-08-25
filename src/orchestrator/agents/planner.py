"""Plan agent — requirements in, a reviewable implementation plan out.

This agent reads the repository (read-only) so that the plan names real files.
Its output is what the developer approves at the plan gate, and it is the only
input the implementation agent is allowed to act on.
"""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.prompts import compose, repository_block, skills_block, system_prompt
from orchestrator.core.models import ImplementationPlan, RequirementsDoc
from orchestrator.engine.base import EngineMode, EngineRequest

ROLE = (
    "design the smallest correct implementation for an already-agreed set of requirements, "
    "expressed as ordered steps against real files in this repository. You may read the "
    "repository. You must not modify it."
)


class PlanAgent(Agent):
    name = "planner"
    responsibility = "Produce an ordered, file-level implementation plan for developer approval."
    output_model = ImplementationPlan

    def run(self, ctx: AgentContext, **kwargs: Any) -> ImplementationPlan:
        log = ctx.log(self.name)
        requirements: RequirementsDoc = kwargs.get("requirements") or ctx.blackboard["requirements"]
        library = ctx.blackboard["skills"]
        feedback: str | None = kwargs.get("feedback")

        prompt = compose(
            "## Approved requirements\n\n"
            + "\n".join(
                f"- **{req.id}** ({req.priority}): {req.statement}"
                for req in requirements.requirements
            ),
            self._context_block(requirements),
            repository_block(ctx.profile),
            skills_block(library, "planning", platform=ctx.profile.platform),
            self._feedback_block(feedback),
            "## Task\n\n"
            "Explore the repository enough to name the actual files each step touches, then "
            "produce the plan. Every requirement id must be satisfied by at least one step. "
            "Do not modify anything.",
        )

        log.info("planning %s (%d requirements)", requirements.issue_key, len(requirements.requirements))
        plan, response = ctx.engine.generate_structured(
            EngineRequest(
                task="plan",
                system=system_prompt(ROLE),
                prompt=prompt,
                mode=EngineMode.READ_ONLY,
                cwd=ctx.repo_path,
                max_turns=kwargs.get("max_turns", 30),
                metadata={
                    "issue_key": requirements.issue_key,
                    "platform": ctx.profile.platform.value,
                    "requirements": requirements.model_dump(mode="json"),
                },
            ),
            ImplementationPlan,
        )

        plan.issue_key = requirements.issue_key
        plan.platform = ctx.profile.platform

        coverage_gaps = self._coverage_gaps(requirements, plan)
        if coverage_gaps:
            plan.risks.append(
                "Requirements with no plan step: " + ", ".join(sorted(coverage_gaps))
            )

        ctx.audit(
            self.name,
            "plan.generated",
            steps=len(plan.steps),
            uncovered_requirements=sorted(coverage_gaps),
            engine_seconds=round(response.duration_seconds, 2),
        )
        self.emit(ctx, plan)
        ctx.save_artifact("plan.md", self.render_markdown(plan))
        ctx.blackboard["plan"] = plan
        return plan

    @staticmethod
    def _context_block(requirements: RequirementsDoc) -> str:
        parts = []
        if requirements.non_goals:
            parts.append("Non-goals:\n" + "\n".join(f"- {n}" for n in requirements.non_goals))
        if requirements.assumptions:
            parts.append("Assumptions:\n" + "\n".join(f"- {a}" for a in requirements.assumptions))
        return "\n\n".join(parts)

    @staticmethod
    def _feedback_block(feedback: str | None) -> str:
        if not feedback:
            return ""
        return (
            "## Developer feedback on the previous plan\n\n"
            f"{feedback}\n\n"
            "Address this directly in the revised plan."
        )

    @staticmethod
    def _coverage_gaps(requirements: RequirementsDoc, plan: ImplementationPlan) -> set[str]:
        covered = {rid for step in plan.steps for rid in step.satisfies}
        return {req.id for req in requirements.requirements if req.id not in covered}

    @staticmethod
    def render_markdown(plan: ImplementationPlan) -> str:
        lines = [
            f"# Implementation plan — {plan.issue_key}",
            "",
            plan.summary,
            "",
            "## Steps",
            "",
        ]
        for step in plan.ordered_steps():
            depends = f" · after {', '.join(step.depends_on)}" if step.depends_on else ""
            lines.append(f"### {step.id} · {step.title}  _(risk: {step.risk}{depends})_")
            lines.append("")
            lines.append(step.intent)
            if step.target_files:
                lines.append("")
                lines.append("Files: " + ", ".join(f"`{f}`" for f in step.target_files))
            if step.satisfies:
                lines.append(f"Satisfies: {', '.join(step.satisfies)}")
            if step.verification:
                lines.append(f"Verified by: {step.verification}")
            lines.append("")
        for heading, items in (
            ("Test strategy", plan.test_strategy),
            ("Risks", plan.risks),
            ("Out of scope", plan.out_of_scope),
        ):
            if items:
                lines += [f"## {heading}", "", *[f"- {item}" for item in items], ""]
        if plan.rollback:
            lines += ["## Rollback", "", plan.rollback, ""]
        return "\n".join(lines)
