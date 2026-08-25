"""Agent behaviour, using the deterministic offline engine."""

from __future__ import annotations

import pytest

from orchestrator.agents.base import AgentContext
from orchestrator.agents.git_pr import DeliveryAgent, slugify
from orchestrator.agents.implementer import ImplementationAgent
from orchestrator.agents.jira_fetcher import JiraFetcherAgent
from orchestrator.agents.planner import PlanAgent
from orchestrator.agents.platform.base import get_platform_agent
from orchestrator.agents.registry import AGENT_REGISTRY, PIPELINE, get_agent
from orchestrator.agents.requirements import RequirementsAgent
from orchestrator.core.errors import ConfigurationError, IssueTrackerError
from orchestrator.core.models import ImplementationPlan, Platform, RequirementsDoc
from orchestrator.core.state import StateStore, WorkflowState
from orchestrator.engine.mock import MockEngine
from orchestrator.inspection.detector import inspect_repository
from orchestrator.integrations.jira.mock import MockJiraClient
from orchestrator.skills.loader import load_skills


def make_ctx(repo, settings) -> AgentContext:
    profile = inspect_repository(repo)
    store = StateStore(repo)
    state = store.create(
        WorkflowState(issue_key="MOB-101", repo_path=str(repo), platform=profile.platform)
    )
    ctx = AgentContext(
        settings=settings,
        store=store,
        state=state,
        profile=profile,
        engine=MockEngine(),
        tracker=MockJiraClient(),
    )
    ctx.blackboard["skills"] = load_skills()
    ctx.blackboard["platform_agent"] = get_platform_agent(profile.platform)
    return ctx


# -- registry ------------------------------------------------------------------ #


def test_every_pipeline_agent_is_registered():
    assert set(PIPELINE) <= set(AGENT_REGISTRY)


def test_only_two_agents_may_write_to_the_repository():
    writers = {
        name for name, spec in AGENT_REGISTRY.items() if spec.instantiate().mutates_repository
    }
    assert writers == {"implementer", "delivery"}


def test_unknown_agent_is_a_clear_error():
    with pytest.raises(ConfigurationError):
        get_agent("nope")


# -- fetcher -------------------------------------------------------------------- #


