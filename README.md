# Market Monitor

Personal market monitoring project for scheduled stock/index snapshots, emergency market-move alerts, and daily market-calendar digests sent to a phone through ntfy.

## What It Does

- Sends routine market snapshots for major indexes and a watchlist.
- Sends urgent alerts when indexes or watched stocks move beyond configured thresholds.
- Writes local markdown reports for snapshots and market-calendar windows.
- Supports ntfy push notifications, including default and urgent priorities.
- Is structured to run locally or from GitHub Actions when the Mac is asleep/off.

## Why GitHub Actions Works For This

GitHub Actions can run workflows when events happen or at scheduled times. GitHub’s docs describe the `schedule` event using cron syntax, with scheduled workflows running on the latest commit on the default branch. GitHub also supports repository secrets, which is where the private ntfy topic should live.

Useful docs:

- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets

## Project Layout

```text
.
├── .github/workflows/market-monitor.yml
├── config/
│   ├── config.example.json
│   ├── config.github.json
│   └── watchlist.txt
├── docs/
│   └── local-setup.md
├── src/market_monitor/
│   ├── __init__.py
│   └── cli.py
├── tests/
├── market-monitor
└── pyproject.toml
```

`config/config.local.json` is ignored by git and can contain your private ntfy topic for local runs.

## Local Usage

```bash
./market-monitor notify --config config/config.local.json
./market-monitor emergency-check --config config/config.local.json
./market-monitor calendar-notify --config config/config.local.json
```

Add personal tickers in `config/watchlist.txt`, one ticker per line.

## GitHub Setup

1. Create a GitHub repository and push this project.
2. Add a repository secret named `NTFY_TOPIC`.
3. Set the secret value to your ntfy topic.
4. Enable GitHub Actions.
5. Manually run the workflow once from the Actions tab with command `notify`.

The workflow uses `config/config.github.json`, which has ntfy enabled but keeps the topic empty. At runtime, the script reads `NTFY_TOPIC` from GitHub Secrets.

For AI features, add another repository secret:

```text
OPENAI_API_KEY
```

You can optionally add a repository variable:

```text
OPENAI_MODEL=gpt-5.4-mini
```

Manual API test:

```text
Actions → Market Monitor → Run workflow → command: ai-smoke-test
```

First market-close digest test:

```text
Actions → Market Monitor → Run workflow → command: close-digest
```

## Current Watchlist

Indexes:

```text
SPY, QQQ, VTI, DIA, IWM
```

Watchlist:

```text
GLD, VXUS, NVDA, BRK.A, BYD, TSLA, SOXL, AAPL, MSFT, AMZN, GOOGL, META, AVGO, JPM, LLY
```

Ticker aliases:

- `BRK.A` fetches as `BRK-A`.
- `BYD` fetches as `BYDDY`, the BYD Company U.S. ADR.

## Next Step: AI Market-Close Digest

The first market-close digest is available as:

```bash
./market-monitor close-digest --config config/config.local.json
```

It currently uses quotes, threshold alerts, and the local market-calendar window. News search is intentionally left out of the first test so the API and notification path stay easy to validate.

The next version should improve:

- what happened today
- likely drivers from current news
- important events to watch tomorrow
- notable watchlist moves

That needs a reliable AI/news provider decision. The project is now structured so that module can be added cleanly without mixing it into the basic quote-alert code.
