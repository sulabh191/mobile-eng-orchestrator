"""Repository inspection and platform detection."""

from __future__ import annotations

from orchestrator.core.models import Platform
from orchestrator.inspection.detector import detect_platform, inspect_repository


def test_detects_ios(ios_repo):
    profile = inspect_repository(ios_repo)
    assert profile.platform is Platform.IOS
    assert profile.confidence > 0.5
    assert profile.ios is not None
    assert profile.ios.uses_cocoapods
    assert profile.ios.has_swiftlint
    assert "IosApp" in profile.ios.schemes
    assert "Swift" in profile.languages


def test_detects_android(android_repo):
    profile = inspect_repository(android_repo)
    assert profile.platform is Platform.ANDROID
    assert profile.android is not None
    assert profile.android.uses_kotlin_dsl
    assert profile.android.has_wrapper
    assert profile.android.application_id == "com.example.app"
    assert "app" in profile.android.modules
    assert profile.android.uses_compose
    assert profile.android.has_detekt


def test_generic_repository_has_no_platform(generic_repo):
    profile = inspect_repository(generic_repo)
    assert profile.platform is Platform.GENERIC
    assert profile.confidence == 0.0
    assert any("No iOS or Android markers" in note for note in profile.notes)


def test_override_pins_platform(generic_repo):
    profile = inspect_repository(generic_repo, platform_override=Platform.ANDROID)
    assert profile.platform is Platform.ANDROID
    assert profile.confidence == 1.0


def test_hybrid_repository_reports_low_confidence(tmp_path, ios_repo):
    """A repo with both toolchains should hedge rather than guess confidently."""
    (ios_repo / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
    (ios_repo / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
    (ios_repo / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    platform, confidence, signals = detect_platform(ios_repo)
    assert platform in (Platform.IOS, Platform.ANDROID)
    assert confidence < 0.6
    assert {s.platform for s in signals} == {Platform.IOS, Platform.ANDROID}


def test_git_info_is_populated(ios_repo):
    profile = inspect_repository(ios_repo)
    assert profile.git.is_repo
    assert profile.git.current_branch == "main"


def test_build_dirs_are_not_scanned(ios_repo):
    """Pruned directories must not contribute detection signals."""
    noise = ios_repo / "Pods" / "Fake.xcodeproj"
    noise.mkdir(parents=True)
    profile = inspect_repository(ios_repo)
    assert all("Pods/" not in (s.path or "") for s in profile.signals)


def test_prompt_block_is_stable_text(android_repo):
    block = inspect_repository(android_repo).as_prompt_block()
    assert "Platform: Android" in block
    assert "Kotlin DSL" in block
