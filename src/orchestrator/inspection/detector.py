"""Platform detection and repository profiling.

Detection is evidence-based rather than first-match: every marker contributes a
weighted signal, the highest-scoring platform wins, and the margin between the
top two becomes a confidence score. Hybrid repositories (a KMP project with an
``iosApp`` folder, say) therefore report a low confidence instead of silently
picking one — the CLI can then ask, or the developer can pin the platform with
``--platform``.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

from orchestrator.core.errors import PlatformDetectionError
from orchestrator.core.models import Platform
from orchestrator.inspection.profile import (
    AndroidProfile,
    DetectionSignal,
    GitInfo,
    IOSProfile,
    RepoProfile,
)
from orchestrator.integrations.git.repo import GitRepo, sanitize_remote

#: Directories that are never worth walking into.
PRUNED_DIRS = {
    ".git",
    ".orchestrator",
    "node_modules",
    "build",
    ".build",
    "DerivedData",
    "Pods",
    "Carthage",
    ".gradle",
    ".idea",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "out",
    ".dart_tool",
}

MAX_SCAN_DEPTH = 4

#: marker (glob, relative) -> (platform, weight)
MARKERS: list[tuple[str, Platform, int]] = [
    ("*.xcodeproj", Platform.IOS, 5),
    ("*.xcworkspace", Platform.IOS, 5),
    ("Podfile", Platform.IOS, 3),
    ("Package.swift", Platform.IOS, 3),
    ("Project.swift", Platform.IOS, 3),
    ("Cartfile", Platform.IOS, 2),
    ("fastlane/Appfile", Platform.IOS, 1),
    (".swiftlint.yml", Platform.IOS, 1),
    ("*.xcconfig", Platform.IOS, 1),
    ("settings.gradle", Platform.ANDROID, 5),
    ("settings.gradle.kts", Platform.ANDROID, 5),
    ("build.gradle", Platform.ANDROID, 4),
    ("build.gradle.kts", Platform.ANDROID, 4),
    ("gradlew", Platform.ANDROID, 3),
    ("gradle.properties", Platform.ANDROID, 2),
    ("app/src/main/AndroidManifest.xml", Platform.ANDROID, 5),
    ("local.properties", Platform.ANDROID, 1),
]

CONVENTION_FILES = (
    "CONTRIBUTING.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/engineering-guidelines.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)

CI_MARKERS = (".github/workflows", ".gitlab-ci.yml", "Jenkinsfile", "bitrise.yml", ".circleci")

_EXTENSION_LANGUAGES = {
    ".swift": "Swift",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".java": "Java",
    ".dart": "Dart",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".py": "Python",
    ".rb": "Ruby",
    ".go": "Go",
}


def _iter_paths(root: Path, max_depth: int = MAX_SCAN_DEPTH):
    """Depth-limited walk that skips build output and dependency caches."""
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.name in PRUNED_DIRS:
                continue
            yield entry, depth
            if entry.is_dir() and not entry.name.endswith((".xcodeproj", ".xcworkspace")):
                if depth < max_depth:
                    stack.append((entry, depth + 1))


def _collect_signals(root: Path) -> list[DetectionSignal]:
    signals: list[DetectionSignal] = []
    seen: set[tuple[str, str]] = set()
    for entry, depth in _iter_paths(root):
        rel = entry.relative_to(root).as_posix()
        for pattern, platform, weight in MARKERS:
            if "/" in pattern:
                matches = rel == pattern or rel.endswith("/" + pattern)
            else:
                matches = entry.match(pattern)
            if matches and (pattern, rel) not in seen:
                seen.add((pattern, rel))
                # Markers deeper in the tree are weaker evidence.
                signals.append(
                    DetectionSignal(
                        platform=platform,
                        marker=pattern,
                        weight=max(1, weight - depth),
                        path=rel,
                    )
                )
    return signals


def _score(signals: list[DetectionSignal]) -> dict[Platform, int]:
    scores: dict[Platform, int] = {Platform.IOS: 0, Platform.ANDROID: 0}
    for signal in signals:
        scores[signal.platform] = scores.get(signal.platform, 0) + signal.weight
    return scores


def detect_platform(root: Path) -> tuple[Platform, float, list[DetectionSignal]]:
    """Return ``(platform, confidence, signals)`` for ``root``."""
    signals = _collect_signals(root)
    scores = _score(signals)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top, top_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0

    if top_score == 0:
        return Platform.GENERIC, 0.0, signals

    confidence = (top_score - runner_up_score) / top_score
    # A handful of weak markers should not read as certainty.
    confidence *= min(1.0, top_score / 8)
    return top, round(min(confidence, 1.0), 2), signals


# --------------------------------------------------------------------------- #
# Platform-specific profiling
# --------------------------------------------------------------------------- #


def _profile_ios(root: Path, signals: list[DetectionSignal]) -> IOSProfile:
    profile = IOSProfile()
    for signal in signals:
        if signal.path is None:
            continue
        if signal.marker == "*.xcodeproj":
            profile.xcodeproj.append(signal.path)
        elif signal.marker == "*.xcworkspace":
            profile.xcworkspace.append(signal.path)
    profile.swift_package = (root / "Package.swift").exists()
    profile.uses_cocoapods = (root / "Podfile").exists()
    profile.uses_carthage = (root / "Cartfile").exists()
    profile.uses_tuist = (root / "Project.swift").exists()
    profile.uses_fastlane = (root / "fastlane").is_dir()
    profile.has_swiftlint = any((root / n).exists() for n in (".swiftlint.yml", ".swiftlint.yaml"))
    profile.has_swiftformat = (root / ".swiftformat").exists()
    profile.schemes = _xcode_schemes(root, profile)
    profile.test_targets = [s for s in profile.schemes if "test" in s.lower()]
    profile.min_ios_version = _min_ios_version(root)
    return profile


def _xcode_schemes(root: Path, profile: IOSProfile) -> list[str]:
    """Read shared schemes straight off disk — no xcodebuild call required."""
    schemes: list[str] = []
    containers = [*profile.xcworkspace, *profile.xcodeproj]
    for container in containers:
        shared = root / container / "xcshareddata" / "xcschemes"
        if shared.is_dir():
            schemes.extend(sorted(p.stem for p in shared.glob("*.xcscheme")))
    if not schemes and profile.swift_package:
        match = re.search(
            r'name:\s*"([^"]+)"', (root / "Package.swift").read_text(errors="ignore")
        )
        if match:
            schemes.append(match.group(1))
    return list(dict.fromkeys(schemes))


def _min_ios_version(root: Path) -> str | None:
    for plist in list(root.glob("**/Info.plist"))[:5]:
        try:
            data = plistlib.loads(plist.read_bytes())
        except Exception:
            continue
        value = data.get("MinimumOSVersion")
        if value:
            return str(value)
    for config in list(root.glob("**/*.xcconfig"))[:10]:
        match = re.search(
            r"IPHONEOS_DEPLOYMENT_TARGET\s*=\s*([\d.]+)", config.read_text(errors="ignore")
        )
        if match:
            return match.group(1)
    return None


def _profile_android(root: Path, signals: list[DetectionSignal]) -> AndroidProfile:
    profile = AndroidProfile()
    profile.gradle_files = sorted(
        {s.path for s in signals if s.path and s.marker.startswith("build.gradle")}
    )
    profile.uses_kotlin_dsl = any(p.endswith(".kts") for p in profile.gradle_files) or (
        root / "settings.gradle.kts"
    ).exists()
    profile.has_wrapper = (root / "gradlew").exists()

    settings_file = next(
        (root / name for name in ("settings.gradle.kts", "settings.gradle") if (root / name).exists()),
        None,
    )
    if settings_file:
        text = settings_file.read_text(errors="ignore")
        profile.modules = sorted(set(re.findall(r'include\s*\(?\s*["\']:?([\w:\-]+)["\']', text)))

    build_texts = []
    for rel in profile.gradle_files[:12]:
        try:
            build_texts.append((root / rel).read_text(errors="ignore"))
        except OSError:
            continue
    joined = "\n".join(build_texts)
    if match := re.search(r'applicationId\s*=?\s*["\']([\w.]+)["\']', joined):
        profile.application_id = match.group(1)
    if match := re.search(r'com\.android\.tools\.build:gradle:([\d.]+)', joined):
        profile.agp_version = match.group(1)
    profile.uses_compose = "compose" in joined.lower()
    profile.has_ktlint = "ktlint" in joined.lower() or (root / ".ktlint.yml").exists()
    profile.has_detekt = "detekt" in joined.lower() or (root / "detekt.yml").exists()
    profile.has_spotless = "spotless" in joined.lower()
    return profile


def _languages(root: Path) -> list[str]:
    counts: dict[str, int] = {}
    for entry, _ in _iter_paths(root, max_depth=3):
        if entry.is_file():
            language = _EXTENSION_LANGUAGES.get(entry.suffix)
            if language:
                counts[language] = counts.get(language, 0) + 1
    return [lang for lang, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)][:6]


def _source_and_test_dirs(root: Path) -> tuple[list[str], list[str]]:
    sources: list[str] = []
    tests: list[str] = []
    for entry, depth in _iter_paths(root, max_depth=2):
        if not entry.is_dir() or depth > 2:
            continue
        rel = entry.relative_to(root).as_posix()
        lowered = entry.name.lower()
        if lowered in {"test", "tests", "androidtest", "unittests", "uitests"} or lowered.endswith(
            ("tests", "test")
        ):
            tests.append(rel)
        elif lowered in {"src", "sources", "app", "lib", "core", "features", "modules"}:
            sources.append(rel)
    return sorted(set(sources))[:12], sorted(set(tests))[:12]


def _git_info(root: Path) -> GitInfo:
    repo = GitRepo(root)
    if not GitRepo.available() or not repo.is_repo:
        return GitInfo(is_repo=False)
    remote_name = repo.remotes()[0] if repo.remotes() else None
    remote_url = repo.remote_url(remote_name) if remote_name else None
    ahead, behind = repo.ahead_behind()
    return GitInfo(
        is_repo=True,
        current_branch=repo.current_branch(),
        default_branch=repo.default_branch(remote_name or "origin"),
        remote_url=remote_url,
        remote_name=remote_name,
        remote_display=sanitize_remote(remote_url),
        is_dirty=repo.is_dirty(),
        ahead=ahead,
        behind=behind,
    )


def inspect_repository(
    path: Path | str, *, platform_override: Platform | None = None
) -> RepoProfile:
    """Build a complete :class:`RepoProfile` for ``path``."""
    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise PlatformDetectionError(f"{root} is not an existing directory.")

    detected, confidence, signals = detect_platform(root)
    platform = platform_override or detected
    if platform_override is not None:
        confidence = 1.0

    profile = RepoProfile(
        root=str(root),
        platform=platform,
        confidence=confidence,
        signals=signals,
        git=_git_info(root),
        languages=_languages(root),
        has_ci=any((root / marker).exists() for marker in CI_MARKERS),
        convention_files=[name for name in CONVENTION_FILES if (root / name).exists()],
    )
    profile.source_dirs, profile.test_dirs = _source_and_test_dirs(root)

    if platform is Platform.IOS:
        profile.ios = _profile_ios(root, signals)
    elif platform is Platform.ANDROID:
        profile.android = _profile_android(root, signals)

    if platform is Platform.GENERIC:
        profile.notes.append(
            "No iOS or Android markers found; only generic checks will be available."
        )
    elif confidence < 0.4:
        profile.notes.append(
            f"Low-confidence detection ({confidence:.0%}). "
            "Pin the platform with --platform if this is wrong."
        )
    if profile.git.is_repo and profile.git.is_dirty:
        profile.notes.append("Working tree has uncommitted changes before the run started.")
    return profile
