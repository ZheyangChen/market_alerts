from market_monitor.cli import phone_snapshot_summary


def test_phone_snapshot_summary_excludes_report_path() -> None:
    quotes = [
        {"symbol": "SPY", "regularMarketChangePercent": -0.5},
        {"symbol": "QQQ", "regularMarketChangePercent": -1.2},
        {"symbol": "AAPL", "regularMarketChangePercent": 2.0},
        {"symbol": "TSLA", "regularMarketChangePercent": -3.0},
    ]

    summary = phone_snapshot_summary(["SPY", "QQQ"], ["AAPL", "TSLA"], quotes, ["TSLA moved -3.00%"])

    assert "Report:" not in summary
    assert "SPY -0.50%" in summary
    assert "TSLA -3.00%" in summary
