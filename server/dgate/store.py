# -*- coding: utf-8 -*-
"""本地存储（SQLite）：审计日志 + 运行参数（settings 键值表）。

管理员账户已迁移至独立库 admins.db（dgate/admins.py）；
dgate.db 中遗留的旧 admin_credential 表不再读取。
"""
import logging
import os
import sqlite3
import threading
from datetime import datetime

import config

log = logging.getLogger(__name__)

_local = threading.local()


def _conn():
    """每个线程一条 SQLite 连接。"""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(config.SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        _local.conn = conn
    return conn


def init_store():
    conn = _conn()
    conn.execute(
        'CREATE TABLE IF NOT EXISTS audit_log ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  ts TEXT NOT NULL,'
        '  actor_type TEXT NOT NULL,'   # user / admin
        '  actor TEXT NOT NULL,'
        '  uid INTEGER,'
        '  action TEXT NOT NULL,'
        '  detail TEXT,'
        '  ip TEXT)')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS settings ('
        '  name TEXT PRIMARY KEY,'
        '  value TEXT NOT NULL,'
        '  updated_at TEXT,'
        '  updated_by TEXT)')
    conn.commit()


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

def audit(actor_type, actor, action, uid=None, detail=None, ip=None):
    _conn().execute(
        'INSERT INTO audit_log (ts, actor_type, actor, uid, action, detail, ip) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         actor_type, actor, uid, action, detail, ip))
    _conn().commit()


def list_audit(offset=0, limit=20, action=None):
    where, params = '', []
    if action:
        where = 'WHERE action = ?'
        params.append(action)
    conn = _conn()
    total = conn.execute('SELECT COUNT(*) FROM audit_log ' + where, params).fetchone()[0]
    rows = conn.execute(
        'SELECT id, ts, actor_type, actor, uid, action, detail, ip FROM audit_log '
        + where + ' ORDER BY id DESC LIMIT ? OFFSET ?',
        params + [limit, offset]).fetchall()
    items = [dict(r) for r in rows]
    return items, total


# ---------------------------------------------------------------------------
# 运行参数（后台「参数设置」可修改，持久化于 settings 表）
# ---------------------------------------------------------------------------

REGISTER_CERA_KEY = 'register_init_cera'   # 注册赠送点券


def get_setting(name, default=None):
    row = _conn().execute(
        'SELECT value FROM settings WHERE name = ?', (name,)).fetchone()
    return row[0] if row else default


def get_setting_row(name):
    """返回 (value, updated_at, updated_by)；参数未设置时返回 None。"""
    row = _conn().execute(
        'SELECT value, updated_at, updated_by FROM settings WHERE name = ?',
        (name,)).fetchone()
    return tuple(row) if row else None


def set_setting(name, value, updated_by=None):
    """写入参数（存在则覆盖），并记录修改时间与操作人。"""
    _conn().execute(
        'INSERT INTO settings (name, value, updated_at, updated_by) '
        'VALUES (?, ?, ?, ?) '
        'ON CONFLICT(name) DO UPDATE SET value = excluded.value, '
        'updated_at = excluded.updated_at, updated_by = excluded.updated_by',
        (name, str(value), datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         updated_by))
    _conn().commit()


def get_register_init_cera():
    """注册赠送点券的当前生效值；未设置或值非法时回退到 config 默认值。"""
    raw = get_setting(REGISTER_CERA_KEY)
    if raw is None:
        return config.REGISTER_INIT_CERA
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning('settings 中 %s 值非法（%r），回退默认值', REGISTER_CERA_KEY, raw)
        return config.REGISTER_INIT_CERA
