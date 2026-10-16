-- Comparison / Business Discovery extensions (idempotent).

CREATE TABLE IF NOT EXISTS comparison_fetch_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS account_measurements (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    fetch_run_id INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    followers_count INTEGER,
    media_count INTEGER,
    UNIQUE(fetch_run_id, account_id),
    FOREIGN KEY(account_id) REFERENCES accounts(id),
    FOREIGN KEY(fetch_run_id) REFERENCES comparison_fetch_runs(id)
);

CREATE TABLE IF NOT EXISTS post_measurements (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL,
    fetch_run_id INTEGER NOT NULL,
    measured_at TEXT NOT NULL,
    like_count INTEGER,
    comments_count INTEGER,
    UNIQUE(fetch_run_id, post_id),
    FOREIGN KEY(post_id) REFERENCES posts(id),
    FOREIGN KEY(fetch_run_id) REFERENCES comparison_fetch_runs(id)
);

CREATE TABLE IF NOT EXISTS comparison_fetch_account_results (
    id INTEGER PRIMARY KEY,
    fetch_run_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    posts_fetched INTEGER NOT NULL DEFAULT 0,
    period_start TEXT,
    period_end TEXT,
    is_complete INTEGER NOT NULL DEFAULT 0,
    oldest_post_at TEXT,
    newest_post_at TEXT,
    error_message TEXT,
    UNIQUE(fetch_run_id, account_id),
    FOREIGN KEY(fetch_run_id) REFERENCES comparison_fetch_runs(id),
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS comparison_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    own_account_id INTEGER,
    competitor_usernames_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(own_account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS comparison_rate_limits (
    route TEXT PRIMARY KEY,
    hit_at TEXT NOT NULL,
    error_code INTEGER,
    message TEXT,
    retry_after_seconds INTEGER,
    blocked_until TEXT,
    usage_json TEXT,
    last_success_measured_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comparison_discovery_checkpoints (
    account_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    cursor_after TEXT,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    posts_fetched INTEGER NOT NULL DEFAULT 0,
    is_complete INTEGER NOT NULL DEFAULT 0,
    last_fetch_run_id INTEGER,
    account_external_id TEXT,
    account_payload_json TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id),
    FOREIGN KEY(last_fetch_run_id) REFERENCES comparison_fetch_runs(id)
);

CREATE TABLE IF NOT EXISTS comparison_discovery_request_log (
    id INTEGER PRIMARY KEY,
    fetch_run_id INTEGER,
    account_id INTEGER,
    username TEXT NOT NULL,
    page_index INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    posts_received INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL,
    usage_json TEXT,
    cursor_before TEXT,
    cursor_after TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    measured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(fetch_run_id) REFERENCES comparison_fetch_runs(id),
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_account_measurements_account_measured
    ON account_measurements(account_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_post_measurements_post_measured
    ON post_measurements(post_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_comparison_fetch_runs_started
    ON comparison_fetch_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_account_published
    ON posts(account_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_comparison_role
    ON accounts(comparison_role);
CREATE INDEX IF NOT EXISTS idx_discovery_request_log_run
    ON comparison_discovery_request_log(fetch_run_id, page_index);
CREATE INDEX IF NOT EXISTS idx_discovery_checkpoints_updated
    ON comparison_discovery_checkpoints(updated_at DESC);
