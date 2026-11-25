# -*- coding: utf-8 -*-
"""游戏数据库访问层（pymysql，兼容 MySQL 5.1/5.5）。

所有游戏库表均为 MyISAM，不支持事务，注册流程通过“失败回滚清理”保证一致性。

缺表策略：绝不自动创建任何库表（避免影响游戏服务器）。注册流程涉及的从表
若在库中不存在，则跳过对应写入并记录日志/审计；核心表 d_taiwan.accounts
不存在时注册直接报错。
"""
import logging
from contextlib import contextmanager

import pymysql

import config
from . import store

log = logging.getLogger(__name__)


def connect():
    """建立一条新的游戏库连接。"""
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        charset=config.MYSQL_CHARSET,
        autocommit=True,          # MyISAM 无事务，直接自动提交
        cursorclass=pymysql.cursors.Cursor,
    )


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def existing_tables(conn, pairs):
    """返回 pairs 中真实存在的表集合（一次 information_schema 查询）。

    pairs: [(schema, table), ...]；返回 {(schema, table), ...}（小写比较）。
    """
    needed = sorted({(str(s).lower(), str(t).lower()) for s, t in pairs})
    found = set()
    if not needed:
        return found
    conds = []
    params = []
    for schema, table in needed:
        conds.append('(table_schema = %s AND table_name = %s)')
        params += [schema, table]
    with conn.cursor() as cur:
        cur.execute('SELECT table_schema, table_name FROM information_schema.tables '
                    'WHERE ' + ' OR '.join(conds), params)
        for row in cur.fetchall():
            found.add((row[0].lower(), row[1].lower()))
    return found


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------

def find_account(conn, username):
    """按用户名查询账号，返回 dict(UID, accountname, password) 或 None。"""
    with conn.cursor() as cur:
        cur.execute('SELECT UID, accountname, password FROM d_taiwan.accounts '
                    'WHERE accountname = %s', (username,))
        row = cur.fetchone()
    if row is None:
        return None
    return {'uid': row[0], 'accountname': row[1], 'password': row[2]}


def find_account_by_uid(conn, uid):
    with conn.cursor() as cur:
        cur.execute('SELECT UID, accountname, password FROM d_taiwan.accounts '
                    'WHERE UID = %s', (uid,))
        row = cur.fetchone()
    if row is None:
        return None
    return {'uid': row[0], 'accountname': row[1], 'password': row[2]}


def update_password(conn, uid, md5_password):
    with conn.cursor() as cur:
        cur.execute('UPDATE d_taiwan.accounts SET password = %s WHERE UID = %s',
                    (md5_password, uid))


# ---------------------------------------------------------------------------
# 封号（d_taiwan.member_punish_info，格式复刻 GMTool：punish_type=1 /
# punish_value=101 / apply_flag=2 / 10000 天；PK 为 m_id+punish_type）
# ---------------------------------------------------------------------------

def _has_punish_table(conn):
    return ('d_taiwan', 'member_punish_info') in \
        existing_tables(conn, [('d_taiwan', 'member_punish_info')])


def is_banned(conn, uid):
    """是否处于封号状态（punish_type=1 且未到期）；表缺失视为未封号。"""
    if not _has_punish_table(conn):
        return False
    with conn.cursor() as cur:
        cur.execute('SELECT 1 FROM d_taiwan.member_punish_info '
                    'WHERE m_id = %s AND punish_type = 1 AND end_time > NOW() '
                    'LIMIT 1', (uid,))
        return cur.fetchone() is not None


def ban_user(conn, uid, admin_name):
    """写入封号记录（幂等：同 UID 的 punish_type=1 记录会被覆盖）。"""
    with conn.cursor() as cur:
        cur.execute(
            'REPLACE INTO d_taiwan.member_punish_info '
            '(m_id, punish_type, occ_time, punish_value, apply_flag, '
            ' start_time, end_time, admin_id, reason) '
            'VALUES (%s, 1, NOW(), 101, 2, NOW(), '
            '        DATE_ADD(NOW(), INTERVAL 10000 DAY), %s, %s)',
            (uid, admin_name[:25], '-1'))


