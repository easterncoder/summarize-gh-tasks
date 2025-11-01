#!/usr/bin/env python3
"""
Daily summarize workflow helper for Caseproof GitHub follow-ups.

On the first invocation each America/New_York day, this script generates a fresh
todo checklist and opens (or updates) a GitHub issue titled
`Todos for YYYY-MM-DD`. The issue body includes unfinished items from
the previous checklist plus new action items collected via `gh` CLI queries.
Subsequent invocations the same day simply display the already created issue.
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
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from functools import lru_cache
from typing import Any, List, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Python 3.9+ with zoneinfo support is required.") from exc


REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = Path("/tmp/caseproof-summarize")
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


def load_configuration() -> tuple[tuple[str, ...], str]:
    if not CONFIG_PATH.exists():
        raise SummarizeError(
            f"Missing configuration file at {CONFIG_PATH}. "
            "Copy config/status.json.example and set `target_repository`."
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
    if raw_target_repo is None:
        raise SummarizeError(
            f"`target_repository` must be specified in {CONFIG_PATH}."
        )
    if not isinstance(raw_target_repo, str):
        raise SummarizeError(
            f"`target_repository` in {CONFIG_PATH} must be a string."
        )
    target_repo_candidate = raw_target_repo.strip()
    if not target_repo_candidate:
        raise SummarizeError(
            f"`target_repository` in {CONFIG_PATH} cannot be empty."
        )
    if "/" not in target_repo_candidate:
        raise SummarizeError(
            f"`target_repository` in {CONFIG_PATH} must be in the form `owner/repo`."
        )
    target_repo = target_repo_candidate
    return organizations, target_repo


ORG_NAMES, TARGET_REPOSITORY = load_configuration()
GH_REPO_ARGS: tuple[str, ...] = ("--repo", TARGET_REPOSITORY)


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


def find_issue_by_title(title: str) -> dict[str, Any] | None:
    result = run(
        [
            "gh",
            "issue",
            "list",
            *GH_REPO_ARGS,
            "--state",
            "all",
            "--limit",
            "20",
            "--json",
            "number,title,body,url,createdAt,state",
            "--search",
            f"\"{title}\" in:title sort:created-desc",
        ]
    )
    issues = json.loads(result.stdout)
    for issue in issues:
        if issue.get("title") == title:
            return issue
    return None


def list_recent_todo_issues(limit: int = 5) -> list[dict[str, Any]]:
    result = run(
        [
            "gh",
            "issue",
            "list",
            *GH_REPO_ARGS,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,url,createdAt,state",
            "--search",
            "\"Todos\" in:title sort:created-desc",
        ]
    )
    return json.loads(result.stdout)


CHECKBOX_PATTERN = re.compile(r"^\s*[-*]\s*\[\s*([xX ])\s*\]\s*(.+)$")
ITEM_URL_PATTERN = re.compile(r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/")
ISSUE_URL_PATTERN = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/issues/(?P<number>\d+)"
)
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


def compose_issue_body(
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


def write_issue_body_to_tmp(body: str) -> Path:
    ensure_tmp_dir()
    tmp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=TMP_ROOT,
        suffix=".md",
    )
    with tmp_file:
        tmp_file.write(body)
    return Path(tmp_file.name)


def extract_issue_reference(output: str) -> dict[str, Any] | None:
    for line in output.splitlines():
        match = ISSUE_URL_PATTERN.search(line.strip())
        if match:
            url = match.group(0)
            try:
                number = int(match.group("number"))
            except (TypeError, ValueError) as exc:  # pragma: no cover
                raise SummarizeError(
                    f"Unexpected issue number in gh output: {match.group('number')}"
                ) from exc
            return {"number": number, "url": url}
    return None


@lru_cache(maxsize=1)
def get_repo_html_base() -> str:
    if TARGET_REPOSITORY:
        return f"https://github.com/{TARGET_REPOSITORY.strip('/')}"
    result = run(["git", "remote", "get-url", "origin"])
    remote = result.stdout.strip()
    if remote.startswith("git@github.com:"):
        path = remote.split(":", 1)[1]
    elif remote.startswith("https://github.com/"):
        path = remote.split("https://github.com/", 1)[1]
    elif remote.startswith("ssh://git@github.com/"):
        path = remote.split("ssh://git@github.com/", 1)[1]
    else:  # pragma: no cover
        raise SummarizeError(
            f"Unsupported remote URL format for origin: {remote or '<empty>'}"
        )
    if path.endswith(".git"):
        path = path[:-4]
    if not path or "/" not in path:
        raise SummarizeError(f"Unable to parse GitHub owner/repo from remote: {remote}")
    return f"https://github.com/{path.strip('/')}"


def build_issue_url(number: int) -> str:
    base = get_repo_html_base()
    return f"{base}/issues/{number}"


def create_issue(title: str, body: str) -> dict[str, Any]:
    body_path = write_issue_body_to_tmp(body)
    try:
        result = run(
            [
                "gh",
                "issue",
                "create",
                *GH_REPO_ARGS,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
        )
    finally:
        body_path.unlink(missing_ok=True)
    reference = extract_issue_reference(result.stdout)
    if not reference:
        raise SummarizeError(
            "gh issue create succeeded but did not return an issue URL. "
            "Please update the GitHub CLI to a recent version."
        )
    return reference


def update_issue(number: int, title: str, body: str) -> dict[str, Any]:
    body_path = write_issue_body_to_tmp(body)
    try:
        result = run(
            [
                "gh",
                "issue",
                "edit",
                str(number),
                *GH_REPO_ARGS,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
        )
    finally:
        body_path.unlink(missing_ok=True)
    reference = extract_issue_reference(result.stdout)
    if not reference:
        return {"number": number, "url": build_issue_url(number)}
    return reference


def close_issue(number: int, *, new_issue_url: str | None = None) -> None:
    cmd = ["gh", "issue", "close", str(number)]
    cmd.extend(GH_REPO_ARGS)
    if new_issue_url:
        cmd.extend(["--comment", f"Superseded by {new_issue_url}"])
    run(cmd)


def reopen_issue(number: int) -> None:
    cmd = ["gh", "issue", "reopen", str(number)]
    cmd.extend(GH_REPO_ARGS)
    run(cmd)


def show_issue(issue: dict[str, Any]) -> None:
    print(f"{issue['title']} — {issue['url']}")
    print("")
    print(issue.get("body") or "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Caseproof daily GitHub summary automation."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate the checklist even if today's issue already exists.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the current daily issue without modifying anything.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the checklist and print the would-be issue body without creating or updating an issue.",
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
    today_title = f"{TODOS_TITLE_PREFIX}{today_str}"

    existing_today = find_issue_by_title(today_title)

    if args.show and existing_today:
        show_issue(existing_today)
        return 0
    if args.show and not existing_today:
        issues = list_recent_todo_issues(limit=1)
        if not issues:
            raise SummarizeError("No Todos issues exist yet.")
        print("Today's issue not found; showing the most recent entry instead:\n")
        show_issue(issues[0])
        return 0

    if existing_today and not args.force:
        print(
            textwrap.dedent(
                """
                Today's checklist already exists (detected `Todos` issue for the current America/New_York day).
                Showing the current issue body instead of regenerating.
                """
            ).strip()
        )
        show_issue(existing_today)
        return 0

    recent_issues = list_recent_todo_issues(limit=10)

    if not existing_today:
        for issue in recent_issues:
            if issue.get("title") == today_title:
                existing_today = issue
                break

    previous_issue = None
    for issue in recent_issues:
        if existing_today and issue["number"] == existing_today["number"]:
            continue
        if any(issue["title"].startswith(prefix) for prefix in ALL_TODO_PREFIXES):
            previous_issue = issue
            break

    if args.force and existing_today:
        carryover_source = existing_today.get("body")
    else:
        carryover_source = (previous_issue or {}).get("body")
    carryover = extract_unfinished_items(carryover_source)

    sections_by_org = collect_sections_by_org()
    body = compose_issue_body(today_str, now_utc, carryover, sections_by_org)

    if args.dry_run:
        print(today_title)
        print("=" * len(today_title))
        print(body)
        return 0

    if args.force and existing_today:
        updated = update_issue(existing_today["number"], today_title, body)
        reopened = False
        if (existing_today.get("state") or "").upper() != "OPEN":
            reopen_issue(existing_today["number"])
            reopened = True
        status_text = "Issue updated"
        if reopened:
            status_text += " and reopened"
        print(f"{status_text}: {updated['url']}")
        return 0

    created = create_issue(today_title, body)
    if previous_issue and (previous_issue.get("state") or "").upper() == "OPEN":
        close_issue(previous_issue["number"], new_issue_url=created["url"])
        print(
            f"Closed previous issue #{previous_issue['number']} and created: {created['url']}"
        )
    else:
        print(f"Issue created: {created['url']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SummarizeError as exc:
        sys.stderr.write(f"summarize: {exc}\n")
        raise SystemExit(1) from exc
