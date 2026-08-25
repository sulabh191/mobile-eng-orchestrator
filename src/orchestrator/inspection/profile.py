"""The :class:`RepoProfile` — everything downstream agents need to know about a
target repository, computed once and cached in the run state.

The profile is deliberately *descriptive*, not prescriptive: it reports what the
repository contains and which commands are available. Agents decide what to do
with that.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from orchestrator.core.models import Platform, StrictModel


class DetectionSignal(StrictModel):
    """One piece of evidence for a platform verdict."""

    platform: Platform
    marker: str
    weight: int = 1
    path: str | None = None


class GitInfo(StrictModel):
    is_repo: bool = False
    current_branch: str | None = None
    default_branch: str | None = None
    remote_url: str | None = None
    remote_name: str | None = None
    is_dirty: bool = False
    ahead: int = 0
    behind: int = 0
    #: Redacted form of the remote (credentials in the URL are stripped).
    remote_display: str | None = None


class IOSProfile(StrictModel):
    xcodeproj: list[str] = Field(default_factory=list)
    xcworkspace: list[str] = Field(default_factory=list)
    schemes: list[str] = Field(default_factory=list)
    swift_package: bool = False
    uses_cocoapods: bool = False
    uses_carthage: bool = False
    uses_tuist: bool = False
    uses_fastlane: bool = False
    has_swiftlint: bool = False
    has_swiftformat: bool = False
    test_targets: list[str] = Field(default_factory=list)
    min_ios_version: str | None = None

    @property
    def primary_container(self) -> str | None:
        if self.xcworkspace:
            return self.xcworkspace[0]
        if self.xcodeproj:
            return self.xcodeproj[0]
        return None


class AndroidProfile(StrictModel):
    gradle_files: list[str] = Field(default_factory=list)
    uses_kotlin_dsl: bool = False
    has_wrapper: bool = False
    modules: list[str] = Field(default_factory=list)
    application_id: str | None = None
    has_ktlint: bool = False
    has_detekt: bool = False
    has_spotless: bool = False
    uses_compose: bool = False
    agp_version: str | None = None


class RepoProfile(StrictModel):
    """What the orchestrator knows about the repository it is pointed at."""

    root: str
    platform: Platform = Platform.GENERIC
    confidence: float = 0.0
    signals: list[DetectionSignal] = Field(default_factory=list)
    git: GitInfo = Field(default_factory=GitInfo)
    ios: IOSProfile | None = None
    android: AndroidProfile | None = None
    languages: list[str] = Field(default_factory=list)
    source_dirs: list[str] = Field(default_factory=list)
    test_dirs: list[str] = Field(default_factory=list)
    has_ci: bool = False
    #: Repository conventions the implementer must respect, harvested from
    #: CONTRIBUTING.md / AGENTS.md / CLAUDE.md when present.
    convention_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def path(self) -> Path:
        return Path(self.root)

    def summary_line(self) -> str:
        bits = [f"{self.platform.display} ({self.confidence:.0%} confidence)"]
        if self.platform is Platform.IOS and self.ios and self.ios.primary_container:
            bits.append(Path(self.ios.primary_container).name)
        if self.platform is Platform.ANDROID and self.android:
            bits.append(f"{len(self.android.modules)} gradle module(s)")
        if self.git.current_branch:
            bits.append(f"on {self.git.current_branch}")
        return " · ".join(bits)

    def as_prompt_block(self) -> str:
        """Compact, stable description handed to the reasoning engine."""
        lines = [
            f"Repository: {self.root}",
            f"Platform: {self.platform.display}",
            f"Languages: {', '.join(self.languages) or 'unknown'}",
        ]
        if self.source_dirs:
            lines.append(f"Source dirs: {', '.join(self.source_dirs[:8])}")
        if self.test_dirs:
            lines.append(f"Test dirs: {', '.join(self.test_dirs[:8])}")
        if self.ios:
            lines.append(
                "iOS: "
                + ", ".join(
                    filter(
                        None,
                        [
                            f"container={Path(c).name}" if (c := self.ios.primary_container) else None,
                            "SPM" if self.ios.swift_package else None,
                            "CocoaPods" if self.ios.uses_cocoapods else None,
                            "Tuist" if self.ios.uses_tuist else None,
                            "SwiftLint" if self.ios.has_swiftlint else None,
                            f"schemes={','.join(self.ios.schemes[:5])}" if self.ios.schemes else None,
                        ],
                    )
                )
            )
        if self.android:
            lines.append(
                "Android: "
                + ", ".join(
                    filter(
                        None,
                        [
                            "Kotlin DSL" if self.android.uses_kotlin_dsl else "Groovy DSL",
                            "gradlew" if self.android.has_wrapper else "no wrapper",
                            "Compose" if self.android.uses_compose else None,
                            "ktlint" if self.android.has_ktlint else None,
                            "detekt" if self.android.has_detekt else None,
                            f"modules={','.join(self.android.modules[:6])}"
                            if self.android.modules
                            else None,
                        ],
                    )
                )
            )
        if self.convention_files:
            lines.append(f"Convention docs: {', '.join(self.convention_files)}")
        if self.git.is_repo:
            lines.append(
                f"Git: branch={self.git.current_branch} base={self.git.default_branch} "
                f"dirty={self.git.is_dirty}"
            )
        return "\n".join(lines)
