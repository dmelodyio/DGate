# -*- coding: utf-8 -*-
"""客户端接口参数校验。"""
import hashlib
import re
import secrets
import string

USERNAME_RE = re.compile(r'^[A-Za-z0-9]{6,16}$')
# 管理员用户名：3~32 位字母、数字、下划线
ADMIN_USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,32}$')
# 密码：8~32 位可见 ASCII（英文字母、数字、符号）
PASSWORD_RE = re.compile(r'^[\x21-\x7e]{8,32}$')


def valid_username(username):
    return isinstance(username, str) and USERNAME_RE.match(username) is not None


def valid_admin_username(username):
    return isinstance(username, str) and ADMIN_USERNAME_RE.match(username) is not None


def valid_password(password):
    return isinstance(password, str) and PASSWORD_RE.match(password) is not None


def md5_hex(password):
    """入库密码：32 位小写 MD5。"""
    return hashlib.md5(password.encode('utf-8')).hexdigest()


def random_password(length=12):
    """生成无歧义字符的随机密码（后台重置密码用）。"""
    alphabet = ''.join(c for c in string.ascii_letters + string.digits
                       if c not in 'O0oIl1')
    return ''.join(secrets.choice(alphabet) for _ in range(length))
