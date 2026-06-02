"""Check PR/MR review and merge status using Lightpanda headless browser."""

from __future__ import annotations

import html
import re
import threading
from dataclasses import dataclass
from urllib.parse import urldefrag

import lightpanda

from review_helper.pr_urls import PullRequestRef

_FETCH_SEM = threading.Semaphore(8)

LOGIN_HINTS = (
    "sign in to github",
    "sign in to gitlab",
    "you must be logged in",
)

RATE_LIMIT_HINTS = (
    "rate limit",
    "access has been restricted",
    "too many requests",
    "please wait a few minutes",
)

REVIEW_URL_MARKERS = (
    "#pullrequestreview-",
    "#discussion_r",
    "#note_",
)

REVIEW_ACTION_MARKERS = (
    "approved these changes",
    "requested changes",
    "left review comments",
    "approved this merge request",
)

GITHUB_STATE_AFTER_NUMBER_RE = re.compile(
    r"#\d+\s*\n+(open|closed|draft|merged)\b",
    re.IGNORECASE,
)

GITLAB_STATE_RE = re.compile(
    r"status:\s*(open|closed|merged|draft)\b",
    re.IGNORECASE,
)

REVIEW_REQUESTED_MARKERS = (
    "awaiting review from",
    "awaiting requested review from",
    "review requested from",
    "requested your review",
    "your review was requested",
    "review requested",
)

MERGED_MARKERS = {
    "github": (
        " merged this pull request",
        "merged 1 commit into",
        "was merged",
        "successfully merged and closed",
        "pull request successfully merged",
        "merged into",
        "** merged **",
    ),
    "gitlab": (
        "merged by",
        "was merged",
        "merge request was merged",
        "status: merged",
    ),
}

STRONG_MERGED_MARKERS = (
    " merged this pull request",
    " merged this merge request",
    "merged 1 commit into",
    "pull request successfully merged",
    "successfully merged and closed",
    "merge request was merged",
    "** merged **",
)

CLOSED_MARKERS = {
    "github": (
        " closed this pull request",
        "this pull request was closed",
        "pull request was closed",
        "closed without merging",
        "closed this without merging",
        "** closed **",
    ),
    "gitlab": (
        "closed the merge request",
        "merge request was closed",
        "status: closed",
        "** closed **",
    ),
}

STRONG_CLOSED_MARKERS = (
    " closed this pull request",
    " closed this merge request",
    " closed this",
    "closed this without merging",
    "closed the merge request",
    "merge request was closed",
    "this pull request was closed",
    "** closed **",
)

NOT_MERGED_MARKERS = (
    "ready to merge",
    "can be merged",
    "merge conflicts",
    "awaiting merge",
    "not merged",
    "open merge request",
    "open pull request",
)

FETCH_WAIT_MS = 5000
FETCH_RETRY_WAIT_MS = 10000
FETCH_RECHECK_WAIT_MS = 15000

OPEN_MARKERS = (
    "open pull request",
    "open merge request",
    "ready for review",
    "convert to draft",
)


@dataclass(frozen=True, slots=True)
class PrStatus:
    reviewed: bool = False
    merged: bool = False
    closed: bool = False
    draft: bool = False
    detail: str = ""


def _pr_state_from_text(text: str, platform: str) -> str | None:
    """Return open, closed, draft, or merged from the PR/MR state badge when present."""
    header = text[:8000]
    if platform == "github":
        match = GITHUB_STATE_AFTER_NUMBER_RE.search(header)
        if match:
            return match.group(1).lower()
    elif platform == "gitlab":
        match = GITLAB_STATE_RE.search(header)
        if match:
            return match.group(1).lower()
    return None


def _name_matches_review(text: str, names: list[str]) -> bool:
    if not names:
        return False
    lower = text.lower()
    for name in names:
        nl = re.escape(name.lower())
        actor = rf"(?:\*\*{nl}\*\*|{nl})"
        if re.search(rf"{actor}\s+reviewed\b", lower):
            return True
        for marker in REVIEW_ACTION_MARKERS:
            if re.search(rf"{actor}\s+{re.escape(marker)}", lower):
                return True
    return False


