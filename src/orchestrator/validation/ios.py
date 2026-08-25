"""iOS validation checks.

Ordered cheapest-first: formatting and lint run before anything invokes
``xcodebuild``, so an obviously broken change fails in seconds rather than
minutes. Every check is skipped (not failed) when its tooling is absent, which
keeps the same plan usable on a Linux CI box that has no Xcode.
"""

from __future__ import annotations

from typing import Any

from orchestrator.core.config import Settings
from orchestrator.inspection.profile import RepoProfile
from orchestrator.validation.base import (
    Check,
    CheckContext,
    all_of,
    binary_available,
    file_exists,
)

DEFAULT_DESTINATION = "generic/platform=iOS Simulator"


def _ios_setting(settings: Settings, key: str, default: Any = None) -> Any:
    return settings.validation.ios.get(key, default)


def _container_args(profile: RepoProfile) -> list[str]:
    ios = profile.ios
    if ios is None:
        return []
    if ios.xcworkspace:
        return ["-workspace", ios.xcworkspace[0]]
    if ios.xcodeproj:
        return ["-project", ios.xcodeproj[0]]
    return []


def _scheme(profile: RepoProfile, settings: Settings) -> str | None:
    configured = _ios_setting(settings, "scheme")
    if configured:
        return str(configured)
    if profile.ios and profile.ios.schemes:
        non_test = [s for s in profile.ios.schemes if "test" not in s.lower()]
        return (non_test or profile.ios.schemes)[0]
    return None


def _macos_only(ctx: CheckContext) -> tuple[bool, str]:
    import platform as _platform

    if _platform.system() != "Darwin":
        return False, "xcodebuild is only available on macOS."
    return True, ""


def ios_checks(profile: RepoProfile, settings: Settings) -> list[Check]:
    ios = profile.ios
    checks: list[Check] = []

    if ios and ios.has_swiftformat:
        checks.append(
            Check(
                name="ios:swiftformat",
                description="SwiftFormat reports no formatting drift.",
                command=["swiftformat", "--lint", "."],
                precondition=binary_available("swiftformat"),
                category="format",
                timeout=300,
                required=False,
            )
        )

    if ios and ios.has_swiftlint:
        strict = bool(_ios_setting(settings, "swiftlint_strict", True))
        command = ["swiftlint", "lint", "--quiet"]
        if strict:
            command.append("--strict")
        checks.append(
            Check(
                name="ios:swiftlint",
                description="SwiftLint passes with the repository's own configuration.",
                command=command,
                precondition=binary_available("swiftlint"),
                category="lint",
                timeout=600,
            )
        )

    if ios and ios.swift_package:
        checks.append(
            Check(
                name="ios:spm-build",
                description="`swift build` succeeds for the Swift package.",
                command=["swift", "build"],
                precondition=all_of(binary_available("swift"), file_exists("Package.swift")),
                category="build",
                timeout=1800,
            )
        )

    scheme = _scheme(profile, settings)
    destination = str(_ios_setting(settings, "destination", DEFAULT_DESTINATION))
    container = _container_args(profile)

    if scheme and container:
        checks.append(
            Check(
                name="ios:xcodebuild-build",
                description=f"`xcodebuild build` succeeds for scheme {scheme}.",
                command=[
                    "xcodebuild",
                    *container,
                    "-scheme", scheme,
                    "-destination", destination,
                    "-configuration", str(_ios_setting(settings, "configuration", "Debug")),
                    "build",
                    "CODE_SIGNING_ALLOWED=NO",
                ],
                precondition=all_of(_macos_only, binary_available("xcodebuild")),
                category="build",
                timeout=2400,
            )
        )

        test_scheme = str(_ios_setting(settings, "test_scheme", scheme))
        test_destination = str(
            _ios_setting(settings, "test_destination", "platform=iOS Simulator,name=iPhone 15")
        )
        checks.append(
            Check(
                name="ios:xcodebuild-test",
                description=f"Unit tests pass for scheme {test_scheme}.",
                command=[
                    "xcodebuild",
                    *container,
                    "-scheme", test_scheme,
                    "-destination", test_destination,
                    "test",
                    "CODE_SIGNING_ALLOWED=NO",
                ],
                precondition=all_of(_macos_only, binary_available("xcodebuild")),
                category="test",
                timeout=3600,
            )
        )

    if ios and ios.uses_cocoapods:
        checks.append(
            Check(
                name="ios:podfile-lock-in-sync",
                description="Podfile.lock matches Podfile (no un-run `pod install`).",
                command=["pod", "check", "--verbose"],
                precondition=all_of(binary_available("pod"), file_exists("Podfile.lock")),
                category="deps",
                required=False,
                timeout=600,
            )
        )

    return checks
