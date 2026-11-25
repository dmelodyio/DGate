# maildb.py : taiwan_cain_2nd 邮件写入（letter 表 + postal 表，letter_id 关联）
# 字段映射（2026-11-24 与用户确认；stat 语义经游戏内实测修正）：
#   letter: charac_no=收件人, send_charac_no=0, send_charac_name=发件人,
#           letter_text=正文, reg_date=NOW(), stat=1（游戏内实测：stat 控制正文
#           是否在游戏中显示，0 不显示 / 1 显示；并非已读未读标记）
#   postal: 每附件一行；金币放第一个附件行的 gold 列（其余行 0）；
#           纯金币邮件单独一行 item_id=0 + add_info=金币数 + gold=金币数；
#           add_info(附件行)=数量；occ_time=NOW(), delete_flag=0（未领取），
#           unlimit_flag=1（用户约定：附件一律无限制标记）
#   角色表: taiwan_cain.charac_info（charac_no / charac_name / m_id / delete_flag）
# 编码约定（2026-11-24 起双形态兼容，HEX 探查证实）：
#   - 旧生产（游戏经 cp1252 连接，MySQL 5.1）：utf8 列内为双重编码变形形态
#     （如 居民代表 → 'å±…æ°‘ä»£è¡¨'），写入/检索用 _u2l() 复刻、读出用 _l2u() 还原；
#   - 新部署（游戏经 utf8 连接，如 MySQL 5.7.44 全新安装）：列内为干净 UTF-8。
#   读取：_l2u 还原失败自动原样返回（天然兼容干净形态）；
#   检索：关键词同时按「原文 + 变形」双形态匹配（修复新部署搜索不到角色）；
#   写入：按 _storage_legacy() 探测结果决定——干净形态直接写原文（游戏端
#   才能正常显示），变形形态沿用 _u2l。
#   varchar 长度：变形形态按变形后字符数（letter_text 255 → 中文约 85 字），
#   干净形态按原文字符数（255 字）。
# 红线：只 INSERT，绝不 UPDATE/DELETE 存量邮件。

import logging

log = logging.getLogger(__name__)

CHARAC_TABLE = 'taiwan_cain.charac_info'
LETTER_TABLE = 'taiwan_cain_2nd.letter'
POSTAL_TABLE = 'taiwan_cain_2nd.postal'

MAX_SENDER_STORED = 20        # send_charac_name varchar(20)，按变形后字符数
MAX_TEXT_STORED = 255         # letter_text varchar(255)，按变形后字符数

# MySQL cp1252 字节↔字符双向表：Windows cp1252 已定义字节用其字符，
# 5 个未定义字节（0x81 0x8D 0x8F 0x90 0x9D）按 latin1 回退（与 MySQL 行为一致）。
_UNDEF = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))
_BYTE_TO_CHAR = []
_CHAR_TO_BYTE = {}
for _b in range(256):
    if _b in _UNDEF:
        _ch = chr(_b)
    else:
        _ch = bytes([_b]).decode('cp1252')
    _BYTE_TO_CHAR.append(_ch)
    _CHAR_TO_BYTE[_ch] = _b

_LETTER_SQL = ("INSERT INTO %s (letter_id, charac_no, send_charac_no, "
               "send_charac_name, letter_text, reg_date, stat) "
               "VALUES (%%s, %%s, 0, %%s, %%s, NOW(), 1)" % LETTER_TABLE)

_POSTAL_SQL = ("INSERT INTO %s (occ_time, send_charac_no, send_charac_name, "
               "receive_charac_no, item_id, add_info, gold, unlimit_flag, "
               "letter_id) "
               "VALUES (NOW(), 0, %%s, %%s, %%s, %%s, %%s, 1, %%s)" % POSTAL_TABLE)

# postal 其余列全部留默认：endurance/upgrade/amplify_*/avata_flag/
# seal_flag/creature_flag/postal/type/extend_info/ipg_*/auction_id/random_option/
# seperate_upgrade/item_guid = 0/''；receive_time = '0000-00-00 00:00:00'（未领取）


