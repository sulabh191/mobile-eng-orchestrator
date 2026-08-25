---
name: git-hygiene
description: Branch, commit and PR conventions for delivery.
tags: [delivery, git]
---

- Branch from the repository's default branch, named `<type>/<issue-key-lower>-<slug>`.
- Never commit directly to a protected branch, never force-push, never rewrite history the
  developer did not ask you to rewrite.
- One logical change per commit. Conventional-commit subject, imperative mood, ≤72 chars,
  ticket key in a `Refs:` trailer.
- Stage only files the plan touched. If `git status` shows something unexpected, stop and
  surface it — an unrelated stray file in a PR erodes trust in every future run.
- The PR body states what changed, why, how it was validated, and what a reviewer should
  look at first. Link the ticket.