def platform_for_url(url: str) -> str:
    if "/-/merge_requests/" in url:
        return "gitlab"
    return "github"


def name_matches_review_requested(text: str, names: list[str]) -> bool:
    if not names:
        return False
    lower = text.lower()
    for name in names:
        nl = name.lower()
        start = 0
        while True:
            idx = lower.find(nl, start)
            if idx == -1:
                break
            snippet = lower[max(0, idx - 80) : idx + 180]
            start = idx + 1
            if any(marker in snippet for marker in REVIEW_REQUESTED_MARKERS):
                return True
    return False


def pr_needs_user_review(url: str, reviewer_names: list[str]) -> bool:
    platform = platform_for_url(url)
    for wait_ms in (FETCH_WAIT_MS, FETCH_RETRY_WAIT_MS):
        text = _fetch_page_text(url, wait_ms=wait_ms)
        if not text or _looks_like_login_page(text):
            continue
        if looks_like_rate_limit(text):
            return False
        if _is_merged_text(text, platform):
            return False
        if _is_closed_text(text, platform):
            return False
        state = _pr_state_from_text(text, platform)
        if state == "draft":
            return False
        if _name_matches_review(text, reviewer_names):
            return False
        if name_matches_review_requested(text, reviewer_names):
            return True
        if _page_looks_complete(text, platform):
            return False
    return False


def _is_merged_title(title: str, platform: str) -> bool:
    lower = title.lower()
    if platform == "github":
        return "· merged" in lower or lower.startswith("merged ")
    if platform == "gitlab":
        return "merged" in lower and "merge requests" in lower
    return False


def _is_merged_text(text: str, platform: str) -> bool:
    lower = text.lower()
    if any(marker in lower for marker in STRONG_MERGED_MARKERS):
        return True
    if any(marker in lower for marker in NOT_MERGED_MARKERS):
        return False
    return any(marker in lower for marker in MERGED_MARKERS.get(platform, ()))


def _is_closed_title(title: str, platform: str) -> bool:
    if _is_merged_title(title, platform):
        return False
    lower = title.lower()
    if platform == "github":
        return "· closed" in lower or lower.startswith("closed ")
    if platform == "gitlab":
        return "closed" in lower and "merge request" in lower
    return False


def _is_closed_text(text: str, platform: str) -> bool:
    state = _pr_state_from_text(text, platform)
    if state == "open":
        return False
    if state == "closed":
        return True
    if _is_merged_text(text, platform):
        return False
    lower = text.lower()
    if any(marker in lower for marker in STRONG_CLOSED_MARKERS):
        return True
    if any(marker in lower for marker in NOT_MERGED_MARKERS):
        return False
    return any(marker in lower for marker in CLOSED_MARKERS.get(platform, ()))


def _looks_like_login_page(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in LOGIN_HINTS)


def looks_like_rate_limit(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in RATE_LIMIT_HINTS)


