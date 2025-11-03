#!/usr/bin/env python3
"""
Daily summarize workflow helper for Caseproof follow-ups.

On the first invocation each America/New_York day, this script generates a fresh
todo checklist and writes it to a Markdown file under the local `my-tasks`
repository. Subsequent invocations the same day simply display the already
created checklist. The file body includes unfinished items from the previous
checklist plus new action items collected via `gh` CLI queries.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, List, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Python 3.9+ with zoneinfo support is required.") from exc


REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = Path("/tmp/caseproof-summarize")
TASKS_DIR = REPO_ROOT / "my-tasks"
CONFIG_PATH = REPO_ROOT / "config/status.json"
TODOS_TITLE_PREFIX = "Todos for "
LEGACY_TODOS_PREFIXES = ("Caseproof Todos for ",)
ALL_TODO_PREFIXES = (TODOS_TITLE_PREFIX, *LEGACY_TODOS_PREFIXES)
OWNER_PLACEHOLDER = "__OWNER_PLACEHOLDER__"


class SummarizeError(RuntimeError):
    """Raised when the summarize workflow cannot continue."""


def run(cmd: Sequence[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    """Run a command and ensure it succeeds."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        missing = cmd[0]
        raise SummarizeError(f"Required command `{missing}` is not installed.") from exc
    except subprocess.CalledProcessError as exc:
        raise SummarizeError(
            f"Command `{' '.join(cmd)}` failed with exit code {exc.returncode}:\n"
            f"{exc.stderr.strip()}"
        ) from exc
    return result


@dataclasses.dataclass(frozen=True)
class Query:
    slug: str
    heading: str
    command: Sequence[str]
    imperative_template: str
    empty_message: str

    def format_entry(self, item: dict[str, Any]) -> tuple[str, str]:
        number = item.get("number")
        title = (item.get("title") or "").strip()
        url = item.get("url")
        if not number or not url:
            raise SummarizeError(
                f"Query `{self.slug}` returned an item missing `number` or `url`: {item}"
            )
        title = " ".join(title.split())
        repository = repo_slug_from_item(item)
        link = f"[{repository}#{number} {title}]({url})"
        canonical = canonicalize_reference(url, repository, number)
        return canonical, self.imperative_template.format(link=link)

    def format_item(self, item: dict[str, Any]) -> str:
        return self.format_entry(item)[1]


def load_configuration() -> tuple[tuple[str, ...], str | None]:
    if not CONFIG_PATH.exists():
        raise SummarizeError(
            f"Missing configuration file at {CONFIG_PATH}. "
            "Copy config/status.json.example and configure the organizations list."
        )
    try:
        config_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SummarizeError(f"Unable to parse {CONFIG_PATH}: {exc}") from exc
    if not isinstance(config_data, dict):
        raise SummarizeError(f"{CONFIG_PATH} must contain a JSON object.")
    env_value = os.environ.get("CASEPROOF_GH_ORGS") or os.environ.get("CASEPROOF_GH_ORG")
    if env_value:
        env_orgs = tuple(
            name.strip() for name in env_value.split(",") if name.strip()
        )
        if env_orgs:
            organizations = env_orgs
        else:
            organizations = ("Caseproof",)
    else:
        config_data = config_data or {}
        raw_orgs = config_data.get("organizations")
        if raw_orgs is None:
            organizations = ("Caseproof",)
        elif isinstance(raw_orgs, list):
            cleaned = tuple(
                str(name).strip() for name in raw_orgs if str(name).strip()
            )
            if cleaned:
                organizations = cleaned
            else:
                organizations = ("Caseproof",)
        else:
            raise SummarizeError(
                f"`organizations` in {CONFIG_PATH} must be an array of strings."
            )
    raw_target_repo = config_data.get("target_repository")
    target_repo: str | None = None
    if raw_target_repo is not None:
        if not isinstance(raw_target_repo, str):
            raise SummarizeError(
                f"`target_repository` in {CONFIG_PATH} must be a string."
            )
        target_repo_candidate = raw_target_repo.strip()
        if target_repo_candidate:
            if "/" not in target_repo_candidate:
                raise SummarizeError(
                    f"`target_repository` in {CONFIG_PATH} must be in the form `owner/repo`."
                )
            target_repo = target_repo_candidate
    return organizations, target_repo


ORG_NAMES, _TARGET_REPOSITORY = load_configuration()


