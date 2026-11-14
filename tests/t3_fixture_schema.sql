-- The tables of T3 Code's statev2.sqlite that agentmap/t3.py reads, with only
-- the columns that it uses. Names, types and indexes follow T3 Code's schema
-- of 2026-11-14.

CREATE TABLE projection_projects (
  project_id TEXT PRIMARY KEY,
  workspace_root TEXT NOT NULL,
  deleted_at TEXT
);

CREATE TABLE orchestration_v2_projection_threads (
  thread_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  deleted_at TEXT,
  payload_json TEXT NOT NULL
);
CREATE INDEX orchestration_v2_projection_threads_project_updated_idx
  ON orchestration_v2_projection_threads(project_id, updated_at);

CREATE TABLE orchestration_v2_projection_runs (
  run_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  status TEXT NOT NULL,
  completed_at TEXT
);
CREATE UNIQUE INDEX orchestration_v2_projection_runs_thread_ordinal_idx
  ON orchestration_v2_projection_runs(thread_id, ordinal);

CREATE TABLE orchestration_v2_projection_turn_items (
  turn_item_id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  run_id TEXT,
  node_id TEXT,
  ordinal INTEGER NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX orchestration_v2_projection_turn_items_thread_ordinal_idx
  ON orchestration_v2_projection_turn_items(thread_id, ordinal, turn_item_id);
CREATE INDEX orchestration_v2_projection_turn_items_run_ordinal_idx
  ON orchestration_v2_projection_turn_items(run_id, ordinal);
CREATE INDEX orchestration_v2_projection_turn_items_node_ordinal_idx
  ON orchestration_v2_projection_turn_items(node_id, ordinal);
CREATE INDEX orchestration_v2_projection_turn_items_active_ordinal_idx
  ON orchestration_v2_projection_turn_items(thread_id, ordinal, turn_item_id)
  WHERE status IN ('pending', 'running', 'waiting');

CREATE TABLE orchestration_v2_projection_subagents (
  subagent_id TEXT PRIMARY KEY,
  child_thread_id TEXT,
  status TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX orchestration_v2_projection_subagents_child_thread_idx
  ON orchestration_v2_projection_subagents(child_thread_id);
