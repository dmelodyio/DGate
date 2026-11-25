# pvfitem.py : 服务端 PVF 道具清单解析（stackable.lst id → 道具名称）
# 解析逻辑复刻自 docs/pvf_research 的研究成果（decrypt_bytes 旋转解密 +
# 文件树 + stringtable + stackable.lst + [name] 段提取 + type9 str 引用解析）。
import json
import logging
import os
import struct
import threading

import config
from .serverctl import PVF_PATH

log = logging.getLogger(__name__)

XOR_KEY = 0x81A79011
CACHE_PATH = os.path.join(os.path.dirname(config.SQLITE_PATH), 'pvf_items.json')
LIST_PATH = 'stackable/stackable.lst'
STRINGTABLE_PATH = 'stringtable.bin'
N_STRING_PATH = 'n_string.lst'
ENCODE = 'big5'                 # 默认/兜底编码：简体经工具链转繁体 BIG5 存储
_CANDIDATE_ENCODINGS = ('big5', 'gbk')   # 仅限中文编码：繁体 big5 / 简体 gbk
# 常用简体汉字 + 游戏高频词（zhconv 归一化后统计命中率，用于鉴别两种中文编码）
_COMMON_HANS = set(
    '的一是了我不人在他有这上们来到时大地为子中你说生国年着就那和要她出也得里'
    '后自以会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第'
    '样道想作种开美总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位'
    '分爱老因很给名法间知世什两次使身者被高已亲其进此话常与活正感见明白水火暗'
    '光血魔力攻击防御速度量品级装备道具药剂恢复生命值经验等级增加减少提升强化'
    '升级封印解除稀有普通高级传说史诗神器玄铁金银铜剑刀枪杖盾甲衣袍靴鞋头盔项链'
    '戒指手镯饰品材料箱包礼卷点卷')