def build_queries() -> List[Query]:
    return [
        Query(
            slug="assigned-issues",
            heading="Assigned Issues",
            command=[
                "gh",
                "search",
                "issues",
                "--owner",
                OWNER_PLACEHOLDER,
                "--assignee",
                "@me",
                "--state",
                "open",
                "--json",
                "number,title,url,repository",
                "--limit",
                "50",
            ],
            imperative_template="Triage {link}.",
            empty_message="Confirm no assigned issues need attention.",
        ),
        Query(
            slug="review-requests",
            heading="PR Review Requests",
            command=[
                "gh",
                "search",
                "prs",
                "--owner",
                OWNER_PLACEHOLDER,
                "--review-requested",
                "@me",
                "--state",
                "open",
                "--json",
                "number,title,url,repository",
                "--limit",
                "50",
            ],
            imperative_template="Review {link}.",
            empty_message="Confirm no outstanding review requests.",
        ),
        Query(
            slug="authored-prs",
            heading="Authored PRs",
            command=[
                "gh",
                "search",
                "prs",
                "--owner",
                OWNER_PLACEHOLDER,
                "--author",
                "@me",
                "--state",
                "open",
                "--json",
                "number,title,url,repository",
                "--limit",
                "50",
            ],
            imperative_template="Follow up on {link}.",
            empty_message="Confirm no authored PRs require action.",
        ),
    ]


def ensure_tmp_dir() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TMP_ROOT


def build_command_for_org(base_command: Sequence[str], org: str) -> List[str]:
    cmd = list(base_command)
    replaced = False
    for index, token in enumerate(cmd):
        if token == OWNER_PLACEHOLDER:
            cmd[index] = org
            replaced = True
            break
    if not replaced:
        cmd.extend(["--owner", org])
    return cmd


def is_automation_issue(item: dict[str, Any]) -> bool:
    title = (item.get("title") or "").strip()
    return any(title.startswith(prefix) for prefix in ALL_TODO_PREFIXES)


def run_query(query: Query) -> dict[str, list[dict[str, Any]]]:
    tmp_dir = ensure_tmp_dir()
    aggregated: dict[str, list[dict[str, Any]]] = {org: [] for org in ORG_NAMES}
    seen_urls: set[str] = set()
    for org in ORG_NAMES:
        artifact_path = tmp_dir / f"{query.slug}-{org}-{uuid.uuid4().hex}.json"
        cmd = build_command_for_org(query.command, org)
        with artifact_path.open("w", encoding="utf-8") as artifact_file:
            result = run(cmd)
            artifact_file.write(result.stdout)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SummarizeError(
                f"Failed to parse JSON from query `{query.slug}` for org `{org}`. See {artifact_path}."
            ) from exc
        if not isinstance(data, list):
            raise SummarizeError(
                f"Unexpected JSON structure from query `{query.slug}` for org `{org}`: "
                f"{type(data).__name__}"
            )
        for item in data:
            if is_automation_issue(item):
                continue
            url = item.get("url")
            if not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            item_copy = dict(item)
            aggregated.setdefault(org, []).append(item_copy)
    return aggregated


CHECKBOX_PATTERN = re.compile(r"^\s*[-*]\s*\[\s*([xX ])\s*\]\s*(.+)$")
ITEM_URL_PATTERN = re.compile(r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/")
GITHUB_URL_PATTERN = re.compile(r"https://github\.com/[^\s)]+")
REPO_REF_PATTERN = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+)")


def repo_slug_from_item(item: dict[str, Any]) -> str:
    repository = item.get("repository")
    if isinstance(repository, dict):
        name_with_owner = repository.get("nameWithOwner")
        if isinstance(name_with_owner, str) and name_with_owner:
            return name_with_owner
        owner = repository.get("owner")
        owner_login = None
        if isinstance(owner, dict):
            owner_login = owner.get("login")
        name = repository.get("name")
        if isinstance(owner_login, str) and owner_login and isinstance(name, str) and name:
            return f"{owner_login}/{name}"
    url = item.get("url") or ""
    match = ITEM_URL_PATTERN.search(url)
    if match:
        return f"{match.group('owner')}/{match.group('repo')}"
    return "unknown"


def canonicalize_url(url: str | None) -> str:
    if not url:
        return ""
    return url.rstrip("/").lower()


def canonicalize_reference(url: str | None, repository: str, number: Any) -> str:
    normalized_url = canonicalize_url(url)
    if normalized_url:
        return normalized_url
    return f"{repository.lower()}#{number}".lower()


def canonicalize_line(line: str) -> str:
    match = GITHUB_URL_PATTERN.search(line)
    if match:
        return canonicalize_url(match.group(0))
    repo_match = REPO_REF_PATTERN.search(line)
    if repo_match:
        return repo_match.group(1).lower()
    return " ".join(line.split()).lower()


def extract_unfinished_items(issue_body: str | None) -> list[str]:
    if not issue_body:
        return []
    carryover: list[str] = []
    for line in issue_body.splitlines():
        match = CHECKBOX_PATTERN.match(line)
        if not match:
            continue
        marker, text = match.groups()
        if marker.lower() != "x":
            carryover.append(text.strip())
    return carryover


