# ruff: noqa: E501
from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_persisted_timestamp(value: object) -> datetime:
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as error:
        raise ValueError(f"malformed persisted timestamp: {text!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"malformed persisted timestamp (timezone required): {text!r}")
    return parsed.astimezone(UTC)


def _escape(value: object) -> str:
    if value is None or value == "":
        return "unavailable"
    text = html.escape(str(value), quote=False).replace("\\", "\\\\")
    for character in ("`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", "|"):
        text = text.replace(character, f"\\{character}")
    return text.replace("\r", " ").replace("\n", "<br>")


def _json(value: object) -> str:
    if value is None or value == "":
        return "unavailable"
    try:
        parsed = json.loads(str(value))
    except (json.JSONDecodeError, TypeError):
        return _escape(value)
    return _escape(json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _rows(
    connection: sqlite3.Connection, sql: str, values: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    return list(connection.execute(sql, values).fetchall())


def _record(
    lines: list[str], title: str, row: Mapping[str, Any], json_fields: Iterable[str] = ()
) -> None:
    lines.append(f"### {_escape(title)}")
    json_keys = set(json_fields)
    for key in row:
        formatter = _json if key in json_keys or key.endswith("_json") else _escape
        lines.append(f"- **{_escape(key.replace('_', ' ').title())}:** {formatter(row[key])}")
    lines.append("")


def _data_cutoff(connection: sqlite3.Connection) -> str | None:
    queries = (
        "SELECT updated_at value FROM trading_run",
        "SELECT updated_at value FROM run_settings",
        "SELECT started_at value FROM cycles UNION ALL SELECT ended_at FROM cycles",
        "SELECT started_at value FROM trading_round UNION ALL SELECT ended_at FROM trading_round",
        "SELECT observed_at value FROM market_rankings UNION ALL SELECT conversion_observed_at FROM market_rankings",
        "SELECT evaluated_at value FROM trading_signals UNION ALL SELECT source_timestamp FROM trading_signals",
        "SELECT opened_at value FROM paper_positions",
        "SELECT executed_at value FROM paper_trades UNION ALL SELECT source_timestamp FROM paper_trades",
        "SELECT valued_at value FROM portfolio_snapshots",
        "SELECT occurred_at value FROM market_data_incidents UNION ALL SELECT recovered_at FROM market_data_incidents",
        "SELECT next_retry_at value FROM market_data_incidents",
        "SELECT occurred_at value FROM planning_failures UNION ALL SELECT occurred_at FROM transition_planning_failures",
        "SELECT created_at value FROM lifecycle_transitions UNION ALL SELECT completed_at FROM lifecycle_transitions",
        "SELECT created_at value FROM round_retrospectives UNION ALL SELECT created_at FROM cycle_retrospectives",
        "SELECT declared_at value FROM bankruptcies",
        "SELECT last_attempt_at value FROM worker_checkpoint UNION ALL SELECT last_success_at FROM worker_checkpoint UNION ALL SELECT updated_at FROM worker_checkpoint",
    )
    values = [
        _parse_persisted_timestamp(row[0])
        for query in queries
        for row in connection.execute(query)
        if row[0]
    ]
    return _timestamp(max(values)) if values else None


def build_run_report(
    connection: sqlite3.Connection,
    clock: Callable[[], datetime],
    *,
    after_snapshot_pinned: Callable[[], None] | None = None,
) -> str:
    """Build a complete deterministic Markdown export solely from persisted audit records."""
    if connection.in_transaction:
        raise RuntimeError("report generation requires a connection without an active transaction")
    connection.execute("BEGIN")
    try:
        report = _build_run_report(connection, clock, after_snapshot_pinned)
        connection.commit()
        return report
    except BaseException:
        connection.rollback()
        raise


def _build_run_report(
    connection: sqlite3.Connection,
    clock: Callable[[], datetime],
    after_snapshot_pinned: Callable[[], None] | None,
) -> str:
    # This first read pins SQLite's deferred transaction to one read snapshot.
    run = connection.execute("SELECT * FROM trading_run WHERE id=1").fetchone()
    if after_snapshot_pinned is not None:
        after_snapshot_pinned()
    settings = connection.execute("SELECT * FROM run_settings WHERE id=1").fetchone()
    cycles = _rows(connection, "SELECT * FROM cycles ORDER BY id")
    rounds = _rows(connection, "SELECT * FROM trading_round ORDER BY id")
    active_cycle = next((row for row in cycles if row["status"] == "active"), None)
    active_round = next((row for row in rounds if row["status"] == "active"), None)
    snapshots = _rows(connection, "SELECT * FROM portfolio_snapshots ORDER BY valued_at,id")
    latest_snapshot = connection.execute(
        "SELECT ps.* FROM portfolio_snapshots ps JOIN trading_round tr ON tr.id=ps.round_id "
        "WHERE tr.cycle_id=? ORDER BY ps.valued_at DESC,ps.id DESC LIMIT 1",
        (active_cycle["id"],),
    ).fetchone() if active_cycle else None
    configured = Decimal(str(settings["starting_capital_ntd"]))
    experiment = Decimal(str(cycles[0]["starting_capital_ntd"])) if cycles else configured
    cycle_capital = (
        Decimal(str(active_cycle["starting_capital_ntd"]))
        if active_cycle
        else Decimal(str(run["current_capital_ntd"]))
    )
    current = (
        Decimal(str(latest_snapshot["total_equity_ntd"]))
        if latest_snapshot
        else Decimal(str(run["current_capital_ntd"]))
    )
    available = (
        Decimal(str(latest_snapshot["available_capital_ntd"])) if latest_snapshot else current
    )
    exposure = (
        Decimal(str(latest_snapshot["position_value_ntd"])) if latest_snapshot else Decimal("0")
    )
    cutoff = _data_cutoff(connection)

    lines = [
        "# Paper Trading Run Report",
        "",
        "**Mode:** PAPER TRADING ONLY — no real orders",
        f"**Generated at:** {_timestamp(clock())}",
        f"**Data cutoff:** {cutoff if cutoff else 'unavailable — no persisted audit events'}",
        "**Source:** SQLite persisted audit ledger; Binance public market data provenance where recorded.",
        "**Completeness:** The complete persisted run audit is included; authentication, sessions, migration metadata, and internal schema are intentionally outside this export boundary. Paginated dashboard views are not used.",
        "",
        "## Capital Semantics",
        "",
        f"- **Configured starting capital (mutable default):** {configured:.2f} NTD",
        f"- **Experiment initial capital (first persisted cycle, immutable history):** {experiment:.2f} NTD",
        f"- **Current-cycle starting capital:** {cycle_capital:.2f} NTD",
        f"- **Current equity:** {current:.2f} NTD",
        f"- **Available capital:** {available:.2f} NTD",
        f"- **Current-cycle profit:** {current - cycle_capital:.2f} NTD",
        "",
        "## Current Status and Health",
        "",
        f"- **Desired state:** {_escape(run['desired_state'])}",
        f"- **Operational state:** {_escape(run['operational_state'])}",
        f"- **Terminal state:** {_escape(run['terminal_state'])}",
        f"- **Terminal detail:** {_escape(run['terminal_detail'])}",
    ]
    incidents = _rows(connection, "SELECT * FROM market_data_incidents ORDER BY occurred_at,id")
    failures = _rows(connection, "SELECT * FROM planning_failures ORDER BY occurred_at,id")
    transition_failures = _rows(
        connection, "SELECT * FROM transition_planning_failures ORDER BY occurred_at,id"
    )
    checkpoint = connection.execute("SELECT * FROM worker_checkpoint WHERE id=1").fetchone()
    lines.extend(
        [
            f"- **Health:** {'degraded' if any(row['active'] for row in incidents + failures + transition_failures) else 'healthy'}",
            f"- **Worker checkpoint:** {_json(dict(checkpoint)) if checkpoint else 'unavailable'}",
            "",
            "### Incidents and retries",
            "",
        ]
    )
    if not incidents:
        lines.append("No market-data incidents have been persisted.\n")
    for row in incidents:
        _record(lines, f"Incident {row['id']}", dict(row))

    lines.extend(["## Current Cycle and Round", ""])
    lines.append(
        f"- **Current cycle:** {_escape(active_cycle['id']) if active_cycle else 'unavailable'}"
    )
    lines.append(
        f"- **Current round:** {_escape(active_round['id']) if active_round else 'unavailable'}"
    )
    if not cycles:
        lines.append("\nNo trading cycles have been persisted.")
    if not rounds:
        lines.append("\nNo trading rounds have been persisted.")
    lines.append("")

    lines.extend(["## Trading Cycles (complete)", ""])
    if not cycles:
        lines.append("No trading cycle records have been persisted.\n")
    for row in cycles:
        _record(lines, f"Trading cycle {row['id']}", dict(row))

    lines.extend(["## Trading Rounds (complete)", ""])
    if not rounds:
        lines.append("No trading round records have been persisted.\n")
    for row in rounds:
        _record(lines, f"Trading round {row['id']}", dict(row))

    lines.extend(["## Configured Settings", ""])
    _record(lines, "Current mutable defaults", dict(settings))

    lines.extend(["## Selected Pair Rankings, Strategies, Settings, and Backtests", ""])
    for round_row in rounds:
        _record(
            lines,
            f"Round {round_row['id']} frozen settings",
            {"frozen_settings_json": round_row["frozen_settings_json"]},
        )
        rankings = _rows(
            connection,
            "SELECT * FROM market_rankings WHERE round_id=? ORDER BY rank IS NULL,rank,symbol",
            (round_row["id"],),
        )
        selections = _rows(
            connection,
            "SELECT * FROM round_selections WHERE round_id=? ORDER BY selection_rank,symbol",
            (round_row["id"],),
        )
        backtests = _rows(
            connection,
            "SELECT * FROM backtest_results WHERE round_id=? ORDER BY symbol,strategy_version",
            (round_row["id"],),
        )
        for row in rankings:
            _record(lines, f"Ranking {row['symbol']}", dict(row))
        for row in selections:
            _record(lines, f"Selection {row['selection_rank']}: {row['symbol']}", dict(row))
        for row in backtests:
            _record(lines, f"Backtest {row['symbol']} / {row['strategy_version']}", dict(row))
    if not rounds:
        lines.append(
            "No ranking, selected-pair, strategy, frozen-setting, assumption, or backtest evidence has been persisted.\n"
        )

    lines.extend(["## Capital, Profit, Exposure, and Performance", ""])
    lines.extend(
        [
            f"- **Latest equity:** {current:.2f} NTD",
            f"- **Latest available:** {available:.2f} NTD",
            f"- **Latest exposure:** {exposure:.2f} NTD",
            f"- **Current-cycle profit:** {current - cycle_capital:.2f} NTD",
            "",
        ]
    )
    if not snapshots:
        lines.append("No portfolio snapshots have been persisted.\n")
    for row in snapshots:
        _record(lines, f"Portfolio snapshot {row['id']}", dict(row))
    retrospectives = _rows(connection, "SELECT * FROM round_retrospectives ORDER BY round_id")
    for row in retrospectives:
        _record(lines, f"Round {row['round_id']} performance summary", dict(row))

    lines.extend(["## Full Trade Results", ""])
    trades = _rows(connection, "SELECT * FROM paper_trades ORDER BY executed_at,id")
    if not trades:
        lines.append("No paper trades have been persisted.\n")
    for row in trades:
        _record(lines, f"Paper trade {row['id']}", dict(row))

    lines.extend(["## Trading Signals (complete)", ""])
    signals = _rows(connection, "SELECT * FROM trading_signals ORDER BY evaluated_at,id")
    if not signals:
        lines.append("No trading signals have been persisted.\n")
    for row in signals:
        _record(lines, f"Trading signal {row['signal_id']}", dict(row))

    lines.extend(["## Current Paper Positions (complete)", ""])
    positions = _rows(
        connection,
        "SELECT p.*,s.source_timestamp entry_source_timestamp,s.evaluated_at "
        "entry_evaluated_at,s.market_evidence_json entry_market_evidence_json "
        "FROM paper_positions p JOIN trading_signals s ON s.round_id=p.round_id "
        "AND s.symbol=p.symbol AND s.signal_id=p.entry_signal_id "
        "ORDER BY p.round_id,p.symbol",
    )
    if not positions:
        lines.append("No current paper positions have been persisted.\n")
    for row in positions:
        _record(lines, f"Current paper position {row['round_id']} / {row['symbol']}", dict(row))

    lines.extend(["## Rejected Decisions and Planning Evidence", ""])
    rejected = _rows(
        connection,
        "SELECT * FROM trading_signals WHERE outcome='rejected' ORDER BY evaluated_at,id",
    )
    if not rejected and not failures and not transition_failures:
        lines.append("No rejected decisions or planning failures have been persisted.\n")
    for row in rejected:
        _record(lines, f"Rejected signal {row['signal_id']}", dict(row))
    for row in failures:
        _record(lines, f"Planning failure {row['id']}", dict(row))
    for row in transition_failures:
        _record(lines, f"Transition planning failure {row['id']}", dict(row))

    lines.extend(["## Completed Round, Cycle, Bankruptcy, and Reset Retrospectives", ""])
    cycle_retrospectives = _rows(connection, "SELECT * FROM cycle_retrospectives ORDER BY cycle_id")
    bankruptcies = _rows(connection, "SELECT * FROM bankruptcies ORDER BY declared_at,id")
    transitions = _rows(connection, "SELECT * FROM lifecycle_transitions ORDER BY created_at,id")
    if not retrospectives and not cycle_retrospectives and not bankruptcies and not transitions:
        lines.append(
            "No completed-round, completed-cycle, bankruptcy, or reset retrospectives have been persisted.\n"
        )
    for row in retrospectives:
        _record(lines, f"Completed round {row['round_id']} retrospective", dict(row))
    for row in cycle_retrospectives:
        _record(lines, f"Completed cycle {row['cycle_id']} retrospective", dict(row))
    for row in bankruptcies:
        _record(lines, f"Bankruptcy {row['id']}", dict(row))
    for row in transitions:
        _record(lines, f"Round/cycle transition {row['id']}", dict(row))

    lines.extend(
        [
            "## Provenance and Audit Boundary",
            "",
            "- Report content is reconstructed only from persisted SQLite records.",
            "- Market observations originate from Binance public market data; recorded timestamps, quote conversion paths, rates, and provenance are shown above when available.",
            "- Missing evidence is reported as unavailable or as an explicit empty section; no values are inferred or fabricated.",
            "- The generated-at timestamp is injected by the application clock and is the only intentionally generation-time-dependent value.",
            "- Authentication, sessions, migration metadata, and internal schema are intentionally omitted; all persisted run-domain audit records are in scope.",
            "",
        ]
    )
    return "\n".join(lines)