def unban_user(conn, uid):
    """删除封号记录，返回删除的行数。"""
    with conn.cursor() as cur:
        cur.execute('DELETE FROM d_taiwan.member_punish_info '
                    'WHERE m_id = %s AND punish_type = 1', (uid,))
        return cur.rowcount


def list_users(conn, keyword=None, offset=0, limit=20):
    """后台用户列表：ID、用户名、点券、角色数量（无记录视为 0）。

    taiwan_billing.cash_cera / taiwan_cain.charac_info 缺失时对应字段显示 0。
    """
    where = ''
    params = []
    if keyword:
        where = 'WHERE a.accountname LIKE %s OR a.UID = %s'
        like = '%' + keyword + '%'
        try:
            uid_kw = int(keyword)
        except (TypeError, ValueError):
            uid_kw = -1
        params = [like, uid_kw]

    has_cera = ('taiwan_billing', 'cash_cera') in \
        existing_tables(conn, [('taiwan_billing', 'cash_cera')])
    if has_cera:
        join = ('LEFT JOIN taiwan_billing.cash_cera c '
                'ON c.account = CAST(a.UID AS CHAR) ')
        cera_expr = 'IFNULL(c.cera, 0)'
    else:
        join = ''
        cera_expr = '0'
        log.warning('taiwan_billing.cash_cera 不存在，用户列表点券列显示为 0')

    # 角色数量：taiwan_cain.charac_info，m_id 即账号 UID；表缺失时计 0
    has_charac = ('taiwan_cain', 'charac_info') in \
        existing_tables(conn, [('taiwan_cain', 'charac_info')])
    if has_charac:
        charac_expr = ('(SELECT COUNT(*) FROM taiwan_cain.charac_info ci '
                       'WHERE ci.m_id = a.UID)')
    else:
        charac_expr = '0'
        log.warning('taiwan_cain.charac_info 不存在，用户列表角色数显示为 0')

    # 封号状态：d_taiwan.member_punish_info 的 punish_type=1 且未到期；
    # 表缺失时视为未封号
    has_punish = ('d_taiwan', 'member_punish_info') in \
        existing_tables(conn, [('d_taiwan', 'member_punish_info')])
    if has_punish:
        ban_expr = ('EXISTS(SELECT 1 FROM d_taiwan.member_punish_info p '
                    'WHERE p.m_id = a.UID AND p.punish_type = 1 '
                    'AND p.end_time > NOW())')
    else:
        ban_expr = '0'

    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM d_taiwan.accounts a ' + where, params)
        total = cur.fetchone()[0]
        cur.execute(
            'SELECT a.UID, a.accountname, ' + cera_expr + ', ' + charac_expr +
            ', ' + ban_expr + ' '
            'FROM d_taiwan.accounts a ' + join + where +
            ' ORDER BY a.UID DESC LIMIT %s OFFSET %s',
            params + [limit, offset])
        rows = cur.fetchall()
    users = [{'uid': r[0], 'accountname': r[1], 'cera': int(r[2]),
              'charac_count': int(r[3]), 'banned': bool(r[4])} for r in rows]
    return users, total


def get_cera(conn, uid):
    """查询点券；cash_cera 表不存在时视为 0。"""
    if ('taiwan_billing', 'cash_cera') not in \
            existing_tables(conn, [('taiwan_billing', 'cash_cera')]):
        return 0
    with conn.cursor() as cur:
        cur.execute('SELECT cera FROM taiwan_billing.cash_cera WHERE account = %s',
                    (str(uid),))
        row = cur.fetchone()
    return int(row[0]) if row else 0


