"""Android specialist."""

from __future__ import annotations

import os

from orchestrator.agents.base import AgentContext
from orchestrator.agents.platform.base import PlatformAgent, PreflightReport
from orchestrator.core.models import Platform
from orchestrator.core.process import which


class AndroidPlatformAgent(PlatformAgent):
    name = "platform-android"
    responsibility = "Contribute Android/Kotlin conventions and pre-flight checks."
    platform = Platform.ANDROID
    skill_tags = ("android",)

    def preflight(self, ctx: AgentContext) -> PreflightReport:
        report = PreflightReport(platform=Platform.ANDROID)
        profile = ctx.profile.android
        root = ctx.repo_path

        if profile is None:
            report.blockers.append("Repository was classified as Android but has no Android profile.")
            return report

        wrapper = root / "gradlew"
        if not wrapper.exists():
            report.blockers.append(
                "No Gradle wrapper (./gradlew). The orchestrator will not invoke a system "
                "Gradle, because the version would not match the project."
            )
        elif not os.access(wrapper, os.X_OK):
            report.warnings.append("./gradlew is not executable — run `chmod +x gradlew`.")

        if not (root / "local.properties").exists() and not os.environ.get("ANDROID_HOME"):
            report.warnings.append(
                "Neither local.properties nor ANDROID_HOME is set; Gradle may fail to locate "
                "the Android SDK."
            )

        if not which("java"):
            report.warnings.append("java not found on PATH; Gradle checks will fail.")

        if not profile.modules:
            report.warnings.append("No modules found in settings.gradle — is this the project root?")

        report.facts = {
            "modules": ", ".join(profile.modules[:6]) or "(none)",
            "dsl": "Kotlin" if profile.uses_kotlin_dsl else "Groovy",
            "compose": "yes" if profile.uses_compose else "no",
            "application_id": profile.application_id or "unknown",
        }
        return report

    def guidance(self, ctx: AgentContext) -> str:
        profile = ctx.profile.android
        if profile is None:
            return ""
        lines = ["Platform specifics for this repository:"]
        if (ctx.repo_path / "gradle" / "libs.versions.toml").exists():
            lines.append("- Dependencies are managed by a version catalog; add entries there, not inline.")
        if profile.uses_compose:
            lines.append("- UI is Jetpack Compose; follow the existing state-hoisting pattern.")
        if profile.has_detekt or profile.has_ktlint:
            lines.append("- Static analysis runs in validation; new violations will fail the run.")
        return "\n".join(lines) if len(lines) > 1 else ""
