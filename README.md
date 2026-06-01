# Chrome review CLI cleaner

![Dozens of open GitHub and GitLab PR tabs in Chrome](docs/pr-tabs.png)

If you review pull requests regularly, your browser probably looks like this — a row of GitHub and GitLab tabs with no room left for titles. The same PR open twice, a `/changes` view next to the main page, tabs for PRs you approved last week, and tabs for PRs that already merged.

**Chrome review CLI cleaner** cleans that up in one command. It scans your open Chrome tabs, finds GitHub and GitLab PR/MR URLs (including self-hosted instances), checks whether each PR is merged or already reviewed by you, and closes the tabs you no longer need.

- **chrome-cli** — list and close tabs in your running Chrome
- **Lightpanda** (`lightpanda-py`) — fast headless scraping of PR review status

## Requirements

- Google Chrome or a Chromium-based browser
- Python 3.10+
- [chrome-cli](https://github.com/prasmussen/chrome-cli) for tab control (macOS)

### macOS

Install Chrome and chrome-cli:

```bash
brew install chrome-cli
```

Using Brave, Edge, or another Chromium browser? Set `CHROME_BUNDLE_IDENTIFIER` (see [Environment](#environment)).

### Fedora

Install Chrome and Python:

```bash
# Google Chrome (recommended)
sudo dnf install fedora-workstation-repositories
sudo dnf config-manager --set-enabled google-chrome
sudo dnf install google-chrome-stable python3 python3-pip

# Or Chromium from Fedora repos
sudo dnf install chromium python3 python3-pip
```

Tab control uses [chrome-cli](https://github.com/prasmussen/chrome-cli), which relies on macOS Scripting Bridge and runs on **macOS only**. Install and run `review-helper` on a Mac to list and close tabs. The Python package and Lightpanda scraping work on Fedora for development and testing.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Lightpanda's browser binary is bundled in `lightpanda-py`. No other browser install is needed for scraping.

## Usage

Preview:

```bash
review-helper --dry-run
```

Run:

```bash
review-helper
```

If your GitHub username differs from `git config user.name`:

```bash
review-helper --reviewer CustomUsername --reviewer custom_username
```

Only deduplicate (skip review checks):

```bash
review-helper --dedupe-only
```

Serial mode (useful when many tabs fail to load in parallel):

```bash
review-helper --no-parallel
```

Open PRs from a public repo listing that still need your review:

```bash
review-helper --my-reviews
# Public pulls listing URL: https://github.com/konflux-ci/konflux-ui/

review-helper --my-reviews https://github.com/konflux-ci/konflux-ui/pulls
review-helper --dry-run --my-reviews
```

You can paste a repo URL (`https://github.com/org/repo/`) or a pulls page — repo URLs are automatically converted to `/pulls`.

Lightpanda fetches the listing and each PR page, keeps only those where you are a requested reviewer and have not reviewed yet, then opens them in Chrome.

Works with GitHub review-requested pages and GitLab merge-request dashboards filtered by `reviewer_username`.

Output lists PRs being closed, grouped by duplicates, merged, and already reviewed.

## What it does

1. Lists Chrome tabs via `chrome-cli`
2. Finds GitHub/GitLab PRs and MRs (including self-hosted)
3. Closes duplicate tabs, keeping the best URL per PR
4. Scrapes each unique PR with Lightpanda to detect merged or already-reviewed status
5. Closes all tabs for merged and reviewed PRs

Reviewer names come from all `~/.gitconfig*` files (`user.name`, `user.email`, `github.user`).

Private repos that require login cannot be scraped by Lightpanda and are left open.

## Environment

- `CHROME_BUNDLE_IDENTIFIER` — non-Chrome Chromium browser on macOS (e.g. `com.brave.Browser`)