def add_cera(conn, uid, amount):
    """为用户加点券（amount 为负数即扣减）。无点券记录时自动创建。

    cash_cera 表不存在时报错——充值无法被“跳过”。
    """
    if ('taiwan_billing', 'cash_cera') not in \
            existing_tables(conn, [('taiwan_billing', 'cash_cera')]):
        raise RuntimeError('taiwan_billing.cash_cera 表不存在，无法充值')
    with conn.cursor() as cur:
        cur.execute('SELECT cera FROM taiwan_billing.cash_cera WHERE account = %s',
                    (str(uid),))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                'INSERT INTO taiwan_billing.cash_cera '
                '(account, cera, mod_date, reg_date) VALUES (%s, %s, NOW(), NOW())',
                (str(uid), max(amount, 0)))
            return max(amount, 0)
        new_value = int(row[0]) + amount
        if new_value < 0:
            new_value = 0
        cur.execute('UPDATE taiwan_billing.cash_cera SET cera = %s, mod_date = NOW() '
                    'WHERE account = %s', (new_value, str(uid)))
        return new_value


# ---------------------------------------------------------------------------
# 注册（按需求顺序写入多库多表；MyISAM 无事务，失败时逐表清理）
# ---------------------------------------------------------------------------

# (schema, table, sql, 参数形态)
_REGISTER_STEPS = [
    ('d_taiwan', 'limit_create_character',
     'INSERT INTO d_taiwan.limit_create_character (m_id) VALUES (%s)', 'uid'),
    ('d_taiwan', 'member_info',
     'INSERT INTO d_taiwan.member_info (m_id, user_id) VALUES (%s, %s)', 'uid_uid'),
    ('d_taiwan', 'member_join_info',
     'INSERT INTO d_taiwan.member_join_info (m_id) VALUES (%s)', 'uid'),
    ('d_taiwan', 'member_miles',
     'INSERT INTO d_taiwan.member_miles (m_id) VALUES (%s)', 'uid'),
    ('d_taiwan', 'member_white_account',
     'INSERT INTO d_taiwan.member_white_account (m_id) VALUES (%s)', 'uid'),
    ('taiwan_login', 'member_login',
     'INSERT INTO taiwan_login.member_login (m_id) VALUES (%s)', 'uid'),
    ('taiwan_billing', 'cash_cera',
     'INSERT INTO taiwan_billing.cash_cera '
     '(account, cera, mod_date, reg_date) VALUES (%s, %s, NOW(), NOW())', 'cera'),
    ('taiwan_billing', 'cash_cera_point',
     'INSERT INTO taiwan_billing.cash_cera_point '
     '(account, cera_point, reg_date, mod_date) VALUES (%s, 0, NOW(), NOW())',
     'account'),
    ('taiwan_cain_2nd', 'member_avatar_coin',
     'INSERT INTO taiwan_cain_2nd.member_avatar_coin (m_id) VALUES (%s)', 'uid'),
    ('d_gmaster', 'login',
     'INSERT INTO d_gmaster.login (uid, reg_time) VALUES (%s, NOW())', 'uid'),
]

# 注册从表写入的参数构造器：kind -> (uid) -> SQL 参数元组
_ROW_PARAMS = {
    'uid': lambda uid: (uid,),
    'uid_uid': lambda uid: (uid, uid),
    'cera': lambda uid: (str(uid), store.get_register_init_cera()),
    'account': lambda uid: (str(uid),),
}


# 清理/存在性检查用的表清单（schema.table, 列名）。
# 注意：taiwan_login_play.member_key_option 由 d_taiwan.accounts 上的
# AFTER INSERT 触发器自动写入（复合主键 m_id+key_type），注册时无需显式
# INSERT，但删除账号时必须一并清理，否则残留主键会导致同 UID 复用失败。
REGISTER_TABLES = [
    ('d_taiwan.limit_create_character', 'm_id'),
    ('d_taiwan.member_info', 'm_id'),
    ('d_taiwan.member_join_info', 'm_id'),
    ('d_taiwan.member_miles', 'm_id'),
    ('d_taiwan.member_white_account', 'm_id'),
    ('taiwan_login.member_login', 'm_id'),
    ('taiwan_billing.cash_cera', 'account'),
    ('taiwan_billing.cash_cera_point', 'account'),
    ('taiwan_cain_2nd.member_avatar_coin', 'm_id'),
    ('d_gmaster.login', 'uid'),
    ('taiwan_login_play.member_key_option', 'm_id'),
]


