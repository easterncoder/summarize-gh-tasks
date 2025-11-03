# AGENTS.md
**Version:** 2.0.0

> Keep this playbook aligned with the behaviour of the application. Bump the
> version using semantic rules whenever you edit the document.

## Mission

Summarize GitHub Tasks is a standalone CLI that writes a single Markdown
checklist each day into the local `my-tasks` repository. The agent’s job is to
ensure the automation continues to create accurate, actionable checklists
without leaking sensitive data or polluting upstream repositories.

## Daily Operations

- Run `./summarize` once per local morning (default timezone:
  `America/New_York`). The command will create or refresh
  `my-tasks/YYYY-MM-DD.md`.
- Prefer the `--dry-run` flag when validating changes or running in staging
  environments.
- Use `--show` to display a previously generated checklist without mutating
  anything.
- If a run fails midway, fix the root cause, then re-execute with `--force` to
  regenerate the Markdown file in place.

## Configuration Management

- Store organization settings in `config/status.json`. Clone
  `config/status.json.example` when bootstrapping a new environment.
- `target_repository` is now optional and ignored; leave it in place for
  backwards compatibility or remove it entirely.
- Do not commit live configuration. The repository’s `.gitignore` keeps the
  real `status.json` local; double-check before pushing.
- `my-tasks/` is intentionally ignored by git. Create the directory manually
  and run `git init` inside if you want local history.
- Environment overrides (`CASEPROOF_GH_ORGS` or `CASEPROOF_GH_ORG`) are the
  quickest way to test changes against alternate tenants.

## Coding Standards

- Python source lives under `scripts/`. The project is dependency-free; stick to
  the standard library unless there is a compelling operational reason.
- Favour explicit error handling. When shelling out to `gh`, surface the exit
  code and the relevant stderr snippet.
- Keep the output markdown terse: every line should be an actionable checklist
  item. Deduplicate entries before writing to disk.
- When changing behaviour, update this document and `README.md` alongside the
  code so operators stay aligned.
- Follow the Conventional Commits 1.0.0 spec for every commit message.

## Release Process

1. Make the change on `main`. The repo intentionally uses a linear commit
   history—rebase if conflicts arise.
2. Run `./summarize --dry-run` locally to confirm the rendered checklist layout.
3. If the change affects live automation, run `./summarize --force` once to ensure
   today's Markdown file matches expectations.
4. When creating a release, use Semantic Versioning 2.0.0 to choose the next tag.
5. Create a changelog to be used in both updating CHANGELOG.md and the github release.
6. Publish releases with the GitHub CLI (`gh release create`) so metadata stays consistent.
7. Tag releases manually if you want to snapshot a known-good version.

## Support & Troubleshooting

- Authentication errors usually stem from an expired `gh auth login` session.
  Re-authenticate and retry.
- If GitHub rate limits the CLI, re-run with `--dry-run` to inspect the
  generated Markdown, then wait before writing again.
- If checklists go missing, confirm `my-tasks/` exists, is writable, and (if
  desired) has its own git history.
- Capture environment details (Python version, `gh --version`, command flags)
  when filing an issue so maintainers can reproduce the problem.
