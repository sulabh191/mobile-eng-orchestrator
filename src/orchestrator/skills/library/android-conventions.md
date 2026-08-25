---
name: android-conventions
description: Android/Kotlin engineering conventions the implementation must respect.
applies_to: [android]
tags: [implementation, planning, android]
---

- Match the repository's architecture (MVVM + repository, MVI, Clean layering) and its
  dependency-injection framework (Hilt, Koin, manual). Do not introduce a second one.
- Compose vs Views: match the screen being edited. In Compose, hoist state, keep composables
  side-effect free, and use the repository's existing state holder pattern.
- Coroutines: respect the existing dispatcher strategy and structured concurrency. Do not
  launch in `GlobalScope`; use the lifecycle-aware scope the codebase already uses.
- Declare dependencies through the version catalog (`gradle/libs.versions.toml`) if the
  repository has one; never hard-code versions in a module's build file.
- Keep `AndroidManifest.xml` edits minimal and justified — a new permission is a red flag
  that belongs in the plan, not a silent addition.
- Add unit tests in the existing framework (JUnit4/5, Turbine, MockK, Robolectric) and place
  them in the module's existing test source set.
- New user-facing strings go in `strings.xml`, never hard-coded in code or Compose.
- Do not change `minSdk`, `targetSdk`, AGP or Kotlin versions as a side effect.
- Preserve R8/ProGuard rules; if a change needs a keep rule, add it explicitly and say so.
