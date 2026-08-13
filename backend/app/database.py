from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS = Path(__file__).parent.parent / "migrations"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                version = int(migration.name.split("_", 1)[0])
                if version in applied:
                    continue
                script = migration.read_text()
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + f"\nINSERT INTO schema_migrations VALUES ({version}, '{utc_now()}');\nCOMMIT;"
                )

    def ensure_defaults(self) -> None:
        now = utc_now()
        with self.connect() as connection, connection:
            connection.execute(
                """INSERT OR IGNORE INTO run_settings
                (id, starting_capital_ntd, round_duration_days, strategy_cadence_seconds,
                 max_position_allocation_pct, max_concurrent_positions, stop_loss_pct,
                 take_profit_pct, daily_loss_limit_pct, fee_pct, slippage_pct, updated_at)
                VALUES (1, '5000.00', 7, 300, '10.00', 3, '5.00', '10.00', '3.00',
                        '0.10', '0.10', ?)""",
                (now,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO trading_run"
                "(id, desired_state, current_capital_ntd, updated_at) "
                "VALUES (1, 'stopped', '5000.00', ?)",
                (now,),
            )
