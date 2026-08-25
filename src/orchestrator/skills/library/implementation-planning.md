---
name: implementation-planning
description: Produce a reviewable, minimal, dependency-ordered plan.
tags: [planning]
---

The plan is what the developer approves, so it must be readable in two minutes.

- One step should be one coherent edit a reviewer could evaluate on its own. Five focused
  steps beat one step called "implement the feature".
- Name concrete files. Read the repository first; a plan citing files that do not exist is
  a failed plan.
- Every step declares which requirement ids it satisfies. A requirement no step satisfies
  is a gap; a step satisfying nothing is scope creep.
- Order by real dependency only. Steps that can happen in any order should not declare one.
- Prefer the smallest change that satisfies the requirement inside existing architecture.
  Refactoring is a separate ticket unless the ticket asks for it.
- State the test strategy explicitly: which existing tests cover this, and which new ones
  are needed.
- Flag anything touching auth, payments, persistence, migrations, telemetry or feature
  flags as `risk: high`.
