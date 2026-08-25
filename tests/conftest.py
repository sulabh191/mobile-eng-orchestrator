"""Shared fixtures.

Every test runs against a throwaway config directory and a throwaway git
repository, so nothing here can read or write a developer's real configuration.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.core.config import Settings, load_settings
from orchestrator.core.models import Platform


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point global config/state at the test's tmp dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORC_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ORC_STATE_DIR", str(tmp_path / "state"))
    for key in list(os.environ):
        if key.startswith("ORC_") and key not in {"ORC_CONFIG_DIR", "ORC_STATE_DIR"}:
            monkeypatch.delenv(key, raising=False)
    return config_dir


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.email", "orchestrator@example.com")
    _git(path, "config", "user.name", "Orchestrator Test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("# test repo\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial commit")
    return path


def make_ios_repo(root: Path) -> Path:
    repo = root / "IosApp"
    init_git_repo(repo)
    project = repo / "IosApp.xcodeproj"
    (project / "xcshareddata" / "xcschemes").mkdir(parents=True)
    (project / "project.pbxproj").write_text("// pbxproj\n", encoding="utf-8")
    (project / "xcshareddata" / "xcschemes" / "IosApp.xcscheme").write_text(
        "<Scheme/>", encoding="utf-8"
    )
    schemes = project / "xcshareddata" / "xcschemes"
    (schemes / "IosAppTests.xcscheme").write_text("<Scheme/>", encoding="utf-8")
    (repo / ".swiftlint.yml").write_text("disabled_rules: []\n", encoding="utf-8")
    sources = repo / "Sources"
    sources.mkdir()
    (sources / "OrderHistoryView.swift").write_text("import SwiftUI\n", encoding="utf-8")
    (repo / "Podfile").write_text("platform :ios, '16.0'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "scaffold ios project")
    return repo


def make_android_repo(root: Path) -> Path:
    repo = root / "AndroidApp"
    init_git_repo(repo)
    (repo / "settings.gradle.kts").write_text(
        'include(":app")\ninclude(":core:network")\n', encoding="utf-8"
    )
    (repo / "gradlew").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo / "gradlew").chmod(0o755)
    app = repo / "app"
    (app / "src" / "main").mkdir(parents=True)
    (app / "src" / "main" / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    (app / "build.gradle.kts").write_text(
        'plugins { id("com.android.application") }\n'
        'android { defaultConfig { applicationId = "com.example.app" } }\n'
        'dependencies { implementation("androidx.compose.ui:ui") }\n',
        encoding="utf-8",
    )
    (repo / "build.gradle.kts").write_text(
        'plugins { id("io.gitlab.arturbosch.detekt") }\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "scaffold android project")
    return repo


@pytest.fixture
def ios_repo(tmp_path) -> Path:
    return make_ios_repo(tmp_path)


@pytest.fixture
def android_repo(tmp_path) -> Path:
    return make_android_repo(tmp_path)


@pytest.fixture
def generic_repo(tmp_path) -> Path:
    return init_git_repo(tmp_path / "PlainRepo")


@pytest.fixture
def settings(tmp_path) -> Settings:
    loaded = load_settings(None)
    loaded.engine.backend = "mock"
    loaded.git.provider = "none"
    loaded.behaviour.interactive = False
    return loaded


__all__ = ["Platform", "init_git_repo", "make_android_repo", "make_ios_repo"]
