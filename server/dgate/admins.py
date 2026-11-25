# -*- coding: utf-8 -*-
"""管理员账户库（独立 SQLite：server/data/admins.db）。

与审计库（dgate.db）分离，仅存后台管理员账户：
- 用户名 + werkzeug PBKDF2 哈希密码 + 超管标记
- 首次初始化种子超级管理员 admin（密码取 config.ADMIN_INITIAL_PASSWORD）
- 超管可添加/删除管理员（门禁与保护规则在 api/admin.py 层实现）
"""
import os
import sqlite3
import threading
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

import config

_local = threading.local()

# 种子超级管理员的用户名（密码来自 config.ADMIN_INITIAL_PASSWORD）
SEED_SUPER_USERNAME = 'admin'


class DuplicateAdminError(Exception):
    """用户名已存在。"""


def _conn():
    """每个线程一条连接（路径在调用时读取，便于测试 mock config）。"""
    conn = getattr(_local, 'admins_conn', None)
    if conn is None:
        os.makedirs(os.path.dirname(config.ADMINS_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(config.ADMINS_DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        _local.admins_conn = conn
    return conn


def init_admins():
    """幂等建表；空库时种子超级管理员。"""
    conn = _conn()
    conn.execute(
        'CREATE TABLE IF NOT EXISTS admin_user ('
        '  id INTEGER PRIMARY KEY AUTOINCREMENT,'
        '  username TEXT NOT NULL UNIQUE,'
        '  pwd_hash TEXT NOT NULL,'
        '  is_super INTEGER NOT NULL DEFAULT 0,'
        '  created_at TEXT NOT NULL)')
    row = conn.execute('SELECT COUNT(*) FROM admin_user').fetchone()
    if row[0] == 0:
        conn.execute(
            'INSERT INTO admin_user (username, pwd_hash, is_super, created_at) '
            'VALUES (?, ?, 1, ?)',
            (SEED_SUPER_USERNAME,
             generate_password_hash(config.ADMIN_INITIAL_PASSWORD),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()


def _to_user(row):
    if row is None:
        return None
    return {'username': row['username'],
            'is_super': bool(row['is_super']),
            'created_at': row['created_at']}


def get(username):
    row = _conn().execute(
        'SELECT username, is_super, created_at FROM admin_user '
        'WHERE username = ?', (username,)).fetchone()
    return _to_user(row)


def verify(username, password):
    """校验用户名密码，返回 (ok, user_dict_or_None)。"""
    row = _conn().execute(
        'SELECT username, pwd_hash, is_super, created_at FROM admin_user '
        'WHERE username = ?', (username,)).fetchone()
    if row is None:
        return False, None
    if not check_password_hash(row['pwd_hash'], password):
        return False, None
    return True, _to_user(row)


def list_admins():
    rows = _conn().execute(
        'SELECT username, is_super, created_at FROM admin_user '
        'ORDER BY id').fetchall()
    return [_to_user(r) for r in rows]


def add_admin(username, password, is_super):
    """添加管理员；用户名重复抛 DuplicateAdminError。"""
    conn = _conn()
    conn.execute(
        'INSERT INTO admin_user (username, pwd_hash, is_super, created_at) '
        'VALUES (?, ?, ?, ?)',
        (username, generate_password_hash(password), 1 if is_super else 0,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()


def delete_admin(username):
    """删除管理员（调用方负责保护规则）。不存在时静默返回。"""
    conn = _conn()
    conn.execute('DELETE FROM admin_user WHERE username = ?', (username,))
    conn.commit()


def set_password(username, new_password):
    conn = _conn()
    conn.execute('UPDATE admin_user SET pwd_hash = ? WHERE username = ?',
                 (generate_password_hash(new_password), username))
    conn.commit()
