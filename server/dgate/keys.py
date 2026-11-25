# -*- coding: utf-8 -*-
"""认证密钥管理：重新生成服务器 RSA-2048 登录签名密钥对。

- openssl genrsa 2048 生成私钥（PKCS#1 PEM，与既有 privatekey.pem 同格式），
  openssl rsa -pubout 导出公钥
- 私钥原子替换 config.PRIVATE_KEY_PATH 并刷新 crypto 缓存（无需重启服务）
- 公钥同时更新项目根 publickey.pem，并复制覆盖到游戏目录
  serverctl.GAME_PUBLIC_KEY_PATH（备份 .bak）
- 仅允许游戏服务器停止时进行（生成前后双检 + 并发锁）
"""
import logging
import os
import subprocess
import threading
import time

from Crypto.PublicKey import RSA

import config

from . import crypto, serverctl

log = logging.getLogger(__name__)

# 项目根的公钥路径（与 privatekey.pem 同目录，供测试/对拍使用）
PUBLIC_KEY_PATH = os.path.join(config.PROJECT_ROOT, 'publickey.pem')

# 同一时间只允许一次生成
_KEYS_LOCK = threading.Lock()


def _stat(path):
    try:
        st = os.stat(path)
        return {'exists': True, 'size': st.st_size,
                'mtime': time.strftime('%Y-%m-%d %H:%M:%S',
                                       time.localtime(st.st_mtime))}
    except OSError:
        return {'exists': False, 'size': 0, 'mtime': ''}


def _read_text(path):
    try:
        with open(path, 'r', encoding='ascii') as f:
            return f.read()
    except OSError:
        return ''


def public_pem_text():
    """当前已生效公钥 PEM 文本（游戏目录副本优先，项目根兜底）。"""
    return _read_text(serverctl.GAME_PUBLIC_KEY_PATH) or _read_text(PUBLIC_KEY_PATH)


def private_pem_text():
    """当前生效私钥 PEM 文本。"""
    return _read_text(config.PRIVATE_KEY_PATH)


def keys_info():
    """当前密钥信息：系统私钥 / 项目公钥 / 游戏目录公钥 / 已生效公钥 PEM。"""
    return {
        'private': _stat(config.PRIVATE_KEY_PATH),
        'public': _stat(PUBLIC_KEY_PATH),
        'game_public': _stat(serverctl.GAME_PUBLIC_KEY_PATH),
        # 已生效公钥（游戏目录副本优先，项目根兜底），供前端文本框展示
        'public_pem': public_pem_text(),
    }


def _run_openssl(args):
    r = subprocess.run(['openssl'] + args, capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError('openssl 执行失败: %s'
                           % r.stderr.decode('utf-8', 'replace').strip())


def _generate_keypair(priv_path, pub_path):
    """openssl 生成 RSA-2048 密钥对（PEM）。测试可 mock 本函数。"""
    _run_openssl(['genrsa', '-out', priv_path, '2048'])
    _run_openssl(['rsa', '-in', priv_path, '-pubout', '-out', pub_path])


def _load_and_validate_private(tmp_priv):
    """替换前校验：新私钥必须可加载且为 RSA-2048。失败抛异常。"""
    with open(tmp_priv, 'rb') as f:
        key = RSA.import_key(f.read())
    if key.size_in_bits() != 2048:
        raise ValueError('非 RSA-2048')


def _install_new_keypair(tmp_priv, tmp_pub):
    """新私钥校验 → 安装前二次状态检查（防生成期间被启动）→ 原子替换
    密钥文件并刷新缓存。校验/检查不通过时返回 (错误消息, None)，成功返回
    (None, mtime 信息)。"""
    try:
        _load_and_validate_private(tmp_priv)
    except Exception:
        log.exception('新私钥校验失败')
        return '新私钥校验失败，已放弃替换', None
    if serverctl.server_status()['running']:
        return '生成期间服务器已启动，本次安装已取消', None
    if serverctl.server_transitioning():
        return '生成期间服务器已进入启动流程，本次安装已取消', None

    os.replace(tmp_priv, config.PRIVATE_KEY_PATH)
    crypto.reload_private_key()     # 刷新缓存，后续登录立即使用新钥
    serverctl.install_file(tmp_pub, serverctl.GAME_PUBLIC_KEY_PATH)
    os.replace(tmp_pub, PUBLIC_KEY_PATH)

    info = keys_info()
    log.info('认证密钥已更新：私钥 %s，游戏公钥 %s',
             config.PRIVATE_KEY_PATH, serverctl.GAME_PUBLIC_KEY_PATH)
    return None, {'private_mtime': info['private']['mtime'],
                  'game_public_mtime': info['game_public']['mtime']}


def generate_keypair():
    """生成新密钥对并安装。返回 (ok, data_or_message)。

    流程：停止检查 → openssl 生成（临时文件）→ 新私钥可加载校验 →
    二次停止检查 → 私钥原子替换 + 刷新 crypto 缓存 → 公钥覆盖游戏目录
    （备份 .bak）→ 更新项目根公钥。
    """
    if serverctl.server_status()['running']:
        return False, '服务器正在运行，请先停止服务器再生成密钥对'
    if serverctl.server_transitioning():
        return False, '服务器正在启动中，请等待启动完成后再生成密钥对'

    acquired = _KEYS_LOCK.acquire(blocking=False)
    if not acquired:
        return False, '已有密钥生成正在进行，请稍候'

    tmp_priv = config.PRIVATE_KEY_PATH + '.generating.tmp'
    tmp_pub = PUBLIC_KEY_PATH + '.generating.tmp'
    try:
        os.makedirs(os.path.dirname(config.PRIVATE_KEY_PATH), exist_ok=True)
        try:
            _generate_keypair(tmp_priv, tmp_pub)
        except FileNotFoundError:
            return False, '服务器上未安装 openssl'
        except Exception as e:
            log.exception('openssl 生成密钥对失败')
            return False, '密钥生成失败：%s' % e

        err, data = _install_new_keypair(tmp_priv, tmp_pub)
        if err:
            return False, err

        return True, data
    finally:
        for tmp in (tmp_priv, tmp_pub):
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        _KEYS_LOCK.release()
