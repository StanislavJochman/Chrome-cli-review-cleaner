"""Wrapper around chrome-cli for listing and closing tabs."""

from __future__ import annotations

import shutil
import subprocess

from review_helper.pr_urls import ChromeTab, parse_tablink


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


def list_tabs() -> list[ChromeTab]:
    cmd = [_chrome_cli_path(), "list", "tablinks"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ChromeCliError(result.stderr.strip() or result.stdout.strip())

    tabs: list[ChromeTab] = []
    for line in result.stdout.splitlines():
        tab = parse_tablink(line)
        if tab:
            tabs.append(tab)
    return tabs


def close_tab(tab_id: str) -> None:
    cmd = [_chrome_cli_path(), "close", "-t", tab_id]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ChromeCliError(
            f"Failed to close tab {tab_id}: {result.stderr.strip() or result.stdout.strip()}"
        )
