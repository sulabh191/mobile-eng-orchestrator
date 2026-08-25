"""Human approval gates.

A gate is a named, auditable checkpoint. The orchestrator can only pass one of
three ways:

* an approval was already recorded for this run (``orc approve``),
* the developer approves interactively at the prompt,
* auto-approval is explicitly enabled *and* acknowledged (see config).

Otherwise the run stops cleanly with :class:`ApprovalRequired` and can be
resumed later. Destructive gates (commit/push/PR) never auto-pass in a
non-interactive session unless auto-approval is on.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.panel import Panel
from rich.prompt import Prompt

from orchestrator.core.errors import ApprovalRejected, ApprovalRequired
from orchestrator.core.logging import console
from orchestrator.core.models import AuditEvent, Decision
from orchestrator.core.state import StateStore, WorkflowState


class Gate:
    """Canonical gate names — used in state, audit log and CLI."""

    REQUIREMENTS = "requirements"
    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    DELIVERY = "delivery"

    ALL = (REQUIREMENTS, PLAN, IMPLEMENTATION, DELIVERY)

    #: Gates guarding operations that touch the remote or rewrite history.
    DESTRUCTIVE = (DELIVERY,)


@dataclass
class GateRequest:
    gate: str
    title: str
    body: str
    #: Short lines shown under the body, e.g. the branch and remote a push targets.
    facts: tuple[tuple[str, str], ...] = ()


class ApprovalManager:
    """Applies the approval policy for one run."""

    def __init__(self, store: StateStore, state: WorkflowState, *, interactive: bool) -> None:
        self.store = store
        self.state = state
        self.interactive = interactive

    def request(self, req: GateRequest) -> Decision:
        # 1. Already decided out-of-band (`orc approve` / `orc reject`).
        recorded = self.state.latest_approval(req.gate)
        if recorded is not None and recorded.decision is Decision.APPROVED:
            self._audit(req.gate, Decision.APPROVED, "pre-recorded")
            return Decision.APPROVED
        if recorded is not None and recorded.decision is Decision.REJECTED:
            raise ApprovalRejected(f"Gate '{req.gate}' was rejected by {recorded.actor}.")

        # 2. Auto-approval (already safety-checked at config load).
        if self.state.auto_approve:
            self.state.record_approval(req.gate, Decision.APPROVED, actor="auto-approve")
            self.store.save(self.state)
            self._audit(req.gate, Decision.APPROVED, "auto-approve")
            return Decision.APPROVED

        # 3. Interactive prompt.
        if self.interactive:
            return self._prompt(req)

        # 4. Non-interactive: stop cleanly and let the developer decide later.
        self._render(req)
        self.store.save(self.state)
        raise ApprovalRequired(req.gate, self.state.run_id)

    # -- internals ---------------------------------------------------------- #

    def _render(self, req: GateRequest) -> None:
        lines = [req.body.rstrip()]
        if req.facts:
            lines.append("")
            width = max(len(k) for k, _ in req.facts)
            lines.extend(f"  {k.ljust(width)}  {v}" for k, v in req.facts)
        console.print(
            Panel(
                "\n".join(lines),
                title=f"[gate]APPROVAL GATE — {req.title}[/gate]",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    def _prompt(self, req: GateRequest) -> Decision:
        self._render(req)
        destructive = req.gate in Gate.DESTRUCTIVE
        choices = ["a", "r", "c"]
        prompt = (
            "[a]pprove / [r]eject / request [c]hanges"
            if not destructive
            else "[a]pprove and execute / [r]eject / request [c]hanges"
        )
        answer = Prompt.ask(prompt, choices=choices, default="r" if destructive else "a")
        comment: str | None = None
        if answer == "c":
            comment = Prompt.ask("What should change?")
            decision = Decision.CHANGES_REQUESTED
        elif answer == "a":
            decision = Decision.APPROVED
        else:
            decision = Decision.REJECTED

        self.state.record_approval(req.gate, decision, comment=comment)
        self.store.save(self.state)
        self._audit(req.gate, decision, "interactive", comment)

        if decision is Decision.REJECTED:
            raise ApprovalRejected(f"Gate '{req.gate}' rejected by developer.")
        return decision

    def _audit(
        self, gate: str, decision: Decision, via: str, comment: str | None = None
    ) -> None:
        self.store.append_audit(
            AuditEvent(
                run_id=self.state.run_id,
                actor="developer" if via != "auto-approve" else "auto-approve",
                event="approval.decided",
                detail={"gate": gate, "decision": decision.value, "via": via, "comment": comment},
            )
        )
