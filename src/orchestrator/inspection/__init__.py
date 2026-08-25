"""Target-repository inspection: platform detection and capability profiling."""

from orchestrator.inspection.detector import detect_platform, inspect_repository
from orchestrator.inspection.profile import RepoProfile

__all__ = ["RepoProfile", "detect_platform", "inspect_repository"]