def _sample_stringtable(st, limit=60):
    """从 stringtable 抽样非 ASCII 串（最多 limit 条）。"""
    samples = []
    step = max(1, st.count // 400)
    for i in range(0, st.count, step):
        b = st.body[st.offsets[i]:st.offsets[i + 1]]
        if b and any(x > 127 for x in b):
            samples.append(b)
        if len(samples) >= limit:
            break
    return samples


def _ordered_str_paths(nl, st):
    """n_string.lst 的 str 文件路径，按采样优先级排序（stackable 优先）。"""
    paths = [(sid, st[e[1]].replace('\r', '').strip().lower())
             for sid, e in enumerate(nl.entries)]
    return ([p for p in paths if 'stackable' in p[1]]
            + [p for p in paths if 'skill' in p[1] or 'equipment' in p[1]]
            + paths[:2])


def _collect_str_value_samples(body, samples, limit):
    """单文件值采样（名称载体双倍权重）；累计达到 limit 返回 True。"""
    for line in body.split(b'\n'):
        line = line.strip()
        if b'>' not in line or line.startswith(b'//'):
            continue
        val = line.split(b'>', 1)[1]
        if val and any(x > 127 for x in val):
            samples.append(val)
            samples.append(val)      # 名称载体：双倍权重
            if len(samples) >= limit:
                return True
    return False


def _sample_str_values(pvf, st, samples, limit=140):
    """追加 str 文件值样本（双倍权重；最多累计 limit 条）。

    路径为 ASCII，用默认编码读不影响定位。韩版框架 PVF 的 stringtable 存
    韩文框架串，名称实际位于 str 文件的繁中本地化值里，仅靠 stringtable
    抽样会误判，故优先 stackable 表——道具名称的真正载体。
    """
    try:
        nl = _Lst(pvf.read_file(N_STRING_PATH))
        for _sid, path in _ordered_str_paths(nl, st)[:3]:
            try:
                body = pvf.read_file(path)
            except Exception:
                continue
            if _collect_str_value_samples(body, samples, limit):
                break
    except Exception:
        log.debug('str 文件抽样失败（忽略，仅用 stringtable 样本）', exc_info=True)


def _chinese_quality(text: str) -> int:
    """文本的「常用汉字命中率」得分（zhconv 归一化后）：正确编码的中文
    命中率高，错误编码产生随机生僻字得分低。"""
    good = bad = 0
    for ch in _zh(text):
        if ch in _COMMON_HANS:
            good += 1
        elif '\u4e00' <= ch <= '\u9fff':
            bad += 1
    return good - bad


def _detect_encode(pvf: '_PVF') -> str:
    """探测 PVF 字符串的中文编码（big5 繁体 / gbk 简体，二者择优）。

    抽样来源：stringtable 非 ASCII 串 + str 文件的值（双倍权重）。对每个
    候选编码解码并统计常用汉字命中率，取分高者。全 ASCII 或样本不足时
    回退 big5。其他语言文字（韩文等）两种编码得分都趋近于零，最终由
    _looks_chinese 逐条丢弃。
    """
    st = _StringTable(pvf.read_file(STRINGTABLE_PATH))
    samples = _sample_stringtable(st)
    _sample_str_values(pvf, st, samples)
    if not samples:
        return ENCODE

    best, best_score = ENCODE, None
    for enc in _CANDIDATE_ENCODINGS:
        score = sum(_chinese_quality(b.decode(enc, 'replace')) for b in samples)
        if best_score is None or score > best_score:
            best, best_score = enc, score
    return best


# 非中文文字（韩文/日文假名/西里尔/希腊等）不解析直接丢弃：
# 仅允许 ASCII 可见字符、CJK 统一表意、CJK 标点、全角形式与常用中文引号省略号
_ALLOWED_CHAR_RANGES = (
    (0x20, 0x7E),        # ASCII 可见字符
    (0xB7, 0xB7),        # 间隔号 ·
    (0x2018, 0x201D),    # ‘ ’ “ ”
    (0x2026, 0x2026),    # …
    (0x3000, 0x303F),    # CJK 标点
    (0x4E00, 0x9FFF),    # CJK 统一表意
    (0xFF00, 0xFFEF),    # 全角形式
)


def _looks_chinese(text: str) -> bool:
    """名称是否只含中文/ASCII 允许字符（非空）。其他语言文字返回 False。"""
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        if not any(lo <= cp <= hi for lo, hi in _ALLOWED_CHAR_RANGES):
            return False
    return True

try:
    from zhconv import convert as _zh_convert
except ImportError:
    _zh_convert = None

_LOCK = threading.Lock()


class PVFError(Exception):
    """PVF 文件缺失或解析失败。"""


def decrypt_bytes(data: bytes, crc: int) -> bytes:
    """每 4 字节 word 与 (crc ^ XOR_KEY) 异或后循环右移 6 位。"""
    n = len(data) // 4
    if n == 0:
        return b''
    xk = (crc ^ XOR_KEY) & 0xFFFFFFFF
    words = struct.unpack('<%dI' % n, data[:n * 4])
    return struct.pack('<%dI' % n,
                       *((((w ^ xk) >> 6) | ((w ^ xk) << 26)) & 0xFFFFFFFF
                         for w in words))


class _PVF:
    """最小 PVF 读取器：头部 + 解密文件树 + 按路径读解密文件。"""

    def __init__(self, path: str):
        with open(path, 'rb') as fp:
            self.data = fp.read()
        (self.uuid_len,) = struct.unpack_from('<i', self.data, 0)
        (self.version, self.tree_len,
         self.tree_crc, self.num_files) = struct.unpack_from('<iiII', self.data,
                                                             4 + self.uuid_len)
        self.header_len = 20 + self.uuid_len
        self.data_shift = self.header_len + self.tree_len
        tree = decrypt_bytes(self.data[self.header_len:
                                       self.header_len + self.tree_len],
                             self.tree_crc & 0xFFFFFFFF)
        self.files = []
        idx = 0
        for _ in range(self.num_files):
            (_fn,) = struct.unpack_from('<I', tree, idx)
            (plen,) = struct.unpack_from('<I', tree, idx + 4)
            path = tree[idx + 8:idx + 8 + plen].decode('ascii', 'replace').lower()
            size, crc, off = struct.unpack_from('<III', tree, idx + 8 + plen)
            idx += 8 + plen + 12
            self.files.append({'path': path.lstrip('/'), 'size': size,
                               'crc': crc, 'offset': off})
        self.by_path = {f['path']: f for f in self.files}

    def read_file(self, path: str) -> bytes:
        leaf = self.by_path[path.lower().lstrip('/')]
        read_len = (leaf['size'] + 3) & 0xFFFFFFFC
        start = self.data_shift + leaf['offset']
        return decrypt_bytes(self.data[start:start + read_len], leaf['crc'])


class _StringTable:
    """stringtable.bin：count(4) + 累计偏移数组(N+1 个 uint32) + 字节流。"""

    def __init__(self, data: bytes, encode: str = ENCODE):
        (self.count,) = struct.unpack_from('<I', data, 0)
        self.encode = encode
        self.body = data[4:]
        self.offsets = struct.unpack_from('<%dI' % (self.count + 1), self.body, 0)

    def __getitem__(self, n: int) -> str:
        return self.body[self.offsets[n]:self.offsets[n + 1]].decode(
            self.encode, 'ignore')


class _Lst:
    """*.lst：vercode(2) + 循环 bIbI（type1+id+type2+strIdx）。"""

    def __init__(self, data: bytes):
        self.entries = []
        i = 2
        while i + 10 <= len(data):
            a, aa, b, bb = struct.unpack_from('<bIbI', data, i)
            i += 10
            if a == 2 and b == 7:
                self.entries.append((aa, bb))
            elif a == 7 and b == 2:
                self.entries.append((bb, aa))


def _decode_units(units, st, get_str_table):
    """把 (type, value) 单元流转为可读值；type9 按 str 表二次解析。

    新版 PVF（如 version 66282 韩服）：type9（str 表 id）+ type10（key 字符串）
    成对出现，key 单元被消费不再单独输出；type10 单独出现时按字符串引用解码。
    """
    out = []
    consumed = 0
    for k, (t, v) in enumerate(units):
        if consumed:
            consumed -= 1
            out.append('')          # 占位保持索引对齐（被消费的 key 单元）
            continue
        if t == 4:
            out.append(struct.unpack('<f', struct.pack('<i', v))[0])
        elif t in (5, 6, 7, 8):
            out.append(st[v])
        elif t == 9:
            # 值 = n_string.lst 的 str 文件 id；下一单元的字符串为 key
            nxt = units[k + 1] if k + 1 < len(units) else None
            if nxt is not None and nxt[0] == 10:
                consumed = 1        # 新版格式：type10 key 单元随之消费
            key = st[nxt[1]] if nxt is not None else ''
            out.append(get_str_table(v).get(key, ''))
        elif t == 10:
            out.append(st[v])
        else:
            out.append(v)
    return out


def _item_name(data: bytes, st, get_str_table) -> str:
    """提取 [name] 段落下的全部值（到下一个段落键为止）并拼接。"""
    n = (len(data) - 2) // 5
    units = []
    i = 2
    for _ in range(n):
        t = data[i]
        (v,) = struct.unpack_from('<i', data, i + 1)
        i += 5
        units.append((t, v))
    decoded = _decode_units(units, st, get_str_table)
    name = ''
    in_name = False
    for k, (t, _v) in enumerate(units):
        v = decoded[k]
        if t == 5:
            if in_name:
                break               # [name] 段落结束
            in_name = (v == '[name]')
            continue
        if in_name:
            name += v if isinstance(v, str) else str(v)
    return name.replace('\r', '').strip()


def _zh(text: str) -> str:
    if _zh_convert is not None:
        try:
            return _zh_convert(text, 'zh-cn')
        except Exception:
            return text
    return text


def extract_items(pvf_path: str = PVF_PATH):
    """解析 PVF，返回 [{'id', 'name'(简体/原文), 'name_tc'(原文)}]。

    统一按 big5 解析（PVF 制作工具链将简体中文转繁体 BIG5 存储）；
    名称含其他语言文字（韩文等）或为空的条目直接丢弃，不进入清单。
    """
    if not os.path.exists(pvf_path):
        raise PVFError('PVF 文件不存在: %s' % pvf_path)
    pvf = _PVF(pvf_path)
    encode = _detect_encode(pvf)
    log.info('PVF 中文字符串编码探测：%s（big5=繁体 / gbk=简体，默认 big5）', encode)
    st = _StringTable(pvf.read_file(STRINGTABLE_PATH), encode)
    n_str = _Lst(pvf.read_file(N_STRING_PATH))
    # str 文件与 stringtable 同编码（实证：本地化 PVF 的 str 值为 big5 繁中，
    # 如「攻擊力 +12%%」；旧 utf-8 假设从未被名称路径实际使用过）
    str_encode = encode
    str_tables = {}

    def get_str_table(sid):
        table = str_tables.get(sid)
        if table is None:
            # entries[sid][1] 是 stringtable 索引（整数），需经 st 转为文件路径
            path = st[n_str.entries[sid][1]].replace('\r', '').strip().lower()
            table = {}
            for line in pvf.read_file(path).decode(str_encode, 'ignore').split('\n'):
                if '>' in line:
                    kk, vv = line.split('>', 1)
                    table[kk.strip()] = vv.replace('\r', '').strip()
            str_tables[sid] = table
        return table

    items = []
    for item_id, sidx in _Lst(pvf.read_file(LIST_PATH)).entries:
        rel = st[sidx].replace('\r', '').strip()
        full = ('stackable/' + rel).lower()
        try:
            name_tc = _item_name(pvf.read_file(full), st, get_str_table)
        except Exception:
            continue            # 单个文件失败跳过，不阻塞清单
        if not _looks_chinese(name_tc):
            continue            # 其他语言文字（韩文等）不解析，直接丢弃
        items.append({'id': item_id,
                      'name': _zh(name_tc) if name_tc else '',
                      'name_tc': name_tc})
    return items


def build_cache(pvf_path: str = PVF_PATH) -> dict:
    """解析 PVF 并写缓存 JSON，返回缓存内容。"""
    items = extract_items(pvf_path)
    stat = os.stat(pvf_path)
    cache = {'pvf_size': stat.st_size, 'pvf_mtime': int(stat.st_mtime),
             'count': len(items), 'items': items}
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump(cache, fp, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)
    return cache


def _cache_valid(cache: dict, pvf_path: str) -> bool:
    if not os.path.exists(pvf_path):
        raise PVFError('PVF 文件不存在: %s' % pvf_path)
    stat = os.stat(pvf_path)
    return (cache.get('pvf_mtime') == int(stat.st_mtime)
            and cache.get('pvf_size') == stat.st_size
            and isinstance(cache.get('items'), list))


# ---------------- 构建状态机 ----------------
# 缓存策略（用户约定）：仅服务启动时和管理员更新 PVF 时各构建一次；
# 构建期间（后台线程，约 10~30 秒）锁定邮件发送、启动服务器、更换 PVF。

_STATE_LOCK = threading.Lock()
_BUILDING = False
_LAST_ERROR = None


def start_build(force: bool = False) -> bool:
    """后台线程启动一次清单构建；已在构建中返回 False。

    force=False 时缓存仍有效（PVF 未变化）则跳过；force=True 无条件重建。
    PVF 文件缺失时抛 PVFError。
    """
    global _BUILDING, _LAST_ERROR
    if not os.path.exists(PVF_PATH):
        raise PVFError('PVF 文件不存在: %s' % PVF_PATH)
    with _STATE_LOCK:
        if _BUILDING:
            return False
        if not force and os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, 'r', encoding='utf-8') as fp:
                    cache = json.load(fp)
                if _cache_valid(cache, PVF_PATH):
                    return False       # 缓存已有效，无需重建
            except (ValueError, OSError):
                pass
        _BUILDING = True
        _LAST_ERROR = None

    def _worker():
        global _BUILDING, _LAST_ERROR
        try:
            build_cache(PVF_PATH)
            _LAST_ERROR = None
            log.info('PVF 道具清单构建完成')
        except Exception as e:                       # noqa: BLE001
            log.exception('PVF 道具清单构建失败')
            _LAST_ERROR = str(e)
        finally:
            with _STATE_LOCK:
                _BUILDING = False

    threading.Thread(target=_worker, daemon=True, name='pvf-item-build').start()
    return True


def is_building() -> bool:
    return _BUILDING


def last_error():
    return _LAST_ERROR


def get_items(pvf_path: str = PVF_PATH) -> dict:
    """读取当前缓存清单；缺失/损坏/PVF 已变化时抛 PVFError。

    缓存的生成只发生在服务启动与管理员更新 PVF（含手动「重建清单」）时，
    本函数不再触发构建。
    """
    if _BUILDING:
        raise PVFError('道具清单正在重建，请稍候')
    if not os.path.exists(CACHE_PATH):
        raise PVFError('道具清单尚未生成，请等待启动构建或手动重建')
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as fp:
            cache = json.load(fp)
    except (ValueError, OSError):
        raise PVFError('道具清单缓存损坏，请重建')
    if not _cache_valid(cache, pvf_path):
        raise PVFError('PVF 已更新，道具清单待重建')
    return cache
