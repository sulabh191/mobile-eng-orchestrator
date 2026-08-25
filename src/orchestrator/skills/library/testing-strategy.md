---
name: testing-strategy
description: Decide what to test and at which level.
tags: [planning, implementation, testing]
---

- Every behaviour change needs a test that fails before the change and passes after. If you
  cannot describe that test, the requirement is not yet concrete enough.
- Prefer the cheapest level that actually catches the regression: unit over integration,
  integration over UI. A UI test added for logic that a unit test covers is a slow no-op.
- Bug tickets get a regression test reproducing the reported condition specifically.
- Do not assert on implementation details (call counts, private state) when an observable
  outcome is available.
- Never weaken, skip or delete an existing test to make a build pass. A failing existing
  test is a finding to report, not an obstacle to remove.
