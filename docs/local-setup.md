# Local Setup

These commands run the monitor from your Mac. For reliable delivery while the Mac is asleep or off, use GitHub Actions instead.

## First Run

```bash
cd /Users/zheyangchen/Documents/Codex/2026-06-09/i-would-like-you-to-help
./market-monitor report --config config/config.local.json
./market-monitor notify --config config/config.local.json --dry-run
```

`config/config.local.json` is ignored by git and can contain your private ntfy topic.

## Commands

```bash
./market-monitor report --config config/config.local.json
./market-monitor calendar --config config/config.local.json
./market-monitor notify --config config/config.local.json
./market-monitor emergency-check --config config/config.local.json
./market-monitor calendar-notify --config config/config.local.json
./market-monitor ai-smoke-test --config config/config.local.json --dry-run
./market-monitor close-digest --config config/config.local.json --dry-run
./market-monitor setup-launchd --config config/config.local.json
```

`notify` sends a normal-priority ntfy snapshot.

`emergency-check` checks for large moves during regular market hours only. Defaults:

- index emergency threshold: `2.0%`
- watchlist emergency threshold: `3.0%`

It stores local daily alert state in `outputs/alert_state.json` so the same emergency alert is not repeated all day on the same machine. GitHub Actions uses `state/alert_state.json` instead so state can persist across hosted runners.

## Watchlist

Core market indexes live in `indexes` inside `config/config.local.json`. The default set is:

```text
SPY, QQQ, VTI, DIA, IWM
```

Personal stocks can be added in either place:

```json
"watchlist": ["AAPL", "MSFT", "NVDA", "TSLA"]
```

or by adding one ticker per line to:

```text
config/watchlist.txt
```

## ntfy

The current phone notification channel is ntfy.

Local config may contain:

```json
"ntfy": {
  "enabled": true,
  "server": "https://ntfy.sh",
  "topic": "your-private-topic",
  "default_priority": "default",
  "emergency_priority": "urgent"
}
```

For GitHub Actions, store the topic as the `NTFY_TOPIC` repository secret instead.

## OpenAI API Test

Do not paste your API key into chat or commit it to git.

For a one-time local test, run this in your own terminal:

```bash
cd /Users/zheyangchen/Documents/Codex/2026-06-09/market_alerts
OPENAI_API_KEY="your_api_key_here" ./market-monitor ai-smoke-test --config config/config.local.json --dry-run
```

If that works, send the AI-generated test message to your phone:

```bash
OPENAI_API_KEY="your_api_key_here" ./market-monitor ai-smoke-test --config config/config.local.json
```

For GitHub Actions, add the key as the `OPENAI_API_KEY` repository secret.

## Data Sources

Quotes are fetched from Yahoo Finance's public chart endpoint first, then the older quote endpoint and Stooq CSV endpoint as fallbacks. These are public no-key endpoints rather than paid market-data feeds, so the script reports fetch errors instead of using stale data.

FOMC dates are parsed from the Federal Reserve's official meeting calendar:

https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
