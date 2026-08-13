from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError

from .database import Database
from .engine import MarketDataSafetyError, RoundPlanningError, TradingEngine
from .market_data import MarketDataError


@dataclass(frozen=True)
class WorkerResult:
    outcome: str
    retry_after_seconds: float = 0


_EXPECTED_DATA_FAILURES = (
    MarketDataError,
    HTTPError,
    URLError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
)


class TradingWorker:
    def __init__(
        self,
        database: Database,
        engine: TradingEngine,
        clock: Callable[[], datetime],
        *,
        sleeper: Callable[[float], None] | None = None,
        base_backoff_seconds: int = 1,
        max_backoff_seconds: int = 60,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.engine = engine
        self.clock = clock
        self._stop = threading.Event()
        self.sleeper = sleeper or self._stop.wait
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.fault_injector = fault_injector

    def step(self) -> WorkerResult:
        now = self._now()
        timestamp = self._timestamp(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                run = connection.execute(
                    "SELECT desired_state FROM trading_run WHERE id=1"
                ).fetchone()
                if run is None or run["desired_state"] != "running":
                    self._complete(connection, "stopped", timestamp, success=False)
                    connection.commit()
                    return WorkerResult("stopped")

                incident = connection.execute(
                    "SELECT next_retry_at FROM market_data_incidents WHERE active=1"
                ).fetchone()
                if incident and incident["next_retry_at"]:
                    retry_at = self._parse_timestamp(str(incident["next_retry_at"]))
                    if retry_at > now:
                        remaining = (retry_at - now).total_seconds()
                        self._upsert_checkpoint(connection, "backoff", timestamp, None)
                        connection.commit()
                        return WorkerResult("backoff", remaining)

                def completion(active: sqlite3.Connection) -> None:
                    active.execute(
                        "UPDATE market_data_incidents SET active=0, recovered_at=?, "
                        "next_retry_at=NULL WHERE active=1",
                        (timestamp,),
                    )
                    self._complete(active, "advanced", timestamp, success=True)
                    if self.fault_injector is not None:
                        self.fault_injector("transactional_completion")

                connection.execute("SAVEPOINT engine_evaluation")
                try:
                    self.engine.evaluate_active_round(
                        require_safe_data=True,
                        connection=connection,
                        completion_hook=completion,
                    )
                except (MarketDataSafetyError, *_EXPECTED_DATA_FAILURES) as error:
                    connection.execute("ROLLBACK TO SAVEPOINT engine_evaluation")
                    connection.execute("RELEASE SAVEPOINT engine_evaluation")
                    result = self._degrade(connection, str(error), now)
                    connection.commit()
                    return result
                connection.execute("RELEASE SAVEPOINT engine_evaluation")
                connection.commit()
                return WorkerResult("advanced")
            except RoundPlanningError as error:
                connection.rollback()
                if "stopped" in str(error):
                    self._checkpoint("stopped", now)
                    return WorkerResult("stopped")
                raise
            except BaseException:
                connection.rollback()
                raise

    def run_forever(self, poll_seconds: float = 1, *, max_steps: int | None = None) -> None:
        self._stop.clear()
        steps = 0
        while not self._stop.is_set() and (max_steps is None or steps < max_steps):
            result = self.step()
            steps += 1
            self.sleeper(max(poll_seconds, result.retry_after_seconds))

    def stop(self) -> None:
        self._stop.set()

    def _now(self) -> datetime:
        now = self.clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    @staticmethod
    def _timestamp(now: datetime) -> str:
        return now.isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _degrade(
        self, connection: sqlite3.Connection, cause: str, now: datetime
    ) -> WorkerResult:
        timestamp = self._timestamp(now)
        active = connection.execute(
            "SELECT id, retry_count FROM market_data_incidents WHERE active=1"
        ).fetchone()
        retry_count = int(active["retry_count"]) + 1 if active else 1
        delay = min(
            self.max_backoff_seconds,
            self.base_backoff_seconds * (2 ** (retry_count - 1)),
        )
        next_retry = self._timestamp(now + timedelta(seconds=delay))
        if active:
            connection.execute(
                "UPDATE market_data_incidents SET cause=?, retry_count=?, next_retry_at=? "
                "WHERE id=?",
                (cause, retry_count, next_retry, active["id"]),
            )
        else:
            round_row = connection.execute(
                "SELECT id FROM trading_round WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            connection.execute(
                "INSERT INTO market_data_incidents"
                "(round_id, cause, occurred_at, retry_count, next_retry_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (round_row["id"] if round_row else None, cause, timestamp, retry_count, next_retry),
            )
        connection.execute(
            "UPDATE trading_run SET operational_state='degraded', updated_at=? WHERE id=1",
            (timestamp,),
        )
        self._upsert_checkpoint(connection, "degraded", timestamp, None)
        return WorkerResult("degraded", float(delay))

    def _complete(
        self, connection: sqlite3.Connection, outcome: str, timestamp: str, *, success: bool
    ) -> None:
        self._upsert_checkpoint(connection, outcome, timestamp, timestamp if success else None)
        operational = "running" if outcome == "advanced" else "stopped"
        connection.execute(
            "UPDATE trading_run SET operational_state=?, updated_at=? WHERE id=1",
            (operational, timestamp),
        )

    def _upsert_checkpoint(
        self, connection: sqlite3.Connection, outcome: str, timestamp: str, success_at: str | None
    ) -> None:
        connection.execute(
            "INSERT INTO worker_checkpoint"
            "(id, last_attempt_at, last_success_at, outcome, updated_at) VALUES(1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at, "
            "last_success_at=COALESCE(excluded.last_success_at, "
            "worker_checkpoint.last_success_at), "
            "outcome=excluded.outcome, updated_at=excluded.updated_at",
            (timestamp, success_at, outcome, timestamp),
        )

    def _checkpoint(self, outcome: str, now: datetime) -> None:
        timestamp = self._timestamp(now)
        with self.database.connect() as connection, connection:
            self._complete(connection, outcome, timestamp, success=False)
