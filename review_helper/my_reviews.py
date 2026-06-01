"""Fetch public PR listings and open PRs that need the user's review."""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import lightpanda

from review_helper.pr_urls import extract_pr_urls_from_text
from review_helper.progress import map_with_progress
from review_helper.review import (
    FETCH_RECHECK_WAIT_MS,
    _FETCH_SEM,
    _looks_like_login_page,
    fetch_page_text,
    looks_like_rate_limit,
    pr_needs_user_review,
)

MAX_LISTING_PAGES = 50
LISTING_FETCH_WAITS_MS = (FETCH_RECHECK_WAIT_MS, 20000, 30000)
LISTING_PAGE_DELAY_S = 2
MY_REVIEWS_MAX_WORKERS = 4

RATE_LIMIT_MSG = (
    "GitHub rate limit hit — wait a few minutes, then retry with --no-parallel."
)

GITHUB_LISTING_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pulls",
    re.IGNORECASE,
)

GITLAB_LISTING_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<project>.+?)/-/merge_requests",
    re.IGNORECASE,
)

GITHUB_REPO_ROOT_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/?$",
    re.IGNORECASE,
)

GITLAB_PROJECT_ROOT_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<project>.+)/?$",
    re.IGNORECASE,
)


def normalize_listing_url(url: str) -> str:
    clean = url.strip()
    if not clean:
        return clean

    github_repo = GITHUB_REPO_ROOT_RE.match(clean.rstrip("/"))
    if github_repo:
        return (
            f"https://{github_repo.group('host')}/{github_repo.group('owner')}/"
            f"{github_repo.group('repo')}/pulls"
        )

    parsed = urlparse(clean)
    if "/-/merge_requests" not in parsed.path:
        gitlab_project = GITLAB_PROJECT_ROOT_RE.match(clean.rstrip("/"))
        if gitlab_project and "github" not in gitlab_project.group("host").lower():
            return (
                f"https://{gitlab_project.group('host')}/"
                f"{gitlab_project.group('project')}/-/merge_requests"
            )

    return clean


def _listing_page_url(start_url: str, page: int) -> str:
    parts = list(urlparse(start_url))
    query = parse_qs(parts[4], keep_blank_values=True)
    query["page"] = [str(page)]
    parts[4] = urlencode(query, doseq=True)
    return urlunparse(parts)


def _pr_urls_for_listing(listing_url: str, pr_urls: list[str]) -> list[str]:
    github = GITHUB_LISTING_RE.match(listing_url)
    if github:
        prefix = (
            f"https://{github.group('host')}/{github.group('owner')}/"
            f"{github.group('repo')}/pull/"
        )
        return [url for url in pr_urls if url.startswith(prefix)]

    gitlab = GITLAB_LISTING_RE.match(listing_url)
    if gitlab:
        prefix = (
            f"https://{gitlab.group('host')}/{gitlab.group('project')}"
            f"/-/merge_requests/"
        )
        return [url for url in pr_urls if url.startswith(prefix)]

    return pr_urls


def _fetch_listing_page(url: str, *, host: str) -> tuple[str, str]:
    for attempt, wait_ms in enumerate(LISTING_FETCH_WAITS_MS):
        with _FETCH_SEM:
            try:
                response = lightpanda.fetch(url, dump="html", wait_ms=wait_ms)
                raw = response.text
            except Exception:
                raw = ""

        if looks_like_rate_limit(raw):
            if attempt + 1 < len(LISTING_FETCH_WAITS_MS):
                time.sleep(15 * (attempt + 1))
                continue
            return "", RATE_LIMIT_MSG

        if raw and extract_pr_urls_from_text(raw, host=host):
            return raw, ""

        if attempt + 1 < len(LISTING_FETCH_WAITS_MS):
            time.sleep(5)

    text = fetch_page_text(url, wait_ms=LISTING_FETCH_WAITS_MS[-1])
    if looks_like_rate_limit(text):
        return "", RATE_LIMIT_MSG
    if text:
        return text, ""
    return "", "Could not fetch listing page."


def fetch_listing_pr_urls(listing_url: str) -> tuple[list[str], str, int]:
    host = urlparse(listing_url).netloc
    seen_prs: set[str] = set()
    pr_urls: list[str] = []
    pages_fetched = 0

    for page in range(1, MAX_LISTING_PAGES + 1):
        url = _listing_page_url(listing_url, page)
        text, error = _fetch_listing_page(url, host=host)
        if error:
            if page == 1:
                return [], error, 0
            break
        if _looks_like_login_page(text):
            if page == 1:
                return [], "Listing page requires login — use a public repo URL.", 0
            break

        page_prs = extract_pr_urls_from_text(text, host=host)
        new_prs = [pr for pr in page_prs if pr not in seen_prs]
        if not new_prs:
            break

        pages_fetched += 1
        for pr in new_prs:
            seen_prs.add(pr)
            pr_urls.append(pr)

        if page < MAX_LISTING_PAGES:
            time.sleep(LISTING_PAGE_DELAY_S)

    pr_urls = _pr_urls_for_listing(listing_url, pr_urls)
    return pr_urls, "", pages_fetched


def find_prs_needing_review(
    pr_urls: list[str],
    reviewer_names: list[str],
    *,
    parallel: bool = True,
) -> list[str]:
    def worker(url: str) -> tuple[str, bool]:
        return url, pr_needs_user_review(url, reviewer_names)

    checked = map_with_progress(
        worker,
        pr_urls,
        desc="Checking PRs",
        unit="pr",
        label=lambda url: url.rsplit("/", 1)[-1],
        parallel=parallel,
        max_workers=MY_REVIEWS_MAX_WORKERS if parallel else 1,
    )
    return [url for url, needs_review in checked if needs_review]


def collect_prs_to_open(
    listing_url: str,
    reviewer_names: list[str],
    *,
    parallel: bool = True,
) -> tuple[list[str], str, int, int]:
    pr_urls, error, pages_fetched = fetch_listing_pr_urls(listing_url)
    if error:
        return [], error, pages_fetched, 0
    if not pr_urls:
        return [], "", pages_fetched, 0

    needing_review = find_prs_needing_review(
        pr_urls,
        reviewer_names,
        parallel=parallel,
    )
    return needing_review, "", pages_fetched, len(pr_urls)
