"""Wrapper around chrome-cli for listing and closing tabs."""

from __future__ import annotations

import re
import shutil
import subprocess
import time

from review_helper.pr_urls import ChromeTab, parse_tablink

_TAB_ID_RE = re.compile(r"^Id:\s*(\d+)", re.MULTILINE)
_LOADING_RE = re.compile(r"Loading:\s*(Yes|No)", re.IGNORECASE)


class ChromeCliError(RuntimeError):
    pass


def _chrome_cli_path() -> str:
    path = shutil.which("chrome-cli")
    if not path:
        raise ChromeCliError(
            "chrome-cli not found. macOS: brew install chrome-cli "
            "(see README for Fedora notes)"
        )
    return path


def _run_chrome_cli(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [_chrome_cli_path(), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def list_tabs() -> list[ChromeTab]:
    result = _run_chrome_cli("list", "tablinks")
    if result.returncode != 0:
        raise ChromeCliError(result.stderr.strip() or result.stdout.strip())

    tabs: list[ChromeTab] = []
    for line in result.stdout.splitlines():
        tab = parse_tablink(line)
        if tab:
            tabs.append(tab)
    return tabs


def open_tab(url: str) -> str:
    result = _run_chrome_cli("open", url)
    if result.returncode != 0:
        raise ChromeCliError(
            f"Failed to open {url}: {result.stderr.strip() or result.stdout.strip()}"
        )
    match = _TAB_ID_RE.search(result.stdout)
    if not match:
        raise ChromeCliError(f"Failed to read tab id after opening {url}")
    return match.group(1)


def wait_for_tab_load(tab_id: str, *, timeout: float = 25) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run_chrome_cli("info", "-t", tab_id)
        if result.returncode != 0:
            raise ChromeCliError(
                f"Failed to read tab {tab_id}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        match = _LOADING_RE.search(result.stdout)
        if match and match.group(1).lower() == "no":
            time.sleep(1.5)
            return
        time.sleep(0.5)
    time.sleep(2)


def close_tab(tab_id: str) -> None:
    result = _run_chrome_cli("close", "-t", tab_id)
    if result.returncode != 0:
        raise ChromeCliError(
            f"Failed to close tab {tab_id}: {result.stderr.strip() or result.stdout.strip()}"
        )
