"""Deterministic validation.

Validation is intentionally *not* delegated to a model. A build either compiles
or it does not; a lint rule either fires or it does not. Agents may propose
changes, but only these checks decide whether a run may proceed to delivery.
"""

from orchestrator.validation.base import Check, CheckPlan, ValidationRunner, build_check_plan

__all__ = ["Check", "CheckPlan", "ValidationRunner", "build_check_plan"]