def _u2l(text):
    """UTF-8 文本 → cp1252 形态（与游戏服务器的 cp1252 连接写入习惯一致）。

    UTF-8 的每个字节经 cp1252（含 latin1 回退）映射为一个字符，经 utf8 连接
    写入后落库字节与游戏写入完全一致。
    """
    return ''.join(_BYTE_TO_CHAR[b] for b in text.encode('utf-8'))


def _l2u(text):
    """cp1252 形态 → UTF-8 文本（读取方向）。失败时原样返回。"""
    try:
        return bytes(_CHAR_TO_BYTE[ch] for ch in text).decode('utf-8')
    except (KeyError, UnicodeDecodeError):
        return text


_STORAGE_MODE = None    # None=未探测；True=变形（旧生产）；False=干净 UTF-8


def _reset_storage_mode():
    """重置存储形态探测缓存（测试用）。"""
    global _STORAGE_MODE
    _STORAGE_MODE = None


def _sample_is_legacy(sample):
    """取样角色名判断存储形态：变形形态能整体还原为合法 UTF-8 且含非 ASCII。
    干净 UTF-8 的中文在 cp1252 映射表中不存在（KeyError）→ 判为干净；
    纯 ASCII 两种形态字节一致，按干净处理（行为等价）。"""
    if not sample:
        return False
    try:
        bytes(_CHAR_TO_BYTE[ch] for ch in sample).decode('utf-8')
    except (KeyError, UnicodeDecodeError):
        return False
    return any(ord(ch) > 127 for ch in sample)


def _storage_legacy(conn):
    """探测并缓存角色名的存储形态（取样一行未删除角色）。

    探测失败或无角色时按干净 UTF-8 处理（新部署默认）。
    """
    global _STORAGE_MODE
    if _STORAGE_MODE is None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT charac_name FROM %s WHERE delete_flag = 0 "
                            "LIMIT 1" % CHARAC_TABLE)
                row = cur.fetchone()
            sample = row[0] if row else None
        except Exception:
            log.exception('角色名存储形态探测失败，按干净 UTF-8 处理')
            sample = None
        _STORAGE_MODE = _sample_is_legacy(sample)
        log.info('邮件存储形态：%s', 'cp1252 变形（旧生产）' if _STORAGE_MODE
                 else '干净 UTF-8（新部署）')
    return _STORAGE_MODE


def find_character_by_name(conn, name):
    """按角色名精确查找未删除角色，返回 {charac_no, charac_name, m_id} 或 None。

    同时匹配「原文（干净 UTF-8）」与「变形（旧生产）」两种存储形态。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT charac_no, m_id, charac_name FROM %s "
                    "WHERE (charac_name = %%s OR charac_name = %%s) "
                    "AND delete_flag = 0" % CHARAC_TABLE,
                    (name, _u2l(name)))
        row = cur.fetchone()
    if row is None:
        return None
    return {'charac_no': row[0], 'm_id': row[1], 'charac_name': _l2u(row[2])}


def search_characters(conn, keyword, limit=20):
    """按角色名模糊搜索未删除角色（选人下拉用）。keyword 支持部分名字。

    同时匹配「原文（干净 UTF-8）」与「变形（旧生产）」两种存储形态。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT charac_no, charac_name, m_id FROM %s "
                    "WHERE (charac_name LIKE %%s OR charac_name LIKE %%s) "
                    "AND delete_flag = 0 "
                    "ORDER BY charac_no LIMIT %%s" % CHARAC_TABLE,
                    ('%' + keyword + '%', '%' + _u2l(keyword) + '%', int(limit)))
        rows = cur.fetchall()
    return [{'charac_no': r[0], 'charac_name': _l2u(r[1]), 'm_id': r[2]}
            for r in rows]


def count_characters(conn):
    """全服未删除角色数。"""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM %s WHERE delete_flag = 0" % CHARAC_TABLE)
        row = cur.fetchone()
    return int(row[0])


