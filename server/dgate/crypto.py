# -*- coding: utf-8 -*-
"""登录签名字符串生成。

按需求：
1. 基础字符串中 %08x 替换为 UID 的 8 位小写十六进制（共 92 个字符）；
2. 基础字符串转换为 byte 数组：hexToBin，每两个字符对应一个 byte（46 字节）；
3. 使用 RSA-2048 私钥（privatekey.pem）加密该 byte 数组；
4. 结果 Base64 编码即为签名字符串。

“私钥加密”采用 PKCS#1 v1.5 Type-1 填充，与 openssl_private_encrypt
（DNF 常见 gate 实现使用的原语）完全兼容；游戏端可用公钥还原出明文。
"""
import base64
import logging

from Crypto.PublicKey import RSA
from Crypto.Util.number import bytes_to_long, long_to_bytes

import config

log = logging.getLogger(__name__)

_key_cache = {}


def load_private_key(path=None):
    path = path or config.PRIVATE_KEY_PATH
    if path not in _key_cache:
        with open(path, 'rb') as f:
            _key_cache[path] = RSA.import_key(f.read())
    return _key_cache[path]


def reload_private_key(path=None):
    """重新加载私钥并刷新缓存（密钥文件被更新后调用，无需重启服务）。"""
    path = path or config.PRIVATE_KEY_PATH
    with open(path, 'rb') as f:
        key = RSA.import_key(f.read())
    _key_cache[path] = key
    return key


def _pkcs1_v15_type1_pad(data, key_size):
    """PKCS#1 v1.5 签名块：00 01 FF..FF 00 || D（openssl_private_encrypt 同款）。"""
    max_len = key_size - 11
    if len(data) > max_len:
        raise ValueError('payload too long for RSA key: %d > %d' % (len(data), max_len))
    return b'\x00\x01' + b'\xff' * (key_size - 3 - len(data)) + b'\x00' + data


def php_hex_to_bin(s):
    """与项目根目录 hextobin.php 的 HexTostr 逐位一致的实现。

    PHP 原文语义：
    - 每 2 个字符产出 1 字节：高半字节取第 1 个字符、低半字节取第 2 个；
    - '0'..'9'（48~57）映射 0~9，其余字符按 ord(c) - 97 + 10 换算（不校验合法性）；
    - 半字节分别与 0xf0 / 0x0f 掩码（非法字符按位截断而非报错）；
    - 奇数长度时最后一组第 2 字符越界，PHP ord('') = 0 -> (0-97+10) & 0x0f = 9。
    对合法小写 hex 串（本项目基础字符串）输出与 bytes.fromhex 相同。
    """
    def nibble(ch):
        x = ord(ch)
        return x - 48 if 48 <= x < 58 else x - 97 + 10

    out = bytearray()
    n = len(s)
    for i in range(0, n, 2):
        x1 = nibble(s[i])
        x2 = nibble(s[i + 1]) if i + 1 < n else (0 - 97 + 10)  # PHP 越界行为
        out.append(((x1 << 4) & 0xf0) | (x2 & 0x0f))
    return bytes(out)


def _payload_bytes(formatted):
    """基础字符串 -> byte 数组：默认按 hextobin.php 的 hexToBin 算法。"""
    if config.TOKEN_PAYLOAD_ENCODING == 'hex':
        return php_hex_to_bin(formatted)
    return formatted.encode('ascii')


def build_token(uid, private_key=None):
    """生成登录签名字符串（Base64）。"""
    key = private_key or load_private_key()
    base_str = config.TOKEN_TEMPLATE % uid
    data = _payload_bytes(base_str)
    em = _pkcs1_v15_type1_pad(data, key.size_in_bytes())
    m = bytes_to_long(em)
    sig = pow(m, key.d, key.n)          # RSA 私钥运算
    return base64.b64encode(long_to_bytes(sig, key.size_in_bytes())).decode('ascii')


def verify_token(token_b64, public_key):
    """用公钥还原签名字符串，返回明文 byte 数组（供测试/联调对拍使用）。"""
    em = long_to_bytes(pow(bytes_to_long(base64.b64decode(token_b64)),
                           public_key.e, public_key.n),
                       public_key.size_in_bytes())
    if em[0:2] != b'\x00\x01':
        raise ValueError('bad padding header')
    sep = em.index(b'\x00', 2)
    if any(b != 0xFF for b in em[2:sep]) or sep < 10:
        raise ValueError('bad padding string')
    return em[sep + 1:]
