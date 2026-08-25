"""Validation agent — deterministic gatekeeper.

No model is involved. The agent assembles the platform's check plan, runs it,
and reports. A run cannot reach delivery unless every required check passed.
"""

from __future__ import annotations

from typing import Any

from orchestrator.agents.base import Agent, AgentContext
from orchestrator.core.models import CheckStatus, ImplementationResult, ValidationReport
from orchestrator.validation.base import Check, ValidationRunner, build_check_plan


class ValidationAgent(Agent):
    name = "validator"
    responsibility = "Run the platform's build, lint, test and guard checks and report results."
    output_model = ValidationReport

    def run(self, ctx: AgentContext, **kwargs: Any) -> ValidationReport:
        log = ctx.log(self.name)
        implementation: ImplementationResult | None = kwargs.get("implementation") or (
            ctx.blackboard.get("implementation")
        )
        attempt = int(kwargs.get("attempt", 1))

        plan = build_check_plan(ctx.profile, ctx.settings)
        log.info(
            "running %d check(s) for %s: %s",
            len(plan.checks),
            ctx.profile.platform.display,
            ", ".join(plan.names()),
        )

        def _announce(check: Check) -> None:
            log.info("  → %s", check.name)

        runner = ValidationRunner(ctx.profile, ctx.settings)
        report = runner.run(
            plan,
            issue_key=ctx.issue_key,
            implementation=implementation,
            attempt=attempt,
            on_check=_announce,
        )

        ctx.audit(
            self.name,
            "validation.completed",
            attempt=attempt,
            passed=report.passed,
            checks={c.name: c.status.value for c in report.checks},
        )
        self.emit(ctx, report)
        ctx.save_artifact(f"validation-attempt-{attempt}.md", self.render_markdown(report))
        ctx.blackboard["validation"] = report
        return report

    @staticmethod
    def render_markdown(report: ValidationReport) -> str:
        icon = {
            CheckStatus.PASSED: "✅",
            CheckStatus.FAILED: "❌",
            CheckStatus.SKIPPED: "⏭️",
            CheckStatus.ERRORED: "💥",
        }
        lines = [
            f"# Validation — {report.issue_key} (attempt {report.attempt})",
            "",
            f"**Result:** {'PASSED' if report.passed else 'FAILED'}",
            "",
            "| Check | Status | Exit | Duration |",
            "| --- | --- | --- | --- |",
        ]
        for check in report.checks:
            lines.append(
                f"| `{check.name}` | {icon[check.status]} {check.status.value} | "
                f"{check.exit_code if check.exit_code is not None else '–'} | "
                f"{check.duration_seconds:.1f}s |"
            )
        for check in report.checks:
            if check.status is CheckStatus.SKIPPED and check.skip_reason:
                lines += ["", f"- `{check.name}` skipped: {check.skip_reason}"]
        if report.failures:
            lines += ["", "## Failures", ""]
            for check in report.failures:
                lines += [
                    f"### {check.name}",
                    "",
                    f"`{check.command}`",
                    "",
                    "```",
                    check.output_tail[-4000:],
                    "```",
                    "",
                ]
        return "\n".join(lines)
