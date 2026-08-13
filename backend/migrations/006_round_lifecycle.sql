ALTER TABLE trading_round ADD COLUMN ended_at TEXT;
ALTER TABLE trading_round ADD COLUMN ending_equity_ntd TEXT;
ALTER TABLE trading_round ADD COLUMN retrospective_json TEXT;
ALTER TABLE trading_round ADD COLUMN cycle_id INTEGER REFERENCES cycles(id);
ALTER TABLE trading_round ADD COLUMN lifecycle_transition_id INTEGER REFERENCES lifecycle_transitions(id);

ALTER TABLE trading_run ADD COLUMN terminal_state TEXT
    CHECK (terminal_state IS NULL OR terminal_state = 'bankrupt');
ALTER TABLE trading_run ADD COLUMN terminal_detail TEXT;

DROP TRIGGER immutable_active_round_update;
DROP TRIGGER immutable_completed_round_update;

CREATE TABLE cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL CHECK (status IN ('active', 'completed')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    starting_capital_ntd TEXT NOT NULL,
    ending_capital_ntd TEXT,
    completed_round_count INTEGER NOT NULL DEFAULT 0,
    end_reason TEXT,
    evidence_json TEXT,
    CHECK ((status='active' AND ended_at IS NULL) OR
           (status='completed' AND ended_at IS NOT NULL AND ending_capital_ntd IS NOT NULL))
);
CREATE UNIQUE INDEX one_active_cycle ON cycles(status) WHERE status='active';

INSERT INTO cycles(status, started_at, starting_capital_ntd, completed_round_count)
SELECT 'active',
       COALESCE((SELECT MIN(started_at) FROM trading_round),
                (SELECT updated_at FROM run_settings WHERE id=1)),
       (SELECT starting_capital_ntd FROM run_settings WHERE id=1),
       (SELECT COUNT(*) FROM trading_round WHERE status='completed')
WHERE EXISTS (SELECT 1 FROM trading_round);
UPDATE trading_round
SET cycle_id=(SELECT id FROM cycles WHERE status='active')
WHERE cycle_id IS NULL;

CREATE TRIGGER immutable_active_round_update
BEFORE UPDATE ON trading_round WHEN OLD.status='active'
AND NOT (NEW.status='completed' AND NEW.frozen_settings_json=OLD.frozen_settings_json
         AND NEW.started_at=OLD.started_at AND NEW.id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'active round is immutable'); END;

CREATE TABLE lifecycle_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES cycles(id),
    completed_round_id INTEGER NOT NULL UNIQUE REFERENCES trading_round(id),
    status TEXT NOT NULL CHECK (status IN ('pending_plan', 'completed')),
    created_at TEXT NOT NULL,
    ending_equity_ntd TEXT NOT NULL,
    next_starting_capital_ntd TEXT NOT NULL,
    reason TEXT NOT NULL,
    activated_round_id INTEGER UNIQUE REFERENCES trading_round(id),
    completed_at TEXT
);
CREATE UNIQUE INDEX one_pending_lifecycle_transition
ON lifecycle_transitions(status) WHERE status='pending_plan';
CREATE UNIQUE INDEX one_active_round ON trading_round(status) WHERE status='active';

CREATE TABLE transition_planning_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id INTEGER NOT NULL REFERENCES lifecycle_transitions(id),
    occurred_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE round_retrospectives (
    round_id INTEGER PRIMARY KEY REFERENCES trading_round(id),
    created_at TEXT NOT NULL,
    starting_equity_ntd TEXT NOT NULL,
    ending_equity_ntd TEXT NOT NULL,
    return_pct TEXT NOT NULL,
    max_drawdown_pct TEXT NOT NULL,
    total_costs_ntd TEXT NOT NULL,
    trade_count INTEGER NOT NULL,
    win_count INTEGER NOT NULL,
    loss_count INTEGER NOT NULL,
    rejected_action_count INTEGER NOT NULL,
    pairs_json TEXT NOT NULL,
    strategies_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE cycle_retrospectives (
    cycle_id INTEGER PRIMARY KEY REFERENCES cycles(id),
    created_at TEXT NOT NULL,
    starting_capital_ntd TEXT NOT NULL,
    ending_capital_ntd TEXT NOT NULL,
    completed_round_count INTEGER NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE bankruptcies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL UNIQUE REFERENCES cycles(id),
    round_id INTEGER NOT NULL UNIQUE REFERENCES trading_round(id),
    declared_at TEXT NOT NULL,
    ending_equity_ntd TEXT NOT NULL,
    completed_round_count INTEGER NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);

CREATE TRIGGER immutable_completed_round_lifecycle
BEFORE UPDATE ON trading_round
WHEN OLD.status='completed'
BEGIN SELECT RAISE(ABORT, 'completed round lifecycle is immutable'); END;
CREATE TRIGGER immutable_completed_round_delete BEFORE DELETE ON trading_round
WHEN OLD.status='completed'
BEGIN SELECT RAISE(ABORT, 'completed round lifecycle is immutable'); END;
CREATE TRIGGER immutable_round_retrospective_update BEFORE UPDATE ON round_retrospectives
BEGIN SELECT RAISE(ABORT, 'round retrospective is immutable'); END;
CREATE TRIGGER immutable_round_retrospective_delete BEFORE DELETE ON round_retrospectives
BEGIN SELECT RAISE(ABORT, 'round retrospective is immutable'); END;
CREATE TRIGGER immutable_cycle_retrospective_update BEFORE UPDATE ON cycle_retrospectives
BEGIN SELECT RAISE(ABORT, 'cycle retrospective is immutable'); END;
CREATE TRIGGER immutable_cycle_retrospective_delete BEFORE DELETE ON cycle_retrospectives
BEGIN SELECT RAISE(ABORT, 'cycle retrospective is immutable'); END;
CREATE TRIGGER immutable_bankruptcy_update BEFORE UPDATE ON bankruptcies
BEGIN SELECT RAISE(ABORT, 'bankruptcy is immutable'); END;
CREATE TRIGGER immutable_bankruptcy_delete BEFORE DELETE ON bankruptcies
BEGIN SELECT RAISE(ABORT, 'bankruptcy is immutable'); END;
CREATE TRIGGER immutable_completed_cycle_update BEFORE UPDATE ON cycles WHEN OLD.status='completed'
BEGIN SELECT RAISE(ABORT, 'completed cycle is immutable'); END;
CREATE TRIGGER immutable_cycle_delete BEFORE DELETE ON cycles
BEGIN SELECT RAISE(ABORT, 'cycle history is immutable'); END;
CREATE TRIGGER immutable_completed_transition_update BEFORE UPDATE ON lifecycle_transitions
WHEN OLD.status='completed'
BEGIN SELECT RAISE(ABORT, 'completed transition is immutable'); END;
CREATE TRIGGER immutable_transition_delete BEFORE DELETE ON lifecycle_transitions
BEGIN SELECT RAISE(ABORT, 'transition history is immutable'); END;
