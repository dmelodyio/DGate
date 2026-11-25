# -*- coding: utf-8 -*-
"""DGate 服务端配置。

所有运行配置来自同目录的 config.json（首次运行自动生成模板），
同名环境变量（DGATE_ 前缀）可临时覆盖，优先级：环境变量 > config.json > 默认值。
"""
import copy
import json
import logging
import os

log = logging.getLogger(__name__)

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(_SERVER_DIR, 'config.json')

# config.json 的默认模板（首次运行生成，可直接编辑）
_DEFAULT_CONFIG = {
    'listen': {
        'host': '0.0.0.0',
        'port': 5000,
    },
    'ssl': {
        # 启用 HTTPS：填写 PEM 证书与私钥路径；留空则使用 HTTP
        'cert': '',
        'key': '',
    },
    'mysql': {
        'host': '127.0.0.1',
        'port': 3306,
        'user': 'game',
        'password': 'CHANGE_ME',
    },
    'game': {
        # 游戏服务端部署方式：
        #   native = 同机原生部署（/root/run、/root/stop、df_game_r，CentOS 7）
        #   docker = 容器部署（RHEL 8+ 因 glibc 不兼容游戏二进制，只能容器化，
        #            如 1995chen/dnf:centos7-2.1.9.fix1）
        'mode': 'native',
        # docker 模式：游戏容器名（docker ps -a 可见）
        'container': '',
        # docker 模式：镜像内游戏目录（df_game_r / Script.pvf / publickey.pem
        # 所在处）映射到宿主机的路径，1995chen/dnf 默认 /data/data，可被修改
        'data_dir': '',
    },
}


def _load_json():
    """读取 config.json；不存在时生成默认模板并返回默认值。"""
    if not os.path.exists(JSON_PATH):
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(_DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            f.write('\n')
        log.info('已生成默认配置文件：%s，可按需编辑', JSON_PATH)
        return copy.deepcopy(_DEFAULT_CONFIG)
    try:
        with open(JSON_PATH, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        log.exception('config.json 解析失败，使用默认配置')
        return copy.deepcopy(_DEFAULT_CONFIG)
    if not isinstance(data, dict):
        log.error('config.json 结构错误（应为对象），使用默认配置')
        return copy.deepcopy(_DEFAULT_CONFIG)
    return data


_CFG = _load_json()


def _pick(section, key, default):
    node = _CFG.get(section)
    if isinstance(node, dict) and key in node and node[key] not in (None, ''):
        return node[key]
    return default


def _env_or(name, fallback):
    return os.environ.get('DGATE_' + name, fallback)


# ---------------------------------------------------------------------------
# 监听与 SSL
# ---------------------------------------------------------------------------
HOST = str(_env_or('HOST', _pick('listen', 'host', _DEFAULT_CONFIG['listen']['host'])))
PORT = int(_env_or('PORT', _pick('listen', 'port', _DEFAULT_CONFIG['listen']['port'])))
SSL_CERT = str(_env_or('SSL_CERT', _pick('ssl', 'cert', '')))
SSL_KEY = str(_env_or('SSL_KEY', _pick('ssl', 'key', '')))

# ---------------------------------------------------------------------------
# MySQL（游戏库，MySQL 5.1/5.5 兼容）
# ---------------------------------------------------------------------------
MYSQL_HOST = str(_env_or('MYSQL_HOST', _pick('mysql', 'host', _DEFAULT_CONFIG['mysql']['host'])))
MYSQL_PORT = int(_env_or('MYSQL_PORT', _pick('mysql', 'port', _DEFAULT_CONFIG['mysql']['port'])))
MYSQL_USER = str(_env_or('MYSQL_USER', _pick('mysql', 'user', _DEFAULT_CONFIG['mysql']['user'])))
MYSQL_PASSWORD = str(_env_or('MYSQL_PASSWORD',
                             _pick('mysql', 'password', _DEFAULT_CONFIG['mysql']['password'])))
MYSQL_CHARSET = 'utf8'

# ---------------------------------------------------------------------------
# 游戏服务端部署方式（native 原生 / docker 容器），详见 serverctl 模块
# ---------------------------------------------------------------------------
GAME_MODE = str(_env_or('GAME_MODE', _pick('game', 'mode', 'native'))).lower()
GAME_CONTAINER = str(_env_or('GAME_CONTAINER', _pick('game', 'container', '')))
# docker 模式下游戏目录的宿主机映射路径（1995chen/dnf 默认 /data/data）
GAME_DATA_DIR = str(_env_or('GAME_DATA_DIR', _pick('game', 'data_dir', '/data/data')))

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
# 项目根目录（server/ 的上一级，privatekey.pem 所在处）
PROJECT_ROOT = os.path.dirname(_SERVER_DIR)
PRIVATE_KEY_PATH = os.path.join(PROJECT_ROOT, 'privatekey.pem')

# 运行期数据目录（SQLite：审计日志 + 管理员账户）
DATA_DIR = os.path.join(_SERVER_DIR, 'data')
SQLITE_PATH = os.path.join(DATA_DIR, 'dgate.db')
ADMINS_DB_PATH = os.path.join(DATA_DIR, 'admins.db')
SECRET_KEY_PATH = os.path.join(DATA_DIR, 'secret_key')

# 种子超级管理员（用户名固定 admin）的初始密码（首次登录后请立即修改）
ADMIN_INITIAL_PASSWORD = os.environ.get('DGATE_ADMIN_INITIAL_PASSWORD', 'admin123456')

# ---------------------------------------------------------------------------
# 登录签名字符串
# ---------------------------------------------------------------------------
# %08x 会被替换为 UID 的 8 位小写十六进制字符串（小写、前导零补齐）
# 完整字符串共 92 字符 = %08x + "01"*32 + "55914510010403030101"
TOKEN_TEMPLATE = '%08x' + '01' * 32 + '55914510010403030101'
# 基础字符串 -> byte 数组的转换方式（与用户确认）：
# hex = 每两个字符对应一个 byte，即 hexToBin（92 字符 -> 46 字节，默认）；
# ascii = 直接取每个字符的 ASCII 字节（92 字节，备用）。
TOKEN_PAYLOAD_ENCODING = os.environ.get('DGATE_TOKEN_ENCODING', 'hex')

# 注册时赠送的点券数量（初始默认值；后台「参数设置」可修改，
# 修改值持久化于 SQLite settings 表，未设置时使用此值）
REGISTER_INIT_CERA = int(os.environ.get('DGATE_REGISTER_INIT_CERA', '30000'))

# 空库（无任何账号）时第一个注册账号使用的起始 UID
FIRST_ACCOUNT_UID = 18000000
