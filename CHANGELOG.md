# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] - 2024-11-02

### Changed
- Store daily todos as Markdown files under a local `my-tasks/` repository instead of publishing GitHub issues.
- Treat `target_repository` as optional configuration while keeping organization lookups intact.
- Refresh operator documentation to describe the local-storage workflow and setup.

### Fixed
- Exclude draft pull requests from review and authored sections when generating the checklist.

## [1.0.0] - 2024-11-02

### Added
- Initial release of the Summarize GitHub Tasks CLI.
- Daily automation that queries assigned issues, review requests, and authored PRs with the GitHub CLI.
- Automatic publication of a single `Todos for YYYY-MM-DD` issue per day, including carryover of unfinished checklist items.
- Options for `--dry-run`, `--show`, and `--force` to preview, inspect, or regenerate the issue.
- Configuration via `config/status.json` with support for `CASEPROOF_GH_ORGS` / `CASEPROOF_GH_ORG` environment overrides.

### Released
- GitHub release `1.0.0` capturing the initial public drop of the CLI.