def compose_checklist_body(
    today_str: str,
    now_utc: datetime,
    carryover: list[str],
    sections_by_org: dict[str, list[tuple[Query, list[tuple[str, str]]]]],
) -> str:
    lines: list[str] = []
    lines.append(f"# Todos — {today_str}")
    lines.append("")
    lines.append(
        f"_Generated {now_utc.strftime('%Y-%m-%d %H:%M')} UTC via `./summarize`._"
    )
    lines.append("")
    seen_items: set[str] = set()
    deduped_carryover: list[str] = []
    for item in carryover:
        canonical = canonicalize_line(item)
        if canonical in seen_items:
            continue
        seen_items.add(canonical)
        deduped_carryover.append(item)
    if deduped_carryover:
        lines.append("## Carryover from Previous List")
        for item in deduped_carryover:
            lines.append(f"- [ ] {item}")
        lines.append("")
    ordered_orgs = list(dict.fromkeys([*ORG_NAMES, *sections_by_org.keys()]))
    for org in ordered_orgs:
        org_sections = sections_by_org.get(org, [])
        lines.append(f"## {org} Todos")
        lines.append("")
        if not org_sections:
            lines.append("- [ ] Confirm no actionable items today.")
            lines.append("")
            continue
        for query, entries in org_sections:
            filtered_lines: list[str] = []
            for entry in entries:
                canonical, line = entry
                canonical_id = canonical or canonicalize_line(line)
                if canonical_id in seen_items:
                    continue
                seen_items.add(canonical_id)
                filtered_lines.append(line)
            lines.append(f"### {query.heading}")
            if filtered_lines:
                for line in filtered_lines:
                    lines.append(f"- [ ] {line}")
            else:
                lines.append(f"- [ ] {query.empty_message}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_sections_by_org() -> dict[str, list[tuple[Query, list[tuple[str, str]]]]]:
    sections: dict[str, list[tuple[Query, list[tuple[str, str]]]]] = {
        org: [] for org in ORG_NAMES
    }
    for query in build_queries():
        try:
            items_by_org = run_query(query)
        except SummarizeError as exc:
            raise SummarizeError(
                f"Unable to complete query '{query.slug}'. {exc}"
            ) from exc
        for org in ORG_NAMES:
            org_items = items_by_org.get(org, [])
            sorted_items = sorted(
                org_items,
                key=lambda item: (
                    repo_slug_from_item(item).lower(),
                    item.get("number") or 0,
                ),
            )
            formatted = [query.format_entry(item) for item in sorted_items]
            sections.setdefault(org, []).append((query, formatted))
    return sections


CHECKLIST_FILENAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def ensure_tasks_dir() -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    return TASKS_DIR


def list_checklist_files() -> list[Path]:
    if not TASKS_DIR.exists():
        return []
    files = [
        path
        for path in TASKS_DIR.iterdir()
        if path.is_file() and CHECKLIST_FILENAME_PATTERN.match(path.name)
    ]
    return sorted(files)


def find_previous_checklist(today_filename: str) -> Path | None:
    for path in reversed(list_checklist_files()):
        if path.name < today_filename:
            return path
    return None


def find_latest_checklist() -> Path | None:
    files = list_checklist_files()
    return files[-1] if files else None


def show_checklist(path: Path) -> None:
    print(f"{path.name} — {path.resolve()}")
    print("")
    print(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Caseproof daily checklist generator."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the checklist even if today's Markdown file already exists.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the current daily checklist without modifying anything.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the checklist and print the would-be Markdown without writing a file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        timezone = ZoneInfo("America/New_York")
    except Exception as exc:
        raise SummarizeError("Unable to load America/New_York timezone.") from exc

    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    now_ny = now_utc.astimezone(timezone)
    today_str = now_ny.date().isoformat()
    today_filename = f"{today_str}.md"
    today_path = TASKS_DIR / today_filename
    existing_today = today_path if today_path.exists() else None

    if args.show and existing_today:
        show_checklist(today_path)
        return 0
    if args.show and not existing_today:
        fallback = find_latest_checklist()
        if not fallback:
            raise SummarizeError("No Todos checklists exist yet.")
        print("Today's checklist not found; showing the most recent entry instead:\n")
        show_checklist(fallback)
        return 0

    if existing_today and not args.force:
        print(
            textwrap.dedent(
                """
                Today's checklist already exists (found local Markdown file for the current America/New_York day).
                Showing the current file instead of regenerating.
                """
            ).strip()
        )
        show_checklist(today_path)
        return 0

    if args.force and existing_today:
        carryover_source = today_path.read_text(encoding="utf-8")
    else:
        previous_path = find_previous_checklist(today_filename)
        carryover_source = (
            previous_path.read_text(encoding="utf-8") if previous_path else None
        )
    carryover = extract_unfinished_items(carryover_source)

    sections_by_org = collect_sections_by_org()
    body = compose_checklist_body(today_str, now_utc, carryover, sections_by_org)

    if args.dry_run:
        print(today_filename)
        print("=" * len(today_filename))
        print(body)
        return 0

    ensure_tasks_dir()
    if args.force and existing_today:
        today_path.write_text(body, encoding="utf-8")
        print(f"Checklist regenerated at {today_path}")
        return 0

    today_path.write_text(body, encoding="utf-8")
    if find_previous_checklist(today_filename):
        print(f"Checklist written to {today_path} (carryover items preserved).")
    else:
        print(f"Checklist written to {today_path}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SummarizeError as exc:
        sys.stderr.write(f"summarize: {exc}\n")
        raise SystemExit(1) from exc
