-- Run once on a dedicated PUBLIC DEMO database. Never use the private course DB.
CREATE TABLE IF NOT EXISTS cp_demo_study_runs (
    id TEXT PRIMARY KEY,
    request_id TEXT UNIQUE NOT NULL,
    fingerprint TEXT NOT NULL,
    course TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cp_demo_study_charges (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    day TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cp_demo_limits (
    key TEXT NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (key, day)
);
CREATE INDEX IF NOT EXISTS cp_demo_runs_course_created
ON cp_demo_study_runs (course, created_at);
CREATE INDEX IF NOT EXISTS cp_demo_charges_day ON cp_demo_study_charges (day);
