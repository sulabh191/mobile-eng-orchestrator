"""Review agent — turns a validated change into something a human can judge."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.prompts import compose, skills_block, system_prompt
from orchestrator.core.models import (
    ImplementationResult,
    RequirementsDoc,
    ReviewSummary,
    ValidationReport,
)
from orchestrator.engine.base import EngineMode, EngineRequest
from orchestrator.integrations.git.repo import GitRepo

ROLE = (
    "summarise a completed, validated change for the human who must review and own it: "
    "what changed, which requirements it covers, what is risky, and what the reviewer "
    "should look at first. You also draft the commit message and PR description. You do "
    "not modify anything."
)


class ReviewAgent(Agent):
    name = "reviewer"
    responsibility = "Summarise the change, map requirement coverage, draft the commit and PR text."
    output_model = ReviewSummary

    def run(self, ctx: AgentContext, **kwargs: Any) -> ReviewSummary:
        log = ctx.log(self.name)
        requirements: RequirementsDoc = kwargs.get("requirements") or ctx.blackboard["requirements"]
        implementation: ImplementationResult = (
            kwargs.get("implementation") or ctx.blackboard["implementation"]
        )
        validation: ValidationReport = kwargs.get("validation") or ctx.blackboard["validation"]
        library = ctx.blackboard["skills"]

        repo = GitRepo(ctx.repo_path)
        diff = repo.diff_text() if repo.is_repo else ""

        prompt = compose(
            "## Requirements\n\n"
            + "\n".join(f"- **{r.id}**: {r.statement}" for r in requirements.requirements),
            "## Files changed\n\n"
            + "\n".join(
                f"- `{fc.path}` ({fc.change_type}, +{fc.lines_added}/-{fc.lines_removed})"
                for fc in implementation.file_changes
            ),
            self._notes_block(implementation),
            "## Validation\n\n"
            + f"Result: {'passed' if validation.passed else 'FAILED'} — "
            + ", ".join(f"{c.name}={c.status.value}" for c in validation.checks),
            ("## Diff\n\n```diff\n" + diff + "\n```") if diff else "",
            skills_block(library, "review", "delivery", platform=ctx.profile.platform),
            "## Task\n\n"
            "Write the review summary. Map every requirement id to covered / partial / "
            "missing, honestly. Draft a conventional-commit message and a PR body.",
        )

        log.info("reviewing %s", ctx.issue_key)
        summary, response = ctx.engine.generate_structured(
            EngineRequest(
                task="review",
                system=system_prompt(ROLE),
                prompt=prompt,
                mode=EngineMode.READ_ONLY,
                cwd=ctx.repo_path,
                metadata={
                    "issue_key": ctx.issue_key,
                    "platform": ctx.profile.platform.value,
                    "issue": ctx.blackboard["issue"].model_dump(mode="json"),
                    "requirements": requirements.model_dump(mode="json"),
                    "touched_paths": implementation.touched_paths,
                },
            ),
            ReviewSummary,
        )
        summary.issue_key = ctx.issue_key

        gaps = [rid for rid, state in summary.requirements_coverage.items() if state != "covered"]
        ctx.audit(
            self.name,
            "review.completed",
            coverage_gaps=gaps,
            engine_seconds=round(response.duration_seconds, 2),
        )
        self.emit(ctx, summary)
        ctx.save_artifact("review.md", self.render_markdown(summary, validation))
        ctx.blackboard["review"] = summary
        return summary

    @staticmethod
    def _notes_block(implementation: ImplementationResult) -> str:
        if not implementation.notes:
            return ""
        return "## Implementation notes\n\n" + "\n".join(f"- {n}" for n in implementation.notes)

    @staticmethod
    def render_markdown(summary: ReviewSummary, validation: ValidationReport) -> str:
        lines = [f"# Review — {summary.issue_key}", "", summary.headline, "", "## What changed", ""]
        lines += [f"- {item}" for item in summary.what_changed]
        if summary.requirements_coverage:
            lines += ["", "## Requirement coverage", "", "| Requirement | Coverage |", "| --- | --- |"]
            lines += [f"| {rid} | {state} |" for rid, state in sorted(summary.requirements_coverage.items())]
        for heading, items in (
            ("Risk notes", summary.risk_notes),
            ("Reviewer checklist", summary.reviewer_checklist),
        ):
            if items:
                lines += ["", f"## {heading}", "", *[f"- {item}" for item in items]]
        lines += [
            "",
            "## Validation",
            "",
            f"{'All required checks passed.' if validation.passed else validation.failure_summary()}",
            "",
            "## Proposed commit",
            "",
            "```",
            summary.commit_message,
            "```",
            "",
            "## Proposed pull request",
            "",
            f"**{summary.pr_title}**",
            "",
            summary.pr_body,
        ]
        return "\n".join(lines)
