---
name: ios-conventions
description: iOS/Swift engineering conventions the implementation must respect.
applies_to: [ios]
tags: [implementation, planning, ios]
---

- Follow the repository's existing architecture (MVVM, TCA, VIPER, plain UIKit) rather than
  introducing another one. Look at two neighbouring features before writing anything.
- SwiftUI vs UIKit: match the screen you are editing, not the codebase average.
- Concurrency: if the file already uses `async/await`, stay there; do not mix in completion
  handlers or Combine unless the surrounding code does. Respect actor isolation and
  `@MainActor` annotations — UI state updates belong on the main actor.
- Avoid force-unwrapping and `try!` in production paths. Model absence with optionals or
  typed errors that match existing error handling.
- Keep view bodies small; push logic into the view model where the repository already does.
- Add unit tests to the existing test target. Prefer XCTest unless the repository has
  adopted Swift Testing.
- New user-facing strings go through the repository's localisation mechanism — never
  hard-coded literals if `Localizable.strings`/String Catalogs exist.
- Do not edit `.pbxproj` by hand when the project uses Tuist, XcodeGen or SPM; regenerate
  through the tool the repository uses.
- Accessibility: give new interactive elements labels and respect Dynamic Type if the
  surrounding screens do.