def list_character_ids(conn):
    """全服未删除角色的 charac_no 列表。"""
    with conn.cursor() as cur:
        cur.execute("SELECT charac_no FROM %s WHERE delete_flag = 0" % CHARAC_TABLE)
        rows = cur.fetchall()
    return [int(r[0]) for r in rows]


def validate_mail(sender, text, legacy=True):
    """发件人/正文存储长度校验，超长抛 ValueError。

    legacy=True 按变形后字符数（更严格，供无数据库连接的预检使用）；
    legacy=False（干净 UTF-8）按原文字符数。insert_mail 会以实测形态复核。
    """
    sender_stored = _u2l(sender) if legacy else sender
    text_stored = _u2l(text) if legacy else text
    if len(sender_stored) > MAX_SENDER_STORED:
        raise ValueError('发件人过长（存储编码后 %d/%d 字符）'
                         % (len(sender_stored), MAX_SENDER_STORED))
    if len(text_stored) > MAX_TEXT_STORED:
        raise ValueError('正文过长（存储编码后 %d/%d 字符，%s）'
                         % (len(text_stored), MAX_TEXT_STORED,
                            '中文约 %d 字' % (MAX_TEXT_STORED // 3) if legacy
                            else '干净 UTF-8 形态'))


def _next_letter_id(conn):
    """取下一个安全的 letter_id：letter 与 postal 两表历史最大值 + 1。

    letter 表曾清空但 postal 仍引用历史 letter_id，自增计数器会与之碰撞，
    故显式计算，避免新邮件与历史 postal 关联错乱。
    """
    with conn.cursor() as cur:
        cur.execute("SELECT "
                    "(SELECT IFNULL(MAX(letter_id), 0) FROM %s), "
                    "(SELECT IFNULL(MAX(letter_id), 0) FROM %s)"
                    % (LETTER_TABLE, POSTAL_TABLE))
        a, b = cur.fetchone()
    return int(max(a, b)) + 1


def insert_mail(conn, charac_no, sender, text, gold, items):
    """给单个角色写一封邮件，返回 letter_id。

    items: [(item_id, count), ...]（≤10 种）；金币放第一个附件行的 gold 列，
    无附件时单独一行 item_id=0 + add_info=gold + gold=gold。
    letter_id 显式取 letter/postal 历史最大值 + 1（避免与历史 postal 引用碰撞）。
    发件人/正文超长（按变形后字符数）抛 ValueError；
    postal 写入失败时按 letter_id 回滚删除 letter 行后原样抛出。
    """
    legacy = _storage_legacy(conn)
    validate_mail(sender, text, legacy=legacy)
    sender_stored = _u2l(sender) if legacy else sender
    text_stored = _u2l(text) if legacy else text

    with conn.cursor() as cur:
        letter_id = _next_letter_id(conn)
        cur.execute(_LETTER_SQL, (letter_id, charac_no, sender_stored, text_stored))
        try:
            if items:
                for i, (item_id, count) in enumerate(items):
                    row_gold = gold if i == 0 else 0
                    cur.execute(_POSTAL_SQL, (sender_stored, charac_no,
                                              item_id, count, row_gold, letter_id))
            else:
                cur.execute(_POSTAL_SQL, (sender_stored, charac_no, 0, gold, gold,
                                          letter_id))
        except Exception:
            log.exception('postal 写入失败，回滚 letter_id=%s', letter_id)
            cur.execute("DELETE FROM %s WHERE letter_id = %%s" % LETTER_TABLE,
                        (letter_id,))
            raise
    return letter_id


def send_bulk(conn, charac_nos, sender, text, gold, items):
    """逐角色写邮件，返回 (success_count, failed_charac_nos)。

    单个角色失败不影响后续角色（各自独立回滚）。
    """
    ok = 0
    failed = []
    for charac_no in charac_nos:
        try:
            insert_mail(conn, charac_no, sender, text, gold, items)
            ok += 1
        except Exception:
            log.exception('邮件写入失败 charac_no=%s', charac_no)
            failed.append(charac_no)
    return ok, failed
