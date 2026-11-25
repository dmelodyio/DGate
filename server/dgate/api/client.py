# -*- coding: utf-8 -*-
"""客户端（MFC 启动器）接口：注册 / 登录 / 修改密码 / 游戏更新。"""
import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory

from .. import crypto, db, store, upgrade, validators

log = logging.getLogger(__name__)
bp = Blueprint('client', __name__)

# 统一错误码
OK = 0
ERR_PARAM = 4001           # 参数格式不合法
ERR_USERNAME_EXISTS = 4002 # 用户名已存在
ERR_NO_USER = 4003         # 用户不存在
ERR_WRONG_PASSWORD = 4004  # 密码错误
ERR_SERVER = 5000          # 服务器内部错误


def _json_payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '')


@bp.post('/register')
def register():
    data = _json_payload()
    username = data.get('username', '')
    password = data.get('password', '')

    if not validators.valid_username(username):
        return jsonify(code=ERR_PARAM, message='用户名需为6~16位英文字母和数字'), 400
    if not validators.valid_password(password):
        return jsonify(code=ERR_PARAM, message='密码需为8~32位英文字母、数字和符号的组合'), 400

    try:
        with db.get_conn() as conn:
            uid, skipped = db.register_account(conn, username,
                                               validators.md5_hex(password))
    except db.DuplicateUsernameError:
        store.audit('user', username, 'register', detail='失败：用户名已存在',
                    ip=_client_ip())
        return jsonify(code=ERR_USERNAME_EXISTS, message='该用户名已被注册'), 409
    except Exception:
        log.exception('注册失败')
        store.audit('user', username, 'register', detail='失败：服务器内部错误',
                    ip=_client_ip())
        return jsonify(code=ERR_SERVER, message='注册失败，请稍后重试'), 500

    detail = '注册成功'
    if skipped:
        detail += '（表缺失已跳过写入：' + '、'.join(skipped) + '）'
    store.audit('user', username, 'register', uid=uid, detail=detail,
                ip=_client_ip())
    return jsonify(code=OK, message='注册成功', data={'uid': uid})


@bp.post('/login')
def login():
    data = _json_payload()
    username = data.get('username', '')
    password = data.get('password', '')

    if not validators.valid_username(username) or \
            not validators.valid_password(password):
        return jsonify(code=ERR_PARAM, message='用户名或密码格式错误'), 400

    try:
        with db.get_conn() as conn:
            account = db.find_account(conn, username)
        if account is None:
            store.audit('user', username, 'login', detail='失败：用户不存在',
                        ip=_client_ip())
            return jsonify(code=ERR_NO_USER, message='用户名或密码错误'), 404
        if account['password'].lower() != validators.md5_hex(password):
            store.audit('user', username, 'login', uid=account['uid'],
                        detail='失败：密码错误', ip=_client_ip())
            return jsonify(code=ERR_WRONG_PASSWORD, message='用户名或密码错误'), 401
        token = crypto.build_token(account['uid'])
    except Exception:
        log.exception('登录失败')
        return jsonify(code=ERR_SERVER, message='登录失败，请稍后重试'), 500

    store.audit('user', username, 'login', uid=account['uid'], detail='登录成功',
                ip=_client_ip())
    return jsonify(code=OK, message='登录成功',
                   data={'uid': account['uid'], 'token': token})


@bp.post('/password')
def change_password():
    data = _json_payload()
    username = data.get('username', '')
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not validators.valid_username(username):
        return jsonify(code=ERR_PARAM, message='用户名需为6~16位英文字母和数字'), 400
    if not validators.valid_password(old_password) or \
            not validators.valid_password(new_password):
        return jsonify(code=ERR_PARAM,
                       message='密码需为8~32位英文字母、数字和符号的组合'), 400

    try:
        with db.get_conn() as conn:
            account = db.find_account(conn, username)
            if account is None:
                store.audit('user', username, 'change_password',
                            detail='失败：用户不存在', ip=_client_ip())
                return jsonify(code=ERR_NO_USER, message='用户名或密码错误'), 404
            if account['password'].lower() != validators.md5_hex(old_password):
                store.audit('user', username, 'change_password', uid=account['uid'],
                            detail='失败：旧密码错误', ip=_client_ip())
                return jsonify(code=ERR_WRONG_PASSWORD, message='用户名或密码错误'), 401
            db.update_password(conn, account['uid'], validators.md5_hex(new_password))
    except Exception:
        log.exception('修改密码失败')
        return jsonify(code=ERR_SERVER, message='修改失败，请稍后重试'), 500

    store.audit('user', username, 'change_password', uid=account['uid'],
                detail='修改密码成功', ip=_client_ip())
    return jsonify(code=OK, message='密码修改成功')


# ---------------- 游戏更新（启动器自动升级） ----------------

@bp.get('/version')
def client_version():
    """双版本号与补丁包大小，供启动器比对本地版本。

    baseline：完整客户端版本（本地低于此值须重新下载完整客户端）；
    current：叠加补丁后的最新版本（本地低于此值且 >= baseline 时增量更新）。
    """
    state = upgrade.get_state()
    size = 0
    try:
        size = os.path.getsize(upgrade.UPDATES_ZIP)
    except OSError:
        pass
    return jsonify(code=OK, data={'baseline': state['baseline'],
                                  'current': state['current'],
                                  'size': size})


@bp.get('/updates.zip')
def client_updates_zip():
    """下载全量更新包（不存在时 404，JSON 错误体）。"""
    if not os.path.isfile(upgrade.UPDATES_ZIP):
        return jsonify(code=4004, message='暂无更新包'), 404
    return send_from_directory(os.path.dirname(upgrade.UPDATES_ZIP),
                               os.path.basename(upgrade.UPDATES_ZIP),
                               as_attachment=True, mimetype='application/zip')
