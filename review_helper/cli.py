"""CLI entry point for review-helper."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from review_helper import __version__
from review_helper.chrome import ChromeCliError, close_tab, list_tabs, open_tab
from review_helper.my_reviews import collect_prs_to_open, normalize_listing_url
from review_helper.pr_urls import PullRequestRef, parse_pr_url, tab_keep_score, tab_url_key
from review_helper.progress import iterate_with_progress, status as log_status
from review_helper.git_config import all_gitconfig_values
from review_helper.review_batch import check_pr_statuses

PR_AUTHOR_RE = re.compile(r" by ([^·]+?) · Pull Request ", re.IGNORECASE)


def _split_name(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [part.lower() for part in re.split(r"[\s._-]+", spaced) if part]


def _identity_tokens(git_name: str | None, git_email: str | None) -> set[str]:
    tokens: set[str] = set()
    if git_name:
        tokens.update(_split_name(git_name))
        tokens.add(git_name.lower())
    if git_email and "@" in git_email:
        local = git_email.split("@", 1)[0].lower()
        tokens.add(local)
        tokens.update(_split_name(local))
    return {t for t in tokens if len(t) >= 4}


def _author_names_from_tabs(prs: list[PullRequestRef]) -> list[str]:
    authors: list[str] = []
    for pr in prs:
        match = PR_AUTHOR_RE.search(pr.title)
        if match:
            authors.append(match.group(1).strip())
    return authors


def _related_names(prs: list[PullRequestRef], tokens: set[str]) -> list[str]:
    if not tokens:
        return []
    related: list[str] = []
    seen: set[str] = set()
    for author in _author_names_from_tabs(prs):
        author_lower = author.lower()
        if any(token in author_lower for token in tokens):
            key = author_lower
            if key not in seen:
                seen.add(key)
                related.append(author)
    return related


def _reviewer_names(
    args: argparse.Namespace,
    prs: list[PullRequestRef] | None = None,
) -> list[str]:
    names: list[str] = []
    git_names = all_gitconfig_values("user.name")
    git_emails = all_gitconfig_values("user.email")

    if args.reviewer:
        names.extend(args.reviewer)
    else:
        names.extend(git_names)
        for git_email in git_emails:
            if "@" in git_email:
                names.append(git_email.split("@", 1)[0])
        for github_user in all_gitconfig_values("github.user"):
            names.append(github_user)
        tokens: set[str] = set()
        for git_name in git_names:
            tokens.update(_identity_tokens(git_name, None))
        for git_email in git_emails:
            tokens.update(_identity_tokens(None, git_email))
        if prs:
            names.extend(_related_names(prs, tokens))

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def _collect_pr_tabs() -> list[PullRequestRef]:
    tabs = list_tabs()
    prs: list[PullRequestRef] = []
    for tab in tabs:
        pr = parse_pr_url(tab.url, tab)
        if pr:
            prs.append(pr)
    return prs


def _dedupe_tabs(prs: list[PullRequestRef]) -> tuple[list[str], dict[tuple, PullRequestRef]]:
    groups: dict[tuple, list[PullRequestRef]] = defaultdict(list)
    for pr in prs:
        groups[pr.key].append(pr)

    to_close: list[str] = []
    kept: dict[tuple, PullRequestRef] = {}

    for key, group in groups.items():
        group.sort(key=lambda p: tab_keep_score(p.url), reverse=True)
        kept[key] = group[0]
        for duplicate in group[1:]:
            to_close.append(duplicate.tab_id)

    return to_close, kept


def _format_pr(pr: PullRequestRef) -> str:
    return f"{pr.host} {pr.project}#{pr.number}"


def _group_tabs_by_pr(
    prs: list[PullRequestRef], tab_ids: set[str]
) -> list[tuple[PullRequestRef, list[PullRequestRef]]]:
    by_key: dict[tuple, list[PullRequestRef]] = defaultdict(list)
    for pr in prs:
        if pr.tab_id in tab_ids:
            by_key[pr.key].append(pr)

    grouped: list[tuple[PullRequestRef, list[PullRequestRef]]] = []
    for key in sorted(by_key, key=lambda k: (k[1], k[2], k[3])):
        tabs = by_key[key]
        tabs.sort(key=lambda p: int(p.tab_id))
        grouped.append((tabs[0], tabs))
    return grouped


def _print_sections(
    prs: list[PullRequestRef],
    sections: list[tuple[str, set[str]]],
) -> list[str]:
    tabs_to_close: list[str] = []
    printed = False
    for heading, tab_ids in sections:
        grouped = _group_tabs_by_pr(prs, tab_ids)
        if not grouped:
            continue
        if printed:
            print()
        tabs_to_close.extend(_print_section(heading, grouped))
        printed = True
    return tabs_to_close


def _print_section(
    heading: str,
    grouped: list[tuple[PullRequestRef, list[PullRequestRef]]],
) -> list[str]:
    tab_ids: list[str] = []
    print(f"{heading} ({sum(len(tabs) for _, tabs in grouped)} tab(s)):")
    for representative, tabs in grouped:
        print(f"  {_format_pr(representative)}")
        for tab in tabs:
            print(f"    {tab.url}")
            tab_ids.append(tab.tab_id)
    return tab_ids


def _close_tabs(tab_ids: list[str]) -> None:
    unique_ids = sorted(set(tab_ids), key=int, reverse=True)
    for tab_id in iterate_with_progress(
        unique_ids,
        desc="Closing tabs",
        unit="tab",
    ):
        try:
            close_tab(tab_id)
        except ChromeCliError as exc:
            log_status(f"Failed to close tab {tab_id}: {exc}")


def _open_tabs(urls: list[str]) -> None:
    for url in iterate_with_progress(
        urls,
        desc="Opening tabs",
        unit="tab",
    ):
        try:
            open_tab(url)
        except ChromeCliError as exc:
            log_status(f"Failed to open tab {url}: {exc}")


def _listing_url_from_args(args: argparse.Namespace) -> str | None:
    url = (args.my_reviews or "").strip()
    if url:
        return url
    if sys.stdin.isatty():
        return input("Public pulls listing URL: ").strip() or None
    line = sys.stdin.readline()
    if not line:
        return None
    return line.strip() or None


def _run_my_reviews(args: argparse.Namespace) -> int:
    listing_url = _listing_url_from_args(args)
    if not listing_url:
        print("Error: No listing URL provided.", file=sys.stderr)
        return 1

    normalized = normalize_listing_url(listing_url)
    if normalized != listing_url.rstrip("/"):
        log_status(f"Using pulls listing: {normalized}")
    listing_url = normalized

    reviewer_names = _reviewer_names(args, None)
    if not reviewer_names:
        print(
            "No reviewer name found. Set git config user.name or pass --reviewer.",
            file=sys.stderr,
        )
        return 1

    log_status(f"Reviewers: {', '.join(reviewer_names)}")
    log_status(f"Fetching PR listing: {listing_url}")

    try:
        pr_urls, fetch_error, pages_fetched, listed_count = collect_prs_to_open(
            listing_url,
            reviewer_names,
            parallel=not args.no_parallel,
        )
    except Exception as exc:
        print(f"Lightpanda error: {exc}", file=sys.stderr)
        return 1

    if fetch_error:
        print(f"Error: {fetch_error}", file=sys.stderr)
        return 1

    if listed_count:
        log_status(
            f"Listed {listed_count} open PR(s) from {pages_fetched} page(s)."
        )

    if not pr_urls:
        log_status("No open PRs need your review on that listing.")
        return 0

    try:
        open_keys = {tab_url_key(tab.url) for tab in list_tabs()}
    except ChromeCliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    to_open = [url for url in pr_urls if tab_url_key(url) not in open_keys]
    skipped = len(pr_urls) - len(to_open)

    print(f"Need review ({len(pr_urls)}):")
    for url in pr_urls:
        suffix = " (already open)" if tab_url_key(url) in open_keys else ""
        print(f"  {url}{suffix}")

    if skipped:
        log_status(f"Skipping {skipped} already-open tab(s).")

    if not to_open:
        log_status("Nothing to open.")
        return 0

    if args.dry_run:
        log_status(f"Would open {len(to_open)} tab(s).")
        return 0

    _open_tabs(to_open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-helper",
        description=(
            "Find GitHub/GitLab PR tabs in Chrome, close duplicates, "
            "merged, closed, or draft PRs, and PRs you have already reviewed."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without closing tabs",
    )
    parser.add_argument(
        "--reviewer",
        action="append",
        metavar="NAME",
        help=(
            "Reviewer name or username to match (default: git config user.name "
            "and email local-part)"
        ),
    )
    parser.add_argument(
        "--dedupe-only",
        action="store_true",
        help=(
            "Only close duplicate, merged, closed, and draft PR tabs; "
            "skip review-status checks"
        ),
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Check PR status one at a time instead of in parallel",
    )
    parser.add_argument(
        "--my-reviews",
        metavar="URL",
        nargs="?",
        const="",
        help=(
            "Open PRs/MRs from a public pulls listing that still need your review; "
            "prompts for URL when omitted"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.my_reviews is not None:
        return _run_my_reviews(args)

    try:
        log_status("Scanning Chrome tabs...")
        prs = _collect_pr_tabs()
    except ChromeCliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not prs:
        log_status("No GitHub/GitLab PR or MR tabs found.")
        return 0

    reviewer_names = _reviewer_names(args, prs)
    if not reviewer_names and not args.dedupe_only:
        print(
            "No reviewer name found. Set git config user.name or pass --reviewer.",
            file=sys.stderr,
        )
        return 1

    if reviewer_names:
        log_status(f"Reviewers: {', '.join(reviewer_names)}")

    duplicate_tab_ids, _kept_by_key = _dedupe_tabs(prs)
    log_status(f"Found {len(prs)} PR/MR tab(s), {len(_kept_by_key)} unique")
    duplicate_ids = set(duplicate_tab_ids)
    reviewed_ids: set[str] = set()
    merged_ids: set[str] = set()
    closed_ids: set[str] = set()
    draft_ids: set[str] = set()

    names_for_check = [] if args.dedupe_only else reviewer_names
    try:
        pr_status = check_pr_statuses(
            prs,
            names_for_check,
            parallel=not args.no_parallel,
        )
    except Exception as exc:
        print(f"Lightpanda error: {exc}", file=sys.stderr)
        pr_status = {}

    merged_keys: set[tuple] = set()
    closed_keys: set[tuple] = set()
    draft_keys: set[tuple] = set()
    reviewed_keys: set[tuple] = set()
    for key, status in pr_status.items():
        if status.merged:
            merged_keys.add(key)
        elif status.closed:
            closed_keys.add(key)
        elif status.draft:
            draft_keys.add(key)
        elif status.reviewed:
            reviewed_keys.add(key)

    for pr in prs:
        if pr.key in merged_keys:
            merged_ids.add(pr.tab_id)
        elif pr.key in closed_keys:
            closed_ids.add(pr.tab_id)
        elif pr.key in draft_keys:
            draft_ids.add(pr.tab_id)
        elif pr.key in reviewed_keys:
            reviewed_ids.add(pr.tab_id)

    duplicate_ids -= merged_ids
    duplicate_ids -= closed_ids
    duplicate_ids -= draft_ids
    duplicate_ids -= reviewed_ids

    if (
        not duplicate_ids
        and not reviewed_ids
        and not merged_ids
        and not closed_ids
        and not draft_ids
    ):
        log_status("Nothing to close.")
        return 0

    if args.dry_run:
        log_status("Dry run — no tabs will be closed.")

    tabs_to_close = _print_sections(
        prs,
        [
            ("Duplicates", duplicate_ids),
            ("Merged", merged_ids),
            ("Closed", closed_ids),
            ("Draft", draft_ids),
            ("Already reviewed", reviewed_ids),
        ],
    )

    if args.dry_run:
        if closed_ids:
            log_status(
                f"Would close {len(closed_ids)} tab(s) for "
                f"{len(closed_keys)} closed PR(s)/MR(s)."
            )
        if draft_ids:
            log_status(
                f"Would close {len(draft_ids)} tab(s) for "
                f"{len(draft_keys)} draft PR(s)/MR(s)."
            )

    if not args.dry_run and tabs_to_close:
        _close_tabs(tabs_to_close)

    return 0
