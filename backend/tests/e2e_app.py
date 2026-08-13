import os
from datetime import UTC, datetime

from backend.app.main import create_app
from backend.tests.test_round_planning import FixtureMarketData

app = create_app(
    database_path=os.environ.get("TRADING_DATABASE_PATH", "data/playwright.sqlite3"),
    market_data=FixtureMarketData(),
    clock=lambda: datetime(2026, 1, 8, 12, tzinfo=UTC),
)
