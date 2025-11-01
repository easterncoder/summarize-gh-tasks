# AGENTS.md
**Version:** 1.0.0

> Keep this playbook aligned with the behaviour of the application. Bump the
> version using semantic rules whenever you edit the document.

## Mission

Summarize GitHub Tasks is a standalone CLI that publishes a single GitHub issue
each day summarising your open work. The agent’s job is to ensure the
automation continues to create accurate, actionable checklists without leaking
sensitive data or spamming repositories.

## Daily Operations

- Run `./summarize` once per local morning (default timezone:
  `America/New_York`). The command will create or refresh the issue titled
  `Todos for YYYY-MM-DD`.
- Prefer the `--dry-run` flag when validating changes or running in staging
  environments.
- Use `--show` to display a previously generated issue without mutating
  anything.
- If a run fails midway, fix the root cause, then re-execute with `--force` to
  regenerate the issue in place.

## Configuration Management

- Store organization settings and the publishing repository in `config/status.json`. Clone
  `config/status.json.example` when bootstrapping a new environment.
- The CLI refuses to run unless `target_repository` is populated; confirm the
  owner/repo slug is correct before scheduling automation.
- Do not commit live configuration. The repository’s `.gitignore` keeps the
  real `status.json` local; double-check before pushing.
- Environment overrides (`CASEPROOF_GH_ORGS` or `CASEPROOF_GH_ORG`) are the
  quickest way to test changes against alternate tenants.

## Coding Standards

- Python source lives under `scripts/`. The project is dependency-free; stick to
  the standard library unless there is a compelling operational reason.
- Favour explicit error handling. When shelling out to `gh`, surface the exit
  code and the relevant stderr snippet.
- Keep the output markdown terse: every line should be an actionable checklist
  item. Deduplicate entries before writing to GitHub.
- When changing behaviour, update this document and `README.md` alongside the
  code so operators stay aligned.
- Follow the Conventional Commits 1.0.0 spec for every commit message.

## Release Process

1. Make the change on `main`. The repo intentionally uses a linear commit
   history—rebase if conflicts arise.
2. Run `./summarize --dry-run` locally to confirm the rendered issue layout.
3. If the change affects live automation, run `./summarize --force` once to ensure
   the published issue matches expectations.
4. When creating a release, use Semantic Versioning 2.0.0 to choose the next tag.
5. Publish releases with the GitHub CLI (`gh release create`) so metadata stays consistent.
6. Tag releases manually if you want to snapshot a known-good version.

## Support & Troubleshooting

- Authentication errors usually stem from an expired `gh auth login` session.
  Re-authenticate and retry.
- If GitHub rate limits the CLI, re-run with `--dry-run` to inspect the body,
  then wait before publishing.
- Capture environment details (Python version, `gh --version`, command flags)
  when filing an issue so maintainers can reproduce the problem.
