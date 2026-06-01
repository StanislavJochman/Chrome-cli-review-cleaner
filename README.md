# review-helper

Close duplicate and already-reviewed GitHub/GitLab PR tabs in Chrome.

- **chrome-cli** — list and close tabs in your running Chrome
- **Lightpanda** (`lightpanda-py`) — fast headless scraping of PR review status

## Requirements

- macOS with Google Chrome
- [chrome-cli](https://github.com/prasmussen/chrome-cli): `brew install chrome-cli`
- Python 3.10+

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Lightpanda's browser binary is bundled in `lightpanda-py`. No other browser install needed.

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

Only deduplicate:

```bash
review-helper --dedupe-only
```

Output lists only duplicate and already-reviewed PRs being closed.

## What it does

1. Lists Chrome tabs via `chrome-cli`
2. Finds GitHub/GitLab PRs (including self-hosted)
3. Closes duplicate tabs, keeping one per PR number
4. Scrapes each unique PR with Lightpanda to detect if you already reviewed it
5. Closes all tabs for reviewed PRs

Private repos that require login cannot be scraped by Lightpanda and are skipped silently.

## Environment

- `CHROME_BUNDLE_IDENTIFIER` — non-Chrome browser (e.g. `com.brave.Browser`)
