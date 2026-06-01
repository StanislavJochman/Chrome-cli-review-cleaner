"""Check PR/MR review and merge status using Lightpanda headless browser."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urldefrag

import lightpanda

from review_helper.pr_urls import PullRequestRef

LOGIN_HINTS = (
    "sign in to github",
    "sign in to gitlab",
    "you must be logged in",
)

REVIEW_URL_MARKERS = (
    "#pullrequestreview-",
    "#discussion_r",
    "#note_",
)

REVIEW_MARKERS = (
    "approved these changes",
    "requested changes",
    "left review comments",
    "approved this merge request",
    "approved by",
    "reviewed",
)

MERGED_MARKERS = {
    "github": (
        " merged this pull request",
        "was merged",
        "successfully merged and closed",
        "pull request successfully merged",
        " merged ",
        "** merged **",
        "state merged",
    ),
    "gitlab": (
        "merged by",
        "was merged",
        "state merged",
        "status: merged",
        "status merged",
        "merge request was merged",
    ),
}

NOT_MERGED_MARKERS = (
    "ready to merge",
    "can be merged",
    "merge conflicts",
    "awaiting merge",
    "not merged",
    "open merge request",
)

FETCH_WAIT_MS = 8000
FETCH_RETRY_WAIT_MS = 12000
MIN_COMPLETE_TEXT_LEN = 25_000


@dataclass(frozen=True, slots=True)
class PrStatus:
    reviewed: bool = False
    merged: bool = False
    detail: str = ""


def _name_matches_review(text: str, names: list[str]) -> bool:
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
            snippet = lower[idx : idx + 160]
            start = idx + 1
            if "awaiting requested review" in snippet or "awaiting review" in snippet:
                continue
            if any(marker in snippet for marker in REVIEW_MARKERS):
                return True
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
    markers = MERGED_MARKERS.get(platform, ())
    if not any(marker in lower for marker in markers):
        return False
    if any(marker in lower for marker in NOT_MERGED_MARKERS):
        strong = (
            " merged this pull request",
            "was merged",
            "successfully merged",
            "merged by",
            "merge request was merged",
            "** merged **",
        )
        return any(marker in lower for marker in strong)
    return True


def _looks_like_login_page(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in LOGIN_HINTS)


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


def _fetch_page_text(url: str, *, wait_ms: int = FETCH_WAIT_MS) -> str:
    best = ""
    waits = [wait_ms]
    if wait_ms < FETCH_RETRY_WAIT_MS:
        waits.append(FETCH_RETRY_WAIT_MS)

    for current_wait in waits:
        for dump in ("markdown", "html"):
            try:
                response = lightpanda.fetch(url, dump=dump, wait_ms=current_wait)
                text = response.text if dump == "markdown" else _text_from_html(response.text)
            except Exception:
                continue
            if len(text) > len(best):
                best = text
            if len(text) >= MIN_COMPLETE_TEXT_LEN:
                return text
    return best


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
        add(tab.canonical_url, 10)
        add(tab.url, 20 if any(m in tab.url for m in REVIEW_URL_MARKERS) else 15)

    scored.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored]


def _status_from_text(text: str, platform: str, reviewer_names: list[str]) -> PrStatus:
    if not text:
        return PrStatus(detail="fetch-failed")
    if _looks_like_login_page(text):
        return PrStatus(detail="login-required")
    return PrStatus(
        reviewed=_name_matches_review(text, reviewer_names),
        merged=_is_merged_text(text, platform),
    )


def check_pr_url(
    url: str,
    platform: str,
    reviewer_names: list[str],
    *,
    wait_ms: int = FETCH_WAIT_MS,
) -> PrStatus:
    text = _fetch_page_text(url, wait_ms=wait_ms)
    return _status_from_text(text, platform, reviewer_names)


def check_pr_tabs(tabs: list[PullRequestRef], reviewer_names: list[str]) -> PrStatus:
    platform = tabs[0].platform
    if any(_is_merged_title(tab.title, platform) for tab in tabs):
        return PrStatus(merged=True)

    best = PrStatus()
    for url in _urls_to_check(tabs):
        status = check_pr_url(url, platform, reviewer_names)
        if status.detail and not best.detail:
            best = PrStatus(detail=status.detail)
        if status.merged:
            best = PrStatus(reviewed=best.reviewed or status.reviewed, merged=True)
            if status.reviewed:
                return best
        if status.reviewed:
            best = PrStatus(reviewed=True, merged=best.merged)
    return best


def recheck_pr_tabs(tabs: list[PullRequestRef], reviewer_names: list[str]) -> PrStatus:
    """Sequential recheck with a longer wait for one best URL."""
    platform = tabs[0].platform
    urls = _urls_to_check(tabs)
    if not urls:
        return PrStatus()
    return check_pr_url(
        urls[0],
        platform,
        reviewer_names,
        wait_ms=FETCH_RETRY_WAIT_MS,
    )


def is_inconclusive(status: PrStatus) -> bool:
    if status.merged or status.reviewed:
        return False
    return status.detail != "login-required"