def test_fetcher_writes_artifacts(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    issue = JiraFetcherAgent().run(ctx)
    assert issue.key == "MOB-101"
    assert ctx.store.read_artifact(ctx.state, "issue.md")
    assert ctx.blackboard["issue"].key == "MOB-101"


def test_fetcher_without_a_tracker_fails_clearly(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    ctx.tracker = None
    with pytest.raises(IssueTrackerError):
        JiraFetcherAgent().run(ctx)


# -- requirements ---------------------------------------------------------------- #


def test_requirements_agent_uses_ticket_criteria(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    JiraFetcherAgent().run(ctx)
    doc = RequirementsAgent().run(ctx)
    assert doc.issue_key == "MOB-101"
    assert doc.platform is Platform.IOS
    assert len(doc.requirements) == 4  # the four acceptance criteria in the fixture
    assert doc.requirements[0].id == "R1"


def test_requirements_markdown_renders_checkboxes():
    doc = RequirementsDoc(
        issue_key="MOB-1",
        platform=Platform.IOS,
        summary="s",
        requirements=[
            {"id": "R1", "title": "t", "statement": "s", "acceptance_criteria": ["does the thing"]}
        ],
        open_questions=["which screen?"],
    )
    markdown = RequirementsAgent.render_markdown(doc)
    assert "- [ ] does the thing" in markdown
    assert "## Open questions" in markdown


# -- planner ---------------------------------------------------------------------- #


def test_planner_covers_every_requirement(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    JiraFetcherAgent().run(ctx)
    doc = RequirementsAgent().run(ctx)
    plan = PlanAgent().run(ctx)

    assert plan.steps
    assert PlanAgent._coverage_gaps(doc, plan) == set()
    assert ctx.store.read_artifact(ctx.state, "plan.md")


def test_planner_flags_uncovered_requirements():
    """A requirement no step satisfies must be surfaced as a risk, not hidden."""
    doc = RequirementsDoc(
        issue_key="MOB-1",
        platform=Platform.IOS,
        summary="s",
        requirements=[
            {"id": "R1", "title": "a", "statement": "a"},
            {"id": "R2", "title": "b", "statement": "b"},
        ],
    )
    plan = ImplementationPlan(
        issue_key="MOB-1",
        platform=Platform.IOS,
        summary="s",
        steps=[{"id": "S1", "title": "only covers R1", "intent": "", "satisfies": ["R1"]}],
    )
    assert PlanAgent._coverage_gaps(doc, plan) == {"R2"}


def test_plan_steps_are_topologically_ordered():
    plan = ImplementationPlan(
        issue_key="MOB-1",
        platform=Platform.IOS,
        summary="s",
        steps=[
            {"id": "S2", "title": "second", "intent": "", "depends_on": ["S1"]},
            {"id": "S1", "title": "first", "intent": ""},
        ],
    )
    assert [step.id for step in plan.ordered_steps()] == ["S1", "S2"]


def test_cyclic_dependencies_do_not_hang():
    plan = ImplementationPlan(
        issue_key="MOB-1",
        platform=Platform.IOS,
        summary="s",
        steps=[
            {"id": "S1", "title": "a", "intent": "", "depends_on": ["S2"]},
            {"id": "S2", "title": "b", "intent": "", "depends_on": ["S1"]},
        ],
    )
    assert len(plan.ordered_steps()) == 2


# -- implementer -------------------------------------------------------------------- #


def test_implementer_reconciles_claims_against_git(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    JiraFetcherAgent().run(ctx)
    RequirementsAgent().run(ctx)
    PlanAgent().run(ctx)

    result = ImplementationAgent().run(ctx)
    assert [fc.path for fc in result.file_changes] == ["ORCHESTRATOR_MOCK_CHANGES.md"]
    assert "ORCHESTRATOR_MOCK_CHANGES.md" in result.touched_paths
    assert (ios_repo / "ORCHESTRATOR_MOCK_CHANGES.md").exists()


def test_implementer_refuses_a_non_editing_engine(ios_repo, settings):
    from orchestrator.core.errors import EngineError
    from orchestrator.engine.base import Engine, EngineResponse

    class ReadOnlyEngine(Engine):
        name = "read-only"
        supports_editing = False

        def complete(self, request):  # pragma: no cover - never reached
            return EngineResponse(text="{}")

    ctx = make_ctx(ios_repo, settings)
    JiraFetcherAgent().run(ctx)
    RequirementsAgent().run(ctx)
    PlanAgent().run(ctx)
    ctx.engine = ReadOnlyEngine()
    with pytest.raises(EngineError):
        ImplementationAgent().run(ctx)


# -- delivery ---------------------------------------------------------------------- #


def test_slugify_is_branch_safe():
    assert slugify("Add pull-to-refresh to Order History!") == "add-pull-to-refresh-to-order-history"
    assert slugify("") == "change"
    assert len(slugify("x" * 200)) <= 40


def test_branch_name_uses_issue_type(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    issue = JiraFetcherAgent().run(ctx)
    name = DeliveryAgent().branch_name(ctx, issue)
    assert name.startswith("feature/mob-101-")

    bug = issue.model_copy(update={"issue_type": "Bug", "key": "MOB-102"})
    assert DeliveryAgent().branch_name(ctx, bug).startswith("fix/mob-102-")


def test_delivery_refuses_protected_branch_without_a_new_branch(ios_repo, settings):
    from orchestrator.core.errors import RepositoryError
    from orchestrator.core.models import ReviewSummary

    ctx = make_ctx(ios_repo, settings)
    issue = JiraFetcherAgent().run(ctx)
    ctx.state.branch = "main"  # pretend the run targets the protected branch itself
    ctx.blackboard["review"] = ReviewSummary(issue_key="MOB-101", headline="h", commit_message="c")
    with pytest.raises(RepositoryError):
        DeliveryAgent().run(ctx, issue=issue, dry_run=False)


# -- platform agents ------------------------------------------------------------------ #


def test_ios_preflight_warns_about_missing_pods(ios_repo, settings):
    ctx = make_ctx(ios_repo, settings)
    report = get_platform_agent(Platform.IOS).preflight(ctx)
    assert report.ok
    assert any("pod install" in w for w in report.warnings)


def test_android_preflight_blocks_without_a_wrapper(android_repo, settings):
    (android_repo / "gradlew").unlink()
    ctx = make_ctx(android_repo, settings)
    report = get_platform_agent(Platform.ANDROID).preflight(ctx)
    assert not report.ok
    assert any("Gradle wrapper" in b for b in report.blockers)
