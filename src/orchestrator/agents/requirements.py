"""Requirements agent — ticket prose in, testable requirements out."""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.agents.prompts import compose, repository_block, skills_block, system_prompt
from orchestrator.core.models import RequirementsDoc, TrackerIssue
from orchestrator.engine.base import EngineMode, EngineRequest

ROLE = (
    "read one ticket and turn it into a small set of numbered, independently testable "
    "requirements, plus explicit non-goals, assumptions and blocking questions. You do not "
    "design a solution and you do not write code."
)


class RequirementsAgent(Agent):
    name = "requirements"
    responsibility = "Turn the ticket into numbered, testable requirements and open questions."
    output_model = RequirementsDoc

    def run(self, ctx: AgentContext, **kwargs: Any) -> RequirementsDoc:
        log = ctx.log(self.name)
        issue: TrackerIssue = kwargs.get("issue") or ctx.blackboard["issue"]
        library = ctx.blackboard["skills"]

        prompt = compose(
            "## Ticket\n\n" + issue.as_prompt_block(),
            self._comments_block(issue),
            repository_block(ctx.profile),
            skills_block(library, "requirements", platform=ctx.profile.platform),
            "## Task\n\n"
            "Produce the requirements document. Ground every requirement in the ticket text. "
            "Anything you had to infer belongs in `assumptions`; anything that would change "
            "the implementation and cannot be inferred belongs in `open_questions`.",
        )

        log.info("deriving requirements for %s", issue.key)
        doc, response = ctx.engine.generate_structured(
            EngineRequest(
                task="requirements",
                system=system_prompt(ROLE),
                prompt=prompt,
                mode=EngineMode.READ_ONLY,
                cwd=ctx.repo_path,
                metadata={
                    "issue_key": issue.key,
                    "platform": ctx.profile.platform.value,
                    "issue": issue.model_dump(mode="json"),
                },
            ),
            RequirementsDoc,
        )

        # The tracker is authoritative for identity; the engine is not.
        doc.issue_key = issue.key
        doc.platform = ctx.profile.platform

        ctx.audit(
            self.name,
            "requirements.derived",
            count=len(doc.requirements),
            open_questions=len(doc.open_questions),
            engine_seconds=round(response.duration_seconds, 2),
        )
        self.emit(ctx, doc)
        ctx.save_artifact("requirements.md", self.render_markdown(doc))
        ctx.blackboard["requirements"] = doc
        return doc

    @staticmethod
    def _comments_block(issue: TrackerIssue) -> str:
        if not issue.comments:
            return ""
        lines = ["## Ticket discussion (most recent last)", ""]
        for comment in issue.comments[-8:]:
            author = comment.author or "unknown"
            lines.append(f"**{author}:** {comment.body.strip()[:1200]}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_markdown(doc: RequirementsDoc) -> str:
        lines = [
            f"# Requirements — {doc.issue_key}",
            "",
            doc.summary,
            "",
            "## Requirements",
            "",
        ]
        for req in doc.requirements:
            lines.append(f"### {req.id} · {req.title}  _({req.priority})_")
            lines.append("")
            lines.append(req.statement)
            if req.acceptance_criteria:
                lines.append("")
                lines.extend(f"- [ ] {ac}" for ac in req.acceptance_criteria)
            lines.append("")
        for heading, items in (
            ("Non-goals", doc.non_goals),
            ("Assumptions", doc.assumptions),
            ("Open questions", doc.open_questions),
        ):
            if items:
                lines += [f"## {heading}", "", *[f"- {item}" for item in items], ""]
        return "\n".join(lines)
