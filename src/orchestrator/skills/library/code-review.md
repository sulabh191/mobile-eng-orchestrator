---
name: code-review
description: Summarise a change for a human reviewer, honestly.
tags: [review]
---

Write for the engineer who has to approve this and is accountable for it in production.

- Lead with what changed and why, in one sentence a reviewer can quote in standup.
- Map each requirement id to `covered`, `partial` or `missing`. Never claim coverage you
  cannot point at in the diff — a `missing` you flag yourself costs far less than one
  found in review.
- Call out risk plainly: behaviour changes behind flags, error paths, migrations,
  performance-sensitive code, anything touching auth or payments.
- The reviewer checklist should be things only a human can confirm (does this match the
  design, is this the right product behaviour), not things validation already proved.
- Commit message: conventional-commit subject under 72 characters, imperative mood, ticket
  key in the trailer.
