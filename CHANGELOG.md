# Changelog

## 3.1.1

- Initial public distribution of P02, P08, and P10 with the Harbor extension 0.7.2.
- Explicit P02 Chinese-body language-violation criteria and structured language
  observation checks. Contradictory or unreadable evidence blocks a numeric score.
- Fixed-revision dataset downloads with file hashes and safe existing-directory checks.

Task-package and extension versions are separate. Harbor is pinned to 0.20.0.

- Install the pinned Codex platform binary explicitly and check its version during Docker builds.
- Align the P02 synthetic pipeline fixture with the language-observation schema.
