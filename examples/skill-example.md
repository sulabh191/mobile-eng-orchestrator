---
name: our-networking-layer
description: How this codebase does networking; read before touching any API call.
applies_to: [ios]
tags: [planning, implementation]
---

Copy this file to `<your-app-repo>/.orchestrator/skills/` and edit it. It is injected into
the planning and implementation prompts for iOS repositories, so the agent follows your
conventions instead of inventing its own.

- All requests go through `APIClient`; never call `URLSession` directly.
- Endpoints live in `Endpoints.swift` as static factory methods, one per backend route.
- Responses decode into `Codable` DTOs under `Networking/DTO/`, then map to domain models.
  Do not let a DTO escape the networking layer.
- Errors surface as `APIError`; never leak `URLError` or a raw status code upwards.
- Anything user-visible on failure goes through `ErrorPresenter`, not an inline alert.