def register_account(conn, username, md5_password):
    """执行需求规定的注册 SQL 序列。

    返回 (uid, skipped)：uid 为新账号 UID；skipped 为因表不存在而跳过写入的
    表名列表（形如 ['d_gmaster.login']）。已存在的表照常写入，任一已存在表
    写入失败仍回滚清理并抛异常；核心表 d_taiwan.accounts 缺失时直接抛错。
    """
    needed = [('d_taiwan', 'accounts')] + [(s, t) for s, t, _, _ in _REGISTER_STEPS]
    existing = existing_tables(conn, needed)
    if ('d_taiwan', 'accounts') not in existing:
        raise RuntimeError('核心表 d_taiwan.accounts 不存在，无法注册')

    skipped = []
    with conn.cursor() as cur:
        cur.execute('SELECT UID FROM d_taiwan.accounts WHERE accountname = %s',
                    (username,))
        if cur.fetchone() is not None:
            raise DuplicateUsernameError(username)

        # 空库时第一个账号使用指定起始 UID（显式插入，纯 DML；
        # 之后 AUTO_INCREMENT 会自动跟随到该值之后）
        cur.execute('SELECT COUNT(*) FROM d_taiwan.accounts')
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO d_taiwan.accounts (UID, accountname, password, qq) "
                "VALUES (%s, %s, %s, '000000')",
                (config.FIRST_ACCOUNT_UID, username, md5_password))
        else:
            cur.execute(
                "INSERT INTO d_taiwan.accounts (accountname, password, qq) "
                "VALUES (%s, %s, '000000')", (username, md5_password))
        uid = cur.lastrowid

        try:
            for schema, table, sql, kind in _REGISTER_STEPS:
                if (schema, table) not in existing:
                    skipped.append('%s.%s' % (schema, table))
                    log.warning('表 %s.%s 不存在，注册写入已跳过（uid=%s）',
                                schema, table, uid)
                    continue
                # kind='cera' 时注册赠送点券取后台「参数设置」运行时值
                # （未设置回退 config 默认）
                cur.execute(sql, _ROW_PARAMS[kind](uid))
        except Exception:
            log.exception('注册写入从表失败，回滚清理 uid=%s', uid)
            _cleanup_register(cur, uid, existing)
            raise
    if skipped:
        log.warning('注册 uid=%s 跳过缺失表：%s', uid, ', '.join(skipped))
    return uid, skipped


def _cleanup_register(cur, uid, existing=None):
    """删除 uid 在各从表及主表中已写入的记录。缺失的表自动跳过。"""
    for table, col in REGISTER_TABLES:
        schema, name = table.split('.', 1)
        if existing is not None and (schema, name) not in existing:
            continue
        try:
            cur.execute('DELETE FROM {} WHERE {} = %s'.format(table, col), (uid,))
        except Exception:
            log.exception('清理 %s 失败', table)
    try:
        cur.execute('DELETE FROM d_taiwan.accounts WHERE UID = %s', (uid,))
    except Exception:
        log.exception('清理 d_taiwan.accounts 失败')


def delete_account_everywhere(conn, uid):
    """彻底删除一个测试账号的所有痕迹（供测试/维护使用）。"""
    existing = existing_tables(
        conn, [t.split('.', 1) for t, _ in REGISTER_TABLES] + [('d_taiwan', 'accounts')])
    with conn.cursor() as cur:
        _cleanup_register(cur, uid, existing)


class DuplicateUsernameError(Exception):
    pass
