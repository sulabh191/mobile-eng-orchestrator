---
name: requirements-analysis
description: Turn a ticket into testable, unambiguous requirements.
tags: [requirements, planning]
---

Convert the ticket into requirements a reviewer could disagree with. Each one must be
independently verifiable.

- Every requirement gets a stable id (`R1`, `R2`, …) that later plan steps reference.
- Prefer the ticket's own words for behaviour; do not invent scope. If the ticket implies
  something without stating it, record it under `assumptions`, not as a requirement.
- Acceptance criteria are observable outcomes ("the list reloads and the spinner is
  dismissed"), never implementation instructions ("call `refresh()`").
- If a genuine ambiguity would change the implementation, put it in `open_questions`. A
  run with blocking questions should stop at the requirements gate rather than guess.
- Mark anything the ticket explicitly excludes as a non-goal, so the plan cannot drift into it.
