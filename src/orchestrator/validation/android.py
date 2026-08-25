"""Android validation checks.

Uses the repository's own Gradle wrapper so the orchestrator never imposes a
Gradle version, and runs with ``--no-daemon`` so a CI run does not leave a
daemon behind. As with iOS, missing tooling means *skipped*, not *failed*.
"""

from __future__ import annotations

from typing import Any

from orchestrator.core.config import Settings
from orchestrator.inspection.profile import RepoProfile
from orchestrator.validation.base import Check, CheckContext, file_exists

GRADLE_BASE = ["--no-daemon", "--console=plain", "--stacktrace"]


def _android_setting(settings: Settings, key: str, default: Any = None) -> Any:
    return settings.validation.android.get(key, default)


def _gradlew(ctx: CheckContext) -> tuple[bool, str]:
    wrapper = ctx.profile.path / "gradlew"
    if not wrapper.exists():
        return False, "No Gradle wrapper (./gradlew) in this repository."
    return True, ""


def _gradle_command(task: str, extra: list[str] | None = None) -> list[str]:
    return ["./gradlew", task, *GRADLE_BASE, *(extra or [])]


def android_checks(profile: RepoProfile, settings: Settings) -> list[Check]:
    android = profile.android
    checks: list[Check] = []

    if android and android.has_spotless:
        checks.append(
            Check(
                name="android:spotless",
                description="Spotless reports no formatting drift.",
                command=_gradle_command("spotlessCheck"),
                precondition=_gradlew,
                category="format",
                required=False,
                timeout=900,
            )
        )

    if android and android.has_ktlint:
        checks.append(
            Check(
                name="android:ktlint",
                description="ktlint passes.",
                command=_gradle_command("ktlintCheck"),
                precondition=_gradlew,
                category="lint",
                timeout=900,
            )
        )

    if android and android.has_detekt:
        checks.append(
            Check(
                name="android:detekt",
                description="detekt reports no new issues.",
                command=_gradle_command("detekt"),
                precondition=_gradlew,
                category="lint",
                timeout=900,
            )
        )

    assemble_task = str(_android_setting(settings, "assemble_task", "assembleDebug"))
    checks.append(
        Check(
            name="android:assemble",
            description=f"`./gradlew {assemble_task}` succeeds.",
            command=_gradle_command(assemble_task),
            precondition=_gradlew,
            category="build",
            timeout=3600,
        )
    )

    test_task = str(_android_setting(settings, "test_task", "testDebugUnitTest"))
    checks.append(
        Check(
            name="android:unit-tests",
            description=f"`./gradlew {test_task}` passes.",
            command=_gradle_command(test_task),
            precondition=_gradlew,
            category="test",
            timeout=3600,
        )
    )

    if _android_setting(settings, "run_android_lint", True):
        checks.append(
            Check(
                name="android:lint",
                description="`./gradlew lintDebug` reports no fatal issues.",
                command=_gradle_command(str(_android_setting(settings, "lint_task", "lintDebug"))),
                precondition=_gradlew,
                category="lint",
                required=False,
                timeout=1800,
            )
        )

    checks.append(
        Check(
            name="android:lockfile-untouched",
            description="gradle/libs.versions.toml parses (catalog not corrupted).",
            command=["python3", "-c", "import sys,tomllib;tomllib.load(open(sys.argv[1],'rb'))", "gradle/libs.versions.toml"],
            precondition=file_exists("gradle/libs.versions.toml"),
            category="deps",
            required=False,
            timeout=60,
        )
    )

    return checks