def _text_from_html(page_html: str) -> str:
    without_scripts = re.sub(
        r"<script[^>]*>.*?</script>",
        " ",
        page_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    without_styles = re.sub(
        r"<style[^>]*>.*?</style>",
        " ",
        without_scripts,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = html.unescape(re.sub(r"<[^>]+>", " ", without_styles))
    return re.sub(r"\s+", " ", text)


def fetch_page_text(url: str, *, wait_ms: int) -> str:
    return _fetch_page_text(url, wait_ms=wait_ms)


def _fetch_page_text(url: str, *, wait_ms: int) -> str:
    with _FETCH_SEM:
        for dump in ("markdown", "html"):
            try:
                response = lightpanda.fetch(url, dump=dump, wait_ms=wait_ms)
                raw = response.text
                if looks_like_rate_limit(raw):
                    return raw
                text = raw if dump == "markdown" else _text_from_html(raw)
                if len(text) > 500:
                    return text
            except Exception:
                continue
    return ""


def _urls_to_check(tabs: list[PullRequestRef]) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    def add(url: str, score: int) -> None:
        normalized = urldefrag(url)[0]
        if url in seen:
            return
        seen.add(url)
        scored.append((score, url))
        if normalized != url and normalized not in seen:
            seen.add(normalized)
            scored.append((score - 1, normalized))

    for tab in tabs:
        add(tab.canonical_url, 20)
        review_url = any(m in tab.url for m in REVIEW_URL_MARKERS)
        add(tab.url, 12 if review_url else 15)

    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored]


def _page_looks_complete(text: str, platform: str) -> bool:
    lower = text.lower()
    if len(text) >= 8000:
        return True
    if platform == "github" and "pull request" in lower:
        return True
    if platform == "gitlab" and "merge request" in lower:
        return True
    return any(marker in lower for marker in OPEN_MARKERS)


def _status_from_text(text: str, platform: str, reviewer_names: list[str]) -> PrStatus:
    if not text:
        return PrStatus(detail="fetch-failed")
    if _looks_like_login_page(text):
        return PrStatus(detail="login-required")

    state = _pr_state_from_text(text, platform)
    if state == "merged":
        return PrStatus(merged=True)
    if state == "closed":
        return PrStatus(closed=True)
    if state == "draft":
        return PrStatus(draft=True)

    merged = _is_merged_text(text, platform)
    if merged:
        return PrStatus(merged=True)
    closed = _is_closed_text(text, platform)
    if closed:
        return PrStatus(closed=True)
    reviewed = _name_matches_review(text, reviewer_names)
    if reviewed:
        return PrStatus(reviewed=True)

    if _page_looks_complete(text, platform):
        return PrStatus()

    return PrStatus(detail="incomplete-page")


def check_pr_url(
    url: str,
    platform: str,
    reviewer_names: list[str],
    *,
    wait_times: tuple[int, ...] | None = None,
) -> PrStatus:
    waits = wait_times or (FETCH_WAIT_MS, FETCH_RETRY_WAIT_MS)
    last = PrStatus(detail="fetch-failed")
    for wait_ms in waits:
        text = _fetch_page_text(url, wait_ms=wait_ms)
        status = _status_from_text(text, platform, reviewer_names)
        last = status
        if status.merged or status.closed or status.draft or status.reviewed:
            return status
        if status.detail == "login-required":
            return status
    return last


def _is_draft_title(title: str, platform: str) -> bool:
    if _is_merged_title(title, platform) or _is_closed_title(title, platform):
        return False
    lower = title.lower()
    if platform == "github":
        return "· draft" in lower or lower.startswith("draft ")
    if platform == "gitlab":
        return "draft" in lower and "merge request" in lower
    return False


def check_pr_tabs(
    tabs: list[PullRequestRef],
    reviewer_names: list[str],
    *,
    wait_times: tuple[int, ...] | None = None,
) -> PrStatus:
    platform = tabs[0].platform
    if any(_is_merged_title(tab.title, platform) for tab in tabs):
        return PrStatus(merged=True)
    if any(_is_closed_title(tab.title, platform) for tab in tabs):
        return PrStatus(closed=True)
    if any(_is_draft_title(tab.title, platform) for tab in tabs):
        return PrStatus(draft=True)

    last = PrStatus(detail="fetch-failed")
    for url in _urls_to_check(tabs):
        status = check_pr_url(
            url,
            platform,
            reviewer_names,
            wait_times=wait_times,
        )
        last = status
        if status.merged or status.closed or status.draft or status.reviewed:
            return status
        if status.detail == "login-required":
            return status
    return last


def needs_recheck(status: PrStatus) -> bool:
    return status.detail in ("fetch-failed", "incomplete-page")
