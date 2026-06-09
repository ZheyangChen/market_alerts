#!/usr/bin/env python3
"""
Small stock-market monitor.

It produces three kinds of output:
- a market snapshot for a configured watchlist
- a recent/upcoming event calendar
- optional macOS notifications
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import platform
import re
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.local.json"
FED_FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class Event:
    date: dt.date
    title: str
    impact: str = "medium"
    source: str = "generated"


def unique_symbols(symbols: list[str]) -> list[str]:
    seen = set()
    result = []
    for symbol in symbols:
        clean = str(symbol).strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        example = path.with_name("config.example.json")
        raise SystemExit(
            f"Missing config file: {path}\n"
            f"Start by copying {example.name} to {path.name} and editing the symbols/events."
        )
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def output_dir(config: dict[str, Any], config_path: Path) -> Path:
    raw = config.get("output_dir", "../../outputs")
    path = Path(raw)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def symbols_from_file(config: dict[str, Any], config_path: Path) -> list[str]:
    raw = config.get("watchlist_file")
    if not raw:
        return []
    path = Path(str(raw))
    if not path.is_absolute():
        path = config_path.parent / path
    if not path.exists():
        return []
    symbols = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.split("#", 1)[0].strip()
        if clean:
            symbols.append(clean)
    return symbols


def configured_symbol_groups(config: dict[str, Any], config_path: Path) -> tuple[list[str], list[str]]:
    indexes = unique_symbols(config.get("indexes", []))
    watchlist = unique_symbols(config.get("watchlist", []) + symbols_from_file(config, config_path))
    legacy = [symbol for symbol in config.get("symbols", []) if symbol not in indexes and symbol not in watchlist]
    watchlist = unique_symbols(watchlist + legacy)
    return indexes, watchlist


def fetch_json(url: str, timeout: int = 15) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "market-monitor/1.0 (+local script)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "market-monitor/1.0 (+local script)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_quotes(symbols: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    if not symbols:
        return [], None
    chart_quotes, chart_error = fetch_yahoo_chart_quotes(symbols)
    if chart_quotes:
        return chart_quotes, None

    params = urllib.parse.urlencode({"symbols": ",".join(symbols)})
    url = f"{YAHOO_QUOTE_URL}?{params}"
    try:
        data = fetch_json(url)
        return data.get("quoteResponse", {}).get("result", []), None
    except Exception as exc:  # noqa: BLE001 - report fetch errors in the digest
        stooq_quotes, stooq_error = fetch_stooq_quotes(symbols)
        if stooq_quotes:
            return stooq_quotes, None
        return [], f"Yahoo chart {chart_error}; Yahoo quote {type(exc).__name__}: {exc}; Stooq {stooq_error}"


def symbol_aliases(config: dict[str, Any]) -> dict[str, str]:
    return {
        str(display).strip().upper(): str(fetch).strip().upper()
        for display, fetch in config.get("symbol_aliases", {}).items()
        if str(display).strip() and str(fetch).strip()
    }


def fetch_quotes_for_display(
    config: dict[str, Any], display_symbols: list[str]
) -> tuple[list[dict[str, Any]], str | None]:
    aliases = symbol_aliases(config)
    display_to_fetch = {symbol: aliases.get(symbol, symbol) for symbol in display_symbols}
    fetch_to_display = {fetch: display for display, fetch in display_to_fetch.items()}
    quotes, error = fetch_quotes(unique_symbols(list(display_to_fetch.values())))
    for quote in quotes:
        fetched = str(quote.get("symbol", "")).upper()
        display = fetch_to_display.get(fetched)
        if display:
            quote["symbol"] = display
            quote["dataSymbol"] = fetched
    return quotes, error


def fetch_yahoo_chart_quotes(symbols: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    quotes = []
    errors = []
    for symbol in symbols:
        url = f"{YAHOO_CHART_URL}/{urllib.parse.quote(symbol)}?range=1d&interval=5m"
        try:
            data = fetch_json(url)
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                errors.append(f"{symbol}: empty result")
                continue
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            previous = meta.get("previousClose") or meta.get("chartPreviousClose")
            pct = None
            if isinstance(price, (int, float)) and isinstance(previous, (int, float)) and previous:
                pct = ((price - previous) / previous) * 100
            quotes.append(
                {
                    "symbol": meta.get("symbol", symbol).upper(),
                    "regularMarketPrice": price,
                    "regularMarketChangePercent": pct,
                    "regularMarketVolume": meta.get("regularMarketVolume"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - collect per-symbol failures
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
    return quotes, "; ".join(errors) if errors else None


def stooq_symbol(symbol: str) -> str:
    if "." in symbol:
        return symbol.lower()
    return f"{symbol.lower()}.us"


def parse_stooq_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_stooq_quotes(symbols: list[str]) -> tuple[list[dict[str, Any]], str | None]:
    mapped = [stooq_symbol(symbol) for symbol in symbols]
    params = urllib.parse.urlencode({"s": ",".join(mapped), "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    url = f"{STOOQ_QUOTE_URL}?{params}"
    try:
        text = fetch_text(url)
    except Exception as exc:  # noqa: BLE001 - preserve the original source error in output
        return [], f"{type(exc).__name__}: {exc}"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return [], "empty CSV response"

    quotes = []
    for raw in lines[1:]:
        parts = raw.split(",")
        if len(parts) < 8:
            continue
        symbol_raw, _, _, open_raw, high_raw, low_raw, close_raw, volume_raw = parts[:8]
        close = parse_stooq_float(close_raw)
        open_price = parse_stooq_float(open_raw)
        if close is None:
            continue
        pct = None
        if open_price and open_price > 0:
            pct = ((close - open_price) / open_price) * 100
        base_symbol = symbol_raw.upper().removesuffix(".US")
        quotes.append(
            {
                "symbol": base_symbol,
                "regularMarketPrice": close,
                "regularMarketChangePercent": pct,
                "regularMarketVolume": int(volume_raw) if volume_raw.isdigit() else None,
            }
        )
    return quotes, None if quotes else "no parseable quotes"


def quote_by_symbol(quotes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(quote.get("symbol", "")).upper(): quote for quote in quotes}


def format_quote_table(
    symbols: list[str], quotes_by_symbol: dict[str, dict[str, Any]], threshold: float
) -> tuple[str, list[str]]:
    rows = ["| Symbol | Price | Day Change | Volume | Note |", "|---|---:|---:|---:|---|"]
    alerts: list[str] = []
    for symbol in symbols:
        quote = quotes_by_symbol.get(symbol, {"symbol": symbol})
        price = quote.get("regularMarketPrice")
        pct = quote.get("regularMarketChangePercent")
        volume = quote.get("regularMarketVolume")
        note = ""
        if isinstance(pct, (int, float)) and abs(pct) >= threshold:
            note = "threshold move"
            alerts.append(f"{symbol} moved {pct:+.2f}%")
        rows.append(
            "| {symbol} | {price} | {pct} | {volume} | {note} |".format(
                symbol=symbol,
                price=f"{price:,.2f}" if isinstance(price, (int, float)) else "n/a",
                pct=f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "n/a",
                volume=f"{volume:,}" if isinstance(volume, int) else "n/a",
                note=note,
            )
        )
    return "\n".join(rows), alerts


def market_summary(
    indexes: list[str],
    watchlist: list[str],
    quotes: list[dict[str, Any]],
    max_watchlist_movers: int = 6,
) -> str:
    by_symbol = quote_by_symbol(quotes)
    index_moves = []
    for symbol in indexes:
        pct = by_symbol.get(symbol, {}).get("regularMarketChangePercent")
        if isinstance(pct, (int, float)):
            index_moves.append(f"{symbol} {pct:+.2f}%")
    movers = []
    for symbol in watchlist:
        pct = by_symbol.get(symbol, {}).get("regularMarketChangePercent")
        if isinstance(pct, (int, float)):
            movers.append((abs(pct), symbol, pct))
    movers.sort(reverse=True)
    summary_parts = []
    if index_moves:
        summary_parts.append("Indexes: " + ", ".join(index_moves))
    if movers:
        summary_parts.append(
            "Watchlist top movers: "
            + ", ".join(f"{symbol} {pct:+.2f}%" for _, symbol, pct in movers[:max_watchlist_movers])
        )
    return " | ".join(summary_parts) if summary_parts else "No market data returned."


def phone_snapshot_summary(
    indexes: list[str],
    watchlist: list[str],
    quotes: list[dict[str, Any]],
    alerts: list[str],
) -> str:
    summary = market_summary(indexes, watchlist, quotes, max_watchlist_movers=7)
    if alerts:
        summary += " | Alerts: " + ", ".join(alerts[:5])
        if len(alerts) > 5:
            summary += f", +{len(alerts) - 5} more"
    return summary


def parse_manual_events(config: dict[str, Any]) -> list[Event]:
    events = []
    for item in config.get("manual_events", []):
        try:
            events.append(
                Event(
                    date=dt.date.fromisoformat(item["date"]),
                    title=str(item["title"]),
                    impact=str(item.get("impact", "medium")).lower(),
                    source=str(item.get("source", "manual")),
                )
            )
        except Exception:
            continue
    return events


def parse_ics_events(config: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for url in config.get("ics_urls", []):
        try:
            text = fetch_text(url)
        except Exception:
            continue
        for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.S):
            date_match = re.search(r"DTSTART(?:;[^:]*)?:(\d{8})", block)
            summary_match = re.search(r"SUMMARY(?:;[^:]*)?:(.+)", block)
            if not date_match or not summary_match:
                continue
            event_date = dt.datetime.strptime(date_match.group(1), "%Y%m%d").date()
            title = summary_match.group(1).strip().replace("\\,", ",")
            events.append(Event(event_date, title, "medium", url))
    return events


def parse_fomc_events(today: dt.date) -> list[Event]:
    try:
        text = fetch_text(FED_FOMC_URL)
    except Exception:
        return []

    clean = re.sub(r"<[^>]+>", " ", text)
    clean = html.unescape(re.sub(r"\s+", " ", clean))
    years = {today.year - 1, today.year, today.year + 1}
    month_names = (
        "January|February|March|April|May|June|July|August|September|October|November|December"
    )
    events: list[Event] = []
    for match in re.finditer(
        rf"({month_names})\s+(\d{{1,2}})(?:[-–](\d{{1,2}}))?,\s+({'|'.join(map(str, years))})",
        clean,
    ):
        month, start_day, end_day, year = match.groups()
        day = int(end_day or start_day)
        date_value = dt.datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
        events.append(Event(date_value, "FOMC meeting / policy decision window", "high", FED_FOMC_URL))
    return events


def options_expiration_events(today: dt.date, months: int = 4) -> list[Event]:
    events = []
    first = dt.date(today.year, today.month, 1)
    for offset in range(months):
        year = first.year + (first.month + offset - 1) // 12
        month = (first.month + offset - 1) % 12 + 1
        month_start = dt.date(year, month, 1)
        friday_count = 0
        day = month_start
        while day.month == month:
            if day.weekday() == 4:
                friday_count += 1
                if friday_count == 3:
                    events.append(Event(day, "Monthly options expiration", "medium", "generated"))
                    break
            day += dt.timedelta(days=1)
    return events


def market_calendar(config: dict[str, Any], today: dt.date) -> list[Event]:
    events = []
    events.extend(parse_manual_events(config))
    events.extend(parse_ics_events(config))
    events.extend(parse_fomc_events(today))
    events.extend(options_expiration_events(today))

    by_key: dict[tuple[dt.date, str], Event] = {}
    for event in events:
        by_key[(event.date, event.title)] = event

    start = today - dt.timedelta(days=int(config.get("calendar_lookback_days", 3)))
    end = today + dt.timedelta(days=int(config.get("calendar_lookahead_days", 14)))
    return sorted((e for e in by_key.values() if start <= e.date <= end), key=lambda e: (e.date, e.title))


def render_calendar(events: list[Event], today: dt.date) -> str:
    if not events:
        return "No configured market-moving events found in the selected window.\n"
    rows = ["| Date | Impact | Event | Source |", "|---|---|---|---|"]
    for event in events:
        marker = "today" if event.date == today else ""
        date_text = event.date.isoformat() + (f" ({marker})" if marker else "")
        rows.append(f"| {date_text} | {event.impact} | {event.title} | {event.source} |")
    return "\n".join(rows) + "\n"


def write_market_report(config: dict[str, Any], config_path: Path) -> tuple[Path, list[str], str]:
    tz = ZoneInfo(config.get("market_timezone", "America/New_York"))
    now = dt.datetime.now(tz)
    indexes, watchlist = configured_symbol_groups(config, config_path)
    symbols = unique_symbols(indexes + watchlist)
    threshold = float(config.get("price_alert_threshold_pct", 1.5))
    quotes, error = fetch_quotes_for_display(config, symbols)
    by_symbol = quote_by_symbol(quotes)
    index_table, index_alerts = format_quote_table(indexes, by_symbol, threshold)
    watchlist_table, watchlist_alerts = format_quote_table(watchlist, by_symbol, threshold)
    alerts = index_alerts + watchlist_alerts
    summary = phone_snapshot_summary(indexes, watchlist, quotes, alerts)
    out = output_dir(config, config_path) / "market_report.md"

    body = [
        f"# Market Snapshot",
        "",
        f"Generated: {now:%Y-%m-%d %H:%M:%S %Z}",
        "",
    ]
    if error:
        body.extend([f"Quote fetch failed: `{error}`", ""])
    elif quotes:
        body.extend(["## Indexes", "", index_table, "", "## Watchlist", "", watchlist_table, ""])
    else:
        body.extend(["No quote data returned.", ""])
    body.extend(["## Alerts", ""])
    body.extend([f"- {alert}" for alert in alerts] or ["- No threshold alerts."])
    body.append("")
    out.write_text("\n".join(body), encoding="utf-8")
    return out, alerts, summary


def read_alert_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "alert_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_alert_state(out_dir: Path, state: dict[str, Any]) -> None:
    (out_dir / "alert_state.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def emergency_alerts(config: dict[str, Any], config_path: Path) -> tuple[list[str], str | None]:
    indexes, watchlist = configured_symbol_groups(config, config_path)
    symbols = unique_symbols(indexes + watchlist)
    quotes, error = fetch_quotes_for_display(config, symbols)
    by_symbol = quote_by_symbol(quotes)
    index_threshold = float(config.get("emergency_index_alert_threshold_pct", 2.0))
    watchlist_threshold = float(config.get("emergency_alert_threshold_pct", 3.0))
    alerts = []
    for symbol in indexes:
        pct = by_symbol.get(symbol, {}).get("regularMarketChangePercent")
        if isinstance(pct, (int, float)) and abs(pct) >= index_threshold:
            alerts.append(f"{symbol} market index moved {pct:+.2f}%")
    for symbol in watchlist:
        pct = by_symbol.get(symbol, {}).get("regularMarketChangePercent")
        if isinstance(pct, (int, float)) and abs(pct) >= watchlist_threshold:
            alerts.append(f"{symbol} watchlist stock moved {pct:+.2f}%")
    return alerts, error


def new_daily_alerts(alerts: list[str], out_dir: Path, today: dt.date) -> list[str]:
    state = read_alert_state(out_dir)
    key = today.isoformat()
    seen = set(state.get(key, []))
    new = [alert for alert in alerts if alert not in seen]
    if new:
        state = {key: sorted(seen.union(new))}
        write_alert_state(out_dir, state)
    return new


def is_market_hours(now: dt.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now.replace(hour=16, minute=5, second=0, microsecond=0)
    return start <= now <= end


def write_calendar(config: dict[str, Any], config_path: Path) -> tuple[Path, list[Event]]:
    tz = ZoneInfo(config.get("market_timezone", "America/New_York"))
    today = dt.datetime.now(tz).date()
    events = market_calendar(config, today)
    out = output_dir(config, config_path) / "market_calendar.md"
    body = [
        "# Market Calendar",
        "",
        f"Window: {today - dt.timedelta(days=int(config.get('calendar_lookback_days', 3)))}"
        f" to {today + dt.timedelta(days=int(config.get('calendar_lookahead_days', 14)))}",
        "",
        render_calendar(events, today),
    ]
    out.write_text("\n".join(body), encoding="utf-8")
    return out, events


def send_ntfy(ntfy_config: dict[str, Any], title: str, message: str, priority: str, tags: list[str]) -> None:
    server = os.environ.get("NTFY_SERVER", str(ntfy_config.get("server", "https://ntfy.sh"))).rstrip("/")
    topic = os.environ.get("NTFY_TOPIC", str(ntfy_config.get("topic", ""))).strip()
    if not topic:
        print("ntfy topic is not configured.")
        return
    request = urllib.request.Request(
        f"{server}/{urllib.parse.quote(topic)}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": ",".join(tags),
            "User-Agent": "market-monitor/1.0 (+local script)",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def extract_openai_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()

    chunks: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def call_openai(input_text: str, max_output_tokens: int = 500) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    payload = {
        "model": model,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "market-monitor/1.0 (+local script)",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    text = extract_openai_text(data)
    if not text:
        raise RuntimeError("OpenAI API returned no text output.")
    return text


def openai_smoke_test() -> str:
    return call_openai(
        (
            "Write one concise phone notification confirming the OpenAI API works for a market "
            "monitor project. Mention that this is only a test, not investment advice."
        ),
        max_output_tokens=120,
    )


def quote_lines(symbols: list[str], quotes_by_symbol: dict[str, dict[str, Any]]) -> list[str]:
    lines = []
    for symbol in symbols:
        quote = quotes_by_symbol.get(symbol, {})
        price = quote.get("regularMarketPrice")
        pct = quote.get("regularMarketChangePercent")
        if isinstance(price, (int, float)) and isinstance(pct, (int, float)):
            lines.append(f"{symbol}: {price:,.2f}, {pct:+.2f}%")
    return lines


def build_close_digest(config: dict[str, Any], config_path: Path) -> tuple[str, str]:
    tz = ZoneInfo(config.get("market_timezone", "America/New_York"))
    now = dt.datetime.now(tz)
    indexes, watchlist = configured_symbol_groups(config, config_path)
    symbols = unique_symbols(indexes + watchlist)
    quotes, error = fetch_quotes_for_display(config, symbols)
    by_symbol = quote_by_symbol(quotes)

    threshold = float(config.get("price_alert_threshold_pct", 1.5))
    _, index_alerts = format_quote_table(indexes, by_symbol, threshold)
    _, watchlist_alerts = format_quote_table(watchlist, by_symbol, threshold)
    events = market_calendar(config, now.date())
    upcoming_events = [
        f"{event.date.isoformat()} [{event.impact}] {event.title}"
        for event in events
        if event.date >= now.date()
    ][:8]

    prompt = "\n".join(
        [
            "Create a concise market-close style phone digest for a personal market monitor.",
            "This is a first test: use only the structured market data below. Do not invent news.",
            "Keep it under 850 characters. Use this format:",
            "Market Close:",
            "Indexes: ...",
            "Top movers: ...",
            "Watch tomorrow: ...",
            "Tone: factual, cautious, not investment advice.",
            "",
            f"Generated at: {now:%Y-%m-%d %H:%M %Z}",
            "",
            "Indexes:",
            "\n".join(quote_lines(indexes, by_symbol)) or "No index data.",
            "",
            "Watchlist:",
            "\n".join(quote_lines(watchlist, by_symbol)) or "No watchlist data.",
            "",
            "Threshold alerts:",
            "\n".join(index_alerts + watchlist_alerts) or "None.",
            "",
            "Upcoming calendar events:",
            "\n".join(upcoming_events) or "None found.",
            "",
            f"Data-source error, if any: {error or 'None'}",
        ]
    )
    digest = call_openai(prompt, max_output_tokens=350)
    full_markdown = "\n".join(
        [
            "# Market Close Digest",
            "",
            f"Generated: {now:%Y-%m-%d %H:%M:%S %Z}",
            "",
            "## Phone Summary",
            "",
            digest,
            "",
            "## Raw Inputs",
            "",
            "### Indexes",
            "",
            "\n".join(f"- {line}" for line in quote_lines(indexes, by_symbol)) or "- No index data.",
            "",
            "### Watchlist",
            "",
            "\n".join(f"- {line}" for line in quote_lines(watchlist, by_symbol)) or "- No watchlist data.",
            "",
            "### Upcoming Calendar Events",
            "",
            "\n".join(f"- {line}" for line in upcoming_events) or "- None found.",
            "",
            "### Data Source Error",
            "",
            error or "None",
            "",
        ]
    )
    return digest, full_markdown


def notify(
    config: dict[str, Any],
    title: str,
    message: str,
    dry_run: bool,
    priority: str = "default",
    tags: list[str] | None = None,
) -> None:
    notifications = config.get("notifications", {})
    enabled = notifications.get("enabled", True)
    tags = tags or []
    print(f"{title}: {message}")
    if dry_run or not enabled:
        return
    ntfy_config = notifications.get("ntfy", {})
    if ntfy_config.get("enabled", False):
        send_ntfy(ntfy_config, title, message, priority, tags)
    if notifications.get("macos", True) and platform.system() == "Darwin":
        script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
        subprocess.run(["osascript", "-e", script], check=False)


def write_launchd_plists(config_path: Path, python_path: str, out_dir: Path) -> list[Path]:
    script_path = Path(__file__).resolve()
    jobs = [
        ("com.codex.market-monitor.10am", 10, 0, "notify"),
        ("com.codex.market-monitor.1pm", 13, 0, "notify"),
        ("com.codex.market-monitor.4pm", 16, 0, "notify"),
        ("com.codex.market-monitor.calendar", 8, 30, "calendar-notify"),
    ]
    paths = []
    for label, hour, minute, command in jobs:
        path = out_dir / f"{label}.plist"
        path.write_text(
            textwrap.dedent(
                f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
                <plist version="1.0">
                <dict>
                  <key>Label</key><string>{label}</string>
                  <key>ProgramArguments</key>
                  <array>
                    <string>{python_path}</string>
                    <string>{script_path}</string>
                    <string>{command}</string>
                    <string>--config</string>
                    <string>{config_path.resolve()}</string>
                  </array>
                  <key>StartCalendarInterval</key>
                  <dict>
                    <key>Hour</key><integer>{hour}</integer>
                    <key>Minute</key><integer>{minute}</integer>
                  </dict>
                  <key>RunAtLoad</key><false/>
                </dict>
                </plist>
                """
            ),
            encoding="utf-8",
        )
        paths.append(path)
    emergency_path = out_dir / "com.codex.market-monitor.emergency.plist"
    emergency_path.write_text(
        textwrap.dedent(
            f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
              <key>Label</key><string>com.codex.market-monitor.emergency</string>
              <key>ProgramArguments</key>
              <array>
                <string>{python_path}</string>
                <string>{script_path}</string>
                <string>emergency-check</string>
                <string>--config</string>
                <string>{config_path.resolve()}</string>
              </array>
              <key>StartInterval</key><integer>900</integer>
              <key>RunAtLoad</key><false/>
            </dict>
            </plist>
            """
        ),
        encoding="utf-8",
    )
    paths.append(emergency_path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Market monitoring and calendar notifications")
    parser.add_argument(
        "command",
        choices=[
            "report",
            "calendar",
            "notify",
            "emergency-check",
            "calendar-notify",
            "ai-smoke-test",
            "close-digest",
            "setup-launchd",
        ],
        help="action to run",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    out_dir = output_dir(config, args.config)

    if args.command == "report":
        path, _, summary = write_market_report(config, args.config)
        print(f"Wrote {path}: {summary}")
    elif args.command == "calendar":
        path, events = write_calendar(config, args.config)
        print(f"Wrote {path}: {len(events)} events")
    elif args.command == "notify":
        _, _, summary = write_market_report(config, args.config)
        title = config.get("notifications", {}).get("title", "Market Monitor")
        ntfy_config = config.get("notifications", {}).get("ntfy", {})
        priority = ntfy_config.get("default_priority", "default")
        notify(config, title, summary, args.dry_run, priority, ["chart_with_upwards_trend"])
    elif args.command == "emergency-check":
        tz = ZoneInfo(config.get("market_timezone", "America/New_York"))
        now = dt.datetime.now(tz)
        if not is_market_hours(now):
            print("Emergency check skipped outside regular market hours.")
            return 0
        today = now.date()
        alerts, error = emergency_alerts(config, args.config)
        new_alerts = new_daily_alerts(alerts, out_dir, today)
        if error:
            print(f"Emergency check data-source error: {error}")
        if new_alerts:
            title = config.get("notifications", {}).get("title", "Market Monitor")
            ntfy_config = config.get("notifications", {}).get("ntfy", {})
            priority = ntfy_config.get("emergency_priority", "urgent")
            notify(config, title, "Emergency: " + "; ".join(new_alerts), args.dry_run, priority, ["rotating_light"])
        else:
            print("No new emergency alerts.")
    elif args.command == "calendar-notify":
        path, events = write_calendar(config, args.config)
        high = [e for e in events if e.impact == "high"]
        message = f"{len(events)} events in window; {len(high)} high impact. Calendar: {path}"
        title = config.get("notifications", {}).get("title", "Market Monitor")
        ntfy_config = config.get("notifications", {}).get("ntfy", {})
        priority = ntfy_config.get("default_priority", "default")
        notify(config, title, message, args.dry_run, priority, ["calendar"])
    elif args.command == "ai-smoke-test":
        try:
            message = openai_smoke_test()
        except Exception as exc:  # noqa: BLE001 - CLI should return a clear setup error
            raise SystemExit(f"AI smoke test failed: {exc}") from exc
        title = config.get("notifications", {}).get("title", "Market Monitor")
        ntfy_config = config.get("notifications", {}).get("ntfy", {})
        priority = ntfy_config.get("default_priority", "default")
        notify(config, title, message, args.dry_run, priority, ["robot"])
    elif args.command == "close-digest":
        try:
            message, markdown = build_close_digest(config, args.config)
        except Exception as exc:  # noqa: BLE001 - CLI should return a clear setup error
            raise SystemExit(f"Close digest failed: {exc}") from exc
        digest_path = out_dir / "market_close_digest.md"
        digest_path.write_text(markdown, encoding="utf-8")
        title = config.get("notifications", {}).get("title", "Market Monitor")
        ntfy_config = config.get("notifications", {}).get("ntfy", {})
        priority = ntfy_config.get("default_priority", "default")
        notify(config, title, message, args.dry_run, priority, ["bar_chart"])
    elif args.command == "setup-launchd":
        paths = write_launchd_plists(args.config, sys.executable, out_dir)
        print("Wrote launchd plists:")
        for path in paths:
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
