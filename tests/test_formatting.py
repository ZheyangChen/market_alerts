import datetime as dt
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from market_monitor.cli import (
    new_daily_alerts,
    phone_snapshot_summary,
    record_daily_alerts,
    send_ntfy,
)


class MarketMonitorTests(unittest.TestCase):
    def test_phone_snapshot_summary_excludes_report_path(self) -> None:
        quotes = [
            {"symbol": "SPY", "regularMarketChangePercent": -0.5},
            {"symbol": "QQQ", "regularMarketChangePercent": -1.2},
            {"symbol": "AAPL", "regularMarketChangePercent": 2.0},
            {"symbol": "TSLA", "regularMarketChangePercent": -3.0},
        ]

        summary = phone_snapshot_summary(
            ["SPY", "QQQ"], ["AAPL", "TSLA"], quotes, ["TSLA moved -3.00%"]
        )

        self.assertNotIn("Report:", summary)
        self.assertIn("SPY -0.50%", summary)
        self.assertIn("TSLA -3.00%", summary)

    def test_emergency_alert_is_deduplicated_by_symbol_for_the_day(self) -> None:
        today = dt.date(2026, 9, 2)
        first = "NVDA watchlist stock moved +3.42%"
        later = "NVDA watchlist stock moved +3.08%"

        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            self.assertEqual(new_daily_alerts([first], state_dir, today), [first])
            record_daily_alerts([first], state_dir, today)
            self.assertEqual(new_daily_alerts([later], state_dir, today), [])

    def test_ntfy_retries_transient_delivery_failure(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b""
        config = {"server": "https://ntfy.sh", "topic": "test-topic"}

        with (
            patch(
                "market_monitor.cli.urllib.request.urlopen",
                side_effect=[urllib.error.URLError("temporary"), response],
            ) as urlopen,
            patch("market_monitor.cli.time.sleep") as sleep,
        ):
            send_ntfy(config, "Title", "Message", "default", ["test"])

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_ntfy_requires_topic_when_enabled(self) -> None:
        with patch.dict("market_monitor.cli.os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NTFY_TOPIC is not configured"):
                send_ntfy(
                    {"server": "https://ntfy.sh", "topic": ""},
                    "Title",
                    "Message",
                    "default",
                    [],
                )


if __name__ == "__main__":
    unittest.main()
