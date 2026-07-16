PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT,
    username TEXT,
    page_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    text TEXT,
    permalink TEXT,
    media_url TEXT,
    thumbnail_url TEXT,
    media_type TEXT,
    published_at TEXT NOT NULL,
    raw_json TEXT,
    first_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, external_id),
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    post_id INTEGER NOT NULL,
    parent_comment_id INTEGER,
    author_name TEXT,
    author_id TEXT,
    text TEXT NOT NULL,
    published_at TEXT NOT NULL,
    raw_json TEXT,
    first_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, external_id),
    FOREIGN KEY(post_id) REFERENCES posts(id),
    FOREIGN KEY(parent_comment_id) REFERENCES comments(id)
);

CREATE TABLE IF NOT EXISTS hashtags (
    id INTEGER PRIMARY KEY,
    tag TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS post_hashtags (
    post_id INTEGER NOT NULL,
    hashtag_id INTEGER NOT NULL,
    PRIMARY KEY(post_id, hashtag_id),
    FOREIGN KEY(post_id) REFERENCES posts(id),
    FOREIGN KEY(hashtag_id) REFERENCES hashtags(id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    resource_type TEXT NOT NULL,
    last_sync_at TEXT,
    last_cursor TEXT,
    last_success_at TEXT,
    last_error TEXT,
    UNIQUE(account_id, resource_type),
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,
    posts_added INTEGER NOT NULL DEFAULT 0,
    posts_updated INTEGER NOT NULL DEFAULT 0,
    comments_added INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5 (
    entity_type,
    entity_id,
    platform,
    text,
    hashtags,
    published_at UNINDEXED,
    permalink UNINDEXED,
    thumbnail_url UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 1'
);

CREATE INDEX IF NOT EXISTS idx_posts_platform_date ON posts(platform, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_published ON comments(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON hashtags(tag);
