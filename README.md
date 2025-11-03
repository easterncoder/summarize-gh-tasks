# Summarize GitHub Tasks
**Version:** 2.0.0

> Keep this document versioned. Bump the version using semantic rules whenever
> you edit the content so operators can track documentation changes.

Summarize GitHub Tasks is a small command line application that assembles your
open GitHub work into a daily Markdown checklist. Running the bundled
`summarize` script queries GitHub for assigned issues, review requests, and
authored pull requests, then writes the results to `my-tasks/YYYY-MM-DD.md`
inside a local repository that stays on your machine.

This automation was mostly vibe coded end-to-end with OpenAI Codex, the
GPT-5-based coding agent that powers this repository. Codex keeps the workflow
lean—drafting logic, rewriting docs, and double-checking edge cases—so you can
focus on shipping.

The tool is intended for anyone who wants a repeatable daily review ritual with
a single source of truth that lives in a personal repository instead of public
GitHub issues.

## Features

- Generates or refreshes one Markdown checklist per day and overwrites it on
  demand with `--force`.
- Carries over unfinished checklist items from the previous day while avoiding
  duplicates.
- Groups todos by GitHub organization so related repositories stay together.
- Supports dry runs and read-only views without writing to disk.

## Requirements

- Python 3.11 or newer.
- The GitHub CLI (`gh`) installed and authenticated (`gh auth login`).
- Access to the repositories you want to summarize.

## Installation

```bash
git clone https://github.com/easterncoder/summarize-gh-tasks.git
cd summarize-gh-tasks
python3 -m venv .venv && source .venv/bin/activate  # optional
pip install -r requirements.txt  # only if you add dependencies
```

The repository ships with an executable `summarize` wrapper that runs the Python
entrypoint under `scripts/summarize.py`. Mark it as executable if your checkout
loses the bit:

```bash
chmod +x summarize
```

## Configuration

Copy the example file and customize it for your organization list:

```bash
cp config/status.json.example config/status.json
```

The CLI reads a small JSON object with these supported keys:

```json
{
  "target_repository": "org/my-gh-tasks",
  "organizations": ["github-organization-1", "github-organization-2"]
}
```

`target_repository` is now optional and retained only for backwards
compatibility. Summarize GitHub Tasks ignores the value but will happily honour
legacy configuration files. If you omit the `organizations` array, the script
falls back to its built-in defaults; otherwise it queries the listed
organizations.

You can override configuration from the environment with
`CASEPROOF_GH_ORGS=OrgOne,OrgTwo` (or `CASEPROOF_GH_ORG` for a single entry) at
runtime.

## Local checklist repository

Checklists are stored under `my-tasks/`, which is ignored by the main
repository. Create the directory and initialise your personal history once:

```bash
mkdir -p my-tasks
(cd my-tasks && git init)
```

Because the folder remains untracked in this project, you can version your
private checklists separately or leave them as plain files on disk.

## Usage

Generate or update the checklist for today:

```bash
./summarize
```

Show the current checklist without changing it:

```bash
./summarize --show
```

Preview the generated body without writing to disk:

```bash
./summarize --dry-run
```

Regenerate today's checklist file (skipping cache):

```bash
./summarize --force
```

The script stores any intermediate artifacts under `/tmp/caseproof-summarize/` and
cleans up temporary files automatically.

## Development Notes

- The automation depends heavily on the GitHub CLI. If you change `gh` command
  flags, update the fallback parsing logic in `scripts/summarize.py`.
- Python code follows the standard library only; the project intentionally ships
  dependency-free.
- Run `./summarize --dry-run` after making changes to inspect the rendered
  checklist before pushing.
- The repository uses a single linear history. If you need to modify past
  behavior, prefer amending the latest commit and force pushing.

## Support

File issues or pull requests on GitHub if you want new features, notice bugs, or
have automation ideas. The more context you provide (sample output, GitHub CLI
version, etc.), the easier it is to help. If checklists stop appearing, confirm
that `my-tasks/` exists, is writable, and contains a valid git repository (if
you rely on commit history).

## License

Summarize GitHub Tasks is free software released under the
[GNU General Public License v3.0](LICENSE).
