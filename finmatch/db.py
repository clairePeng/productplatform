"""工作库：首次运行把种子库复制为「工作数据.sqlite」，种子库只读；产品、会话、方案写在工作库。"""
import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  product_code TEXT PRIMARY KEY,
  product_name TEXT NOT NULL,
  institution TEXT,
  amount_min REAL, amount_max REAL,
  rate_min REAL, rate_max REAL,
  term_min INTEGER, term_max INTEGER,
  purposes TEXT, regions TEXT, industries TEXT, guarantees TEXT,
  repay_method TEXT, revolving TEXT, subsidy_supported INTEGER,
  conditions TEXT,
  extra_conditions TEXT,
  source_map TEXT,
  intro_text TEXT,
  source_file TEXT, file_sha256 TEXT,
  upload_count INTEGER DEFAULT 1,
  uploaded_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  context TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT, role TEXT, content TEXT, trace TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS solutions (
  solution_id TEXT PRIMARY KEY,
  session_id TEXT, seq INTEGER,
  enterprise_id TEXT, enterprise_name TEXT, region TEXT, industry TEXT,
  request TEXT, primary_condition TEXT, conclusion TEXT,
  precheck TEXT, recommended TEXT, eligible_count INTEGER,
  status TEXT, created_at TEXT, updated_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS solution_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  solution_id TEXT, event TEXT, detail TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY, session_id TEXT, at TEXT, total_ms INTEGER,
  prompt_tokens INTEGER, completion_tokens INTEGER, cost_cny REAL,
  status TEXT, error_node TEXT, body TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id, at);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT, key TEXT, value TEXT, source TEXT, updated_at TEXT,
  UNIQUE(scope, key)
);
"""


def init_db():
    os.makedirs(os.path.dirname(config.WORK_DB), exist_ok=True)
    if not os.path.exists(config.WORK_DB):
        shutil.copyfile(config.SEED_DB, config.WORK_DB)
    with connect() as c:
        c.executescript(SCHEMA)


def reset_runtime():
    """清空工作库并重新从种子库复制（批量测评前使用）。"""
    with _lock:
        if os.path.exists(config.WORK_DB):
            os.remove(config.WORK_DB)
        init_db()


@contextmanager
def connect():
    with _lock:
        c = sqlite3.connect(config.WORK_DB, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def dumps(o):
    return json.dumps(o, ensure_ascii=False)


def loads(s, default=None):
    if s is None or s == "":
        return default
    return json.loads(s)
