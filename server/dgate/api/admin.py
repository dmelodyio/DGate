# -*- coding: utf-8 -*-
"""管理后台接口：管理员账户登录 / 用户管理 / 充值 / 重置密码 / 审计 / 改密 /
服务器控制 / 游戏升级 / 管理员管理。"""
import functools
import logging

from flask import Blueprint, Response, jsonify, request, session

from .. import admins, db, keys, maildb, pvfitem, serverctl, store, syshealth, upgrade, validators

log = logging.getLogger(__name__)
bp = Blueprint('admin', __name__)

ERR_PARAM = 4001
ERR_UNAUTHORIZED = 4101   # 未登录或账号密码错误
ERR_FORBIDDEN = 4301      # 已登录但无权限（非超级管理员）
ERR_NO_USER = 4003
ERR_SERVER = 5000


def _json_payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '')


def _actor():
    """当前登录管理员的用户名（用于审计 actor）。"""
    return session.get('admin_user') or 'admin'


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        # 旧式会话（只有 admin 标记无用户名）一律视为未登录，强制重新登录
        if not session.get('admin') or not session.get('admin_user'):
            return jsonify(code=ERR_UNAUTHORIZED, message='请先登录后台'), 401
        return view(*args, **kwargs)
    return wrapped


def super_admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('admin') or not session.get('admin_user'):
            return jsonify(code=ERR_UNAUTHORIZED, message='请先登录后台'), 401
        if not session.get('admin_super'):
            return jsonify(code=ERR_FORBIDDEN,
                           message='该操作仅限超级管理员'), 403
        return view(*args, **kwargs)
    return wrapped


@bp.post('/login')
def login():
    data = _json_payload()
    username = str(data.get('username') or '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify(code=ERR_PARAM, message='请输入管理员账号和密码'), 400
    ok, user = admins.verify(username, password)
    if not ok:
        store.audit('admin', username, 'admin_login',
                    detail='失败：账号或密码错误', ip=_client_ip())
        return jsonify(code=ERR_UNAUTHORIZED, message='管理员账号或密码错误'), 401
    session['admin'] = True
    session['admin_user'] = user['username']
    session['admin_super'] = user['is_super']
    session.permanent = True
    store.audit('admin', user['username'], 'admin_login', detail='登录成功',
                ip=_client_ip())
    return jsonify(code=0, message='登录成功',
                   data={'username': user['username'],
                         'is_super': user['is_super']})


@bp.post('/logout')
def logout():
    session.pop('admin', None)
    session.pop('admin_user', None)
    session.pop('admin_super', None)
    return jsonify(code=0, message='已退出登录')


@bp.get('/session')
def session_state():
    logged_in = bool(session.get('admin') and session.get('admin_user'))
    return jsonify(code=0, data={
        'logged_in': logged_in,
        'username': session.get('admin_user') or '',
        'is_super': bool(session.get('admin_super')),
    })


# ---------------------------------------------------------------------------
# 管理员管理（仅超级管理员）
# ---------------------------------------------------------------------------

@bp.get('/admins')
@super_admin_required
def admins_list():
    return jsonify(code=0, data={'items': admins.list_admins()})


@bp.post('/admins')
@super_admin_required
def admins_add():
    data = _json_payload()
    username = str(data.get('username') or '').strip()
    password = data.get('password', '')
    if not validators.valid_admin_username(username):
        return jsonify(code=ERR_PARAM,
                       message='管理员账号需为3~32位英文字母、数字和下划线'), 400
    if not validators.valid_password(password):
        return jsonify(code=ERR_PARAM,
                       message='密码需为8~32位英文字母、数字和符号的组合'), 400
    if data.get('is_super'):
        # 超级管理员唯一（种子 admin），不可通过接口添加
        return jsonify(code=4090, message='超级管理员唯一，不可添加'), 409
    if admins.get(username) is not None:
        return jsonify(code=4002, message='该管理员账号已存在'), 409
    try:
        admins.add_admin(username, password, False)
    except admins.DuplicateAdminError:
        return jsonify(code=4002, message='该管理员账号已存在'), 409
    store.audit('admin', _actor(), 'admin_create',
                detail='添加管理员 %s' % username, ip=_client_ip())
    return jsonify(code=0, message='管理员已添加',
                   data={'username': username, 'is_super': False})


@bp.post('/admins/delete')
@super_admin_required
def admins_delete():
    data = _json_payload()
    username = str(data.get('username') or '').strip()
    if not username:
        return jsonify(code=ERR_PARAM, message='缺少用户名'), 400
    if username == session.get('admin_user'):
        return jsonify(code=4090, message='不能删除当前登录的账号'), 409
    target = admins.get(username)
    if target is None:
        return jsonify(code=ERR_NO_USER, message='该管理员不存在'), 404
    if target['is_super']:
        # 超级管理员唯一且不可删除
        return jsonify(code=4090, message='超级管理员不可删除'), 409
    admins.delete_admin(username)
    store.audit('admin', _actor(), 'admin_delete',
                detail='删除管理员 %s' % username, ip=_client_ip())
    return jsonify(code=0, message='管理员已删除')


@bp.get('/users')
@admin_required
def users():
    keyword = request.args.get('search', '').strip()
    try:
        page = max(int(request.args.get('page', '1')), 1)
        page_size = min(max(int(request.args.get('page_size', '20')), 1), 100)
    except ValueError:
        return jsonify(code=ERR_PARAM, message='分页参数错误'), 400
    offset = (page - 1) * page_size
    try:
        with db.get_conn() as conn:
            items, total = db.list_users(conn, None if keyword == '' else keyword,
                                         offset=offset, limit=page_size)
    except Exception:
        log.exception('查询用户列表失败')
        return jsonify(code=ERR_SERVER, message='查询失败，请检查数据库连接'), 500
    return jsonify(code=0, data={'items': items, 'total': total,
                                 'page': page, 'page_size': page_size})


@bp.post('/recharge')
@admin_required
def recharge():
    data = _json_payload()
    uid = data.get('uid')
    amount = data.get('amount')
    if not isinstance(uid, int) or not isinstance(amount, int) or amount == 0:
        return jsonify(code=ERR_PARAM, message='参数错误：uid/amount 必须为整数'), 400
    try:
        with db.get_conn() as conn:
            account = db.find_account_by_uid(conn, uid)
            if account is None:
                return jsonify(code=ERR_NO_USER, message='用户不存在'), 404
            new_value = db.add_cera(conn, uid, amount)
    except Exception:
        log.exception('充值失败')
        return jsonify(code=ERR_SERVER, message='充值失败，请稍后重试'), 500
    store.audit('admin', _actor(), 'recharge', uid=uid,
                detail='点券 %s%d，余额 %d' % ('+' if amount > 0 else '', amount, new_value),
                ip=_client_ip())
    return jsonify(code=0, message='充值成功', data={'uid': uid, 'cera': new_value})


@bp.post('/reset_password')
@admin_required
def reset_password():
    data = _json_payload()
    uid = data.get('uid')
    if not isinstance(uid, int):
        return jsonify(code=ERR_PARAM, message='参数错误：uid 必须为整数'), 400
    new_password = validators.random_password()
    try:
        with db.get_conn() as conn:
            account = db.find_account_by_uid(conn, uid)
            if account is None:
                return jsonify(code=ERR_NO_USER, message='用户不存在'), 404
            db.update_password(conn, uid, validators.md5_hex(new_password))
    except Exception:
        log.exception('重置密码失败')
        return jsonify(code=ERR_SERVER, message='重置失败，请稍后重试'), 500
    store.audit('admin', _actor(), 'reset_password', uid=uid,
                detail='重置用户 %s 的密码' % account['accountname'], ip=_client_ip())
    # 明文密码仅本次返回，不再存储
    return jsonify(code=0, message='密码已重置',
                   data={'uid': uid, 'username': account['accountname'],
                         'new_password': new_password})


@bp.post('/ban')
@admin_required
def ban_user():
    """封停用户（写入 member_punish_info，格式同 GMTool）。"""
    data = _json_payload()
    uid = data.get('uid')
    if not isinstance(uid, int):
        return jsonify(code=ERR_PARAM, message='参数错误：uid 必须为整数'), 400
    try:
        with db.get_conn() as conn:
            account = db.find_account_by_uid(conn, uid)
            if account is None:
                return jsonify(code=ERR_NO_USER, message='用户不存在'), 404
            if db.is_banned(conn, uid):
                return jsonify(code=4090, message='该用户已处于封号状态'), 409
            db.ban_user(conn, uid, _actor())
    except Exception:
        log.exception('封号失败')
        return jsonify(code=ERR_SERVER, message='封号失败，请稍后重试'), 500
    store.audit('admin', _actor(), 'user_ban', uid=uid,
                detail='封停用户 %s' % account['accountname'], ip=_client_ip())
    return jsonify(code=0, message='已封号')


@bp.post('/unban')
@admin_required
def unban_user():
    """解封用户（删除 member_punish_info 中的封号记录）。"""
    data = _json_payload()
    uid = data.get('uid')
    if not isinstance(uid, int):
        return jsonify(code=ERR_PARAM, message='参数错误：uid 必须为整数'), 400
    try:
        with db.get_conn() as conn:
            account = db.find_account_by_uid(conn, uid)
            if account is None:
                return jsonify(code=ERR_NO_USER, message='用户不存在'), 404
            if not db.is_banned(conn, uid):
                return jsonify(code=4090, message='该用户未处于封号状态'), 409
            db.unban_user(conn, uid)
    except Exception:
        log.exception('解封失败')
        return jsonify(code=ERR_SERVER, message='解封失败，请稍后重试'), 500
    store.audit('admin', _actor(), 'user_unban', uid=uid,
                detail='解封用户 %s' % account['accountname'], ip=_client_ip())
    return jsonify(code=0, message='已解封')


@bp.get('/audit')
@admin_required
def audit_list():
    try:
        page = max(int(request.args.get('page', '1')), 1)
        page_size = min(max(int(request.args.get('page_size', '20')), 1), 100)
    except ValueError:
        return jsonify(code=ERR_PARAM, message='分页参数错误'), 400
    action = request.args.get('action', '').strip() or None
    items, total = store.list_audit(offset=(page - 1) * page_size,
                                    limit=page_size, action=action)
    return jsonify(code=0, data={'items': items, 'total': total,
                                 'page': page, 'page_size': page_size})


@bp.post('/change_password')
@admin_required
def change_password():
    """修改当前登录管理员自己的密码。"""
    data = _json_payload()
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    if not old_password or not validators.valid_password(new_password):
        return jsonify(code=ERR_PARAM,
                       message='新密码需为8~32位英文字母、数字和符号的组合'), 400
    ok, _user = admins.verify(session.get('admin_user'), old_password)
    if not ok:
        return jsonify(code=ERR_UNAUTHORIZED, message='原密码错误'), 401
    admins.set_password(session.get('admin_user'), new_password)
    store.audit('admin', _actor(), 'admin_password_change', detail='修改本人密码',
                ip=_client_ip())
    return jsonify(code=0, message='密码已修改')


# ---------------------------------------------------------------------------
# 运行参数（后台「参数设置」）
# ---------------------------------------------------------------------------

MAX_REGISTER_CERA = 2147483647   # cash_cera.cera int 上限


@bp.get('/settings')
@admin_required
def settings_get():
    """当前运行参数：注册赠送点券及最近修改信息。"""
    data = {'register_init_cera': store.get_register_init_cera()}
    row = store.get_setting_row(store.REGISTER_CERA_KEY)
    if row is not None:
        data.update(updated_at=row[1], updated_by=row[2])
    return jsonify(code=0, data=data)


@bp.post('/settings')
@admin_required
def settings_set():
    """修改运行参数：注册赠送点券。"""
    data = _json_payload()
    value = data.get('register_init_cera')
    # bool 是 int 子类，需显式排除
    if not isinstance(value, int) or isinstance(value, bool) \
            or value < 0 or value > MAX_REGISTER_CERA:
        return jsonify(code=ERR_PARAM,
                       message='注册赠送点券需为 0~%d 的整数' % MAX_REGISTER_CERA), 400
    old = store.get_register_init_cera()
    if value != old:
        store.set_setting(store.REGISTER_CERA_KEY, value, updated_by=_actor())
        store.audit('admin', _actor(), 'settings_update',
                    detail='注册赠送点券 %d → %d' % (old, value), ip=_client_ip())
    return jsonify(code=0, message='参数已保存',
                   data={'register_init_cera': value})


# ---------------------------------------------------------------------------
# 服务器控制
# ---------------------------------------------------------------------------

@bp.get('/server/status')
@admin_required
def server_status():
    status = serverctl.server_status()
    return jsonify(code=0, data=status)


@bp.post('/server/start')
@admin_required
def server_start():
    guard = _mail_items_building_guard()
    if guard is not None:
        return guard
    if serverctl.server_transitioning():
        return jsonify(code=4090,
                       message='服务器正在启动中，请等待启动完成（状态页每 3 秒自动刷新）'), 409
    if serverctl.server_status()['running']:
        return jsonify(code=4090, message='服务器运行中，请先停止后再启动'), 409
    serverctl.server_start()
    store.audit('admin', _actor(), 'server_start',
                detail='发起游戏服务器启动（run 脚本）', ip=_client_ip())
    return jsonify(code=0, message='已发起启动，游戏进程约需数十秒拉起',
                   data=serverctl.server_status())


@bp.post('/server/stop')
@admin_required
def server_stop():
    serverctl.server_stop()
    store.audit('admin', _actor(), 'server_stop',
                detail='执行游戏服务器停止（stop 脚本）', ip=_client_ip())
    return jsonify(code=0, message='停止脚本已执行',
                   data=serverctl.server_status())


# ---------------- 系统健康 ----------------

@bp.get('/syshealth')
@admin_required
def syshealth_snapshot():
    """系统健康快照：CPU/内存 当前值与峰值、磁盘余量。

    后台采样线程常驻运行（每 2 秒，与页面无关）；此接口仅在
    采样线程未启动时负责兜底拉起（幂等）。
    """
    syshealth.ensure_started()
    return jsonify(code=0, data=syshealth.snapshot())


@bp.get('/upgrade')
@admin_required
def upgrade_info():
    """游戏升级状态：基线/当前版本 / 补丁包 / updates 目录 / 服务器 PVF。"""
    return jsonify(code=0, data=upgrade.upgrade_info())


@bp.post('/upgrade/baseline')
@admin_required
def upgrade_set_baseline():
    """将当前版本设置为基线版本：清空 updates 目录并重设基线号。"""
    ok, result = upgrade.set_baseline()
    if not ok:
        store.audit('admin', _actor(), 'baseline_reset',
                    detail='失败：' + result, ip=_client_ip())
        return jsonify(code=4090, message=result), 409
    store.audit('admin', _actor(), 'baseline_reset',
                detail='将当前版本 v%d 设置为基线版本（updates 目录已清空）'
                       % result['baseline'], ip=_client_ip())
    return jsonify(code=0,
                   message='基线版本已重设为 v%d' % result['baseline'],
                   data=result)


@bp.post('/upgrade')
@admin_required
def upgrade_upload():
    """上传补丁 zip：解压覆盖 updates 目录 → 重打包 → 版本 +1 →
    含 Script.pvf 时同步到游戏服务器并重建道具清单。"""
    guard = _mail_items_building_guard()
    if guard is not None:
        return guard
    f = request.files.get('file')
    if f is None:
        return jsonify(code=ERR_PARAM, message='缺少文件字段 file'), 400
    ok, result = upgrade.apply_patch(f)
    if not ok:
        store.audit('admin', _actor(), 'game_upgrade',
                    detail='失败：' + result, ip=_client_ip())
        return jsonify(code=4090, message=result), 409
    # 补丁更新了 PVF 时立即重建道具清单（用户约定的构建时机之一）
    if result['pvf_updated']:
        try:
            started = upgrade.after_upgrade_pvf_rebuild()
            if started:
                store.audit('admin', _actor(), 'mail_items_refresh',
                            detail='游戏升级触发清单重建（后台进行）', ip=_client_ip())
        except Exception:
            log.exception('游戏升级后清单重建启动失败')
    store.audit('admin', _actor(), 'game_upgrade',
                detail='上传补丁包：基线 v%d 当前 v%d，updates %d 个文件，'
                       '补丁包 %d 字节%s'
                       % (result['baseline'], result['current'],
                          result['files'], result['zip_size'],
                          '，已同步 Script.pvf' if result['pvf_updated'] else ''),
                ip=_client_ip())
    return jsonify(code=0,
                   message='游戏升级成功，当前版本 v%d%s'
                           % (result['current'],
                              '，道具清单开始重建' if result['pvf_updated'] else ''),
                   data=result)


@bp.get('/keys')
@admin_required
def keys_state():
    """认证密钥状态：系统私钥 / 项目公钥 / 游戏目录公钥。"""
    return jsonify(code=0, data=keys.keys_info())


def _pem_download(content, filename):
    return Response(content, mimetype='application/x-pem-file',
                    headers={'Content-Disposition':
                             'attachment; filename=%s' % filename})


@bp.get('/keys/public.pem')
@admin_required
def keys_download_public():
    """下载当前已生效的公钥 PEM 文件。"""
    data = keys.public_pem_text()
    if not data:
        return jsonify(code=4004, message='公钥文件不存在'), 404
    return _pem_download(data, 'publickey.pem')


@bp.get('/keys/private.pem')
@admin_required
def keys_download_private():
    """下载当前生效的私钥 PEM 文件（前端有双重确认防泄漏）。"""
    data = keys.private_pem_text()
    if not data:
        return jsonify(code=4004, message='私钥文件不存在'), 404
    store.audit('admin', _actor(), 'key_download',
                detail='下载私钥文件 privatekey.pem', ip=_client_ip())
    return _pem_download(data, 'privatekey.pem')


@bp.post('/keys/generate')
@admin_required
def keys_generate():
    """生成新 RSA-2048 认证密钥对（仅服务器停止时可用）。"""
    ok, result = keys.generate_keypair()
    if not ok:
        store.audit('admin', _actor(), 'key_generate',
                    detail='失败：' + result, ip=_client_ip())
        return jsonify(code=4090, message=result), 409
    store.audit('admin', _actor(), 'key_generate',
                detail='生成新认证密钥对（私钥已启用，游戏公钥已覆盖）',
                ip=_client_ip())
    return jsonify(code=0, message='新密钥对已生成并启用', data=result)


# ---------------- 邮件发送 ----------------

MAX_ATTACHMENTS = 10
MAX_GOLD = 4294967295          # postal.gold int(10) unsigned 上限
MAX_TEXT = 255                 # letter.letter_text varchar(255)
MAX_SENDER = 20                # send_charac_name varchar(20)


def _mail_items_building_guard():
    """PVF 清单构建期间：返回锁定提示响应；未构建中返回 None。"""
    if pvfitem.is_building():
        return jsonify(code=5090, message='道具清单正在重建，请稍候再试'), 409
    return None


def _validate_attachment(entry):
    """校验单个附件条目；非法时返回错误消息，合法返回 None。"""
    if not isinstance(entry, dict):
        return '附件参数格式错误'
    item_id = entry.get('id')
    count = entry.get('count')
    if not isinstance(item_id, int) or item_id <= 0:
        return '附件道具 id 必须为正整数'
    if not isinstance(count, int) or count <= 0:
        return '附件道具数量必须为正整数'
    return None


def _mail_items_payload(raw):
    """解析并校验附件参数，返回 [(item_id, count)] 或 (None, 错误消息)。"""
    if raw is None:
        return [], None
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHMENTS:
        return None, '附件最多 %d 种道具' % MAX_ATTACHMENTS
    items = []
    seen = set()
    for entry in raw:
        err = _validate_attachment(entry)
        if err:
            return None, err
        if entry['id'] in seen:
            return None, '附件道具 id 重复'
        seen.add(entry['id'])
        items.append((entry['id'], entry['count']))
    return items, None


@bp.get('/mail/items')
@admin_required
def mail_items():
    search = request.args.get('search', '').strip().lower()
    try:
        cache = pvfitem.get_items()
    except pvfitem.PVFError as e:
        return jsonify(code=ERR_SERVER, message=str(e)), 503
    except Exception:
        log.exception('PVF 道具清单读取失败')
        return jsonify(code=ERR_SERVER, message='PVF 道具清单读取失败'), 500
    items = cache['items']
    if search:
        items = [it for it in items
                 if search in it['name'].lower() or search in it['name_tc'].lower()
                 or search == str(it['id'])]
    return jsonify(code=0, data={'count': len(items), 'total': cache['count'],
                                 'pvf_mtime': cache['pvf_mtime'],
                                 'pvf_size': cache['pvf_size'],
                                 'items': items})


@bp.get('/mail/items/state')
@admin_required
def mail_items_state():
    building = pvfitem.is_building()
    data = {'building': building, 'error': pvfitem.last_error(),
            'ready': False, 'count': None, 'pvf_mtime': None, 'pvf_size': None}
    if not building:
        try:
            cache = pvfitem.get_items()
            data.update(ready=True, count=cache['count'],
                        pvf_mtime=cache['pvf_mtime'], pvf_size=cache['pvf_size'])
        except pvfitem.PVFError as e:
            data['error'] = str(e)
    return jsonify(code=0, data=data)


@bp.post('/mail/items/refresh')
@admin_required
def mail_items_refresh():
    guard = _mail_items_building_guard()
    if guard is not None:
        return guard
    try:
        started = pvfitem.start_build(force=True)
    except pvfitem.PVFError as e:
        store.audit('admin', _actor(), 'mail_items_refresh',
                    detail='失败：' + str(e), ip=_client_ip())
        return jsonify(code=ERR_SERVER, message=str(e)), 500
    except Exception:
        log.exception('PVF 道具清单构建启动失败')
        store.audit('admin', _actor(), 'mail_items_refresh',
                    detail='失败：启动构建异常', ip=_client_ip())
        return jsonify(code=ERR_SERVER, message='PVF 道具清单构建启动失败'), 500
    store.audit('admin', _actor(), 'mail_items_refresh',
                detail='手动重建道具清单（后台进行）', ip=_client_ip())
    message = '道具清单重建已开始（构建期间邮件发送与服务器启动锁定）' if started \
        else '道具清单已在重建中'
    return jsonify(code=0, message=message, data={'building': True})


@bp.get('/mail/characters')
@admin_required
def mail_characters():
    search = request.args.get('search', '').strip()
    if not search:
        return jsonify(code=ERR_PARAM, message='缺少搜索关键字'), 400
    try:
        with db.get_conn() as conn:
            items = maildb.search_characters(conn, search, limit=20)
    except Exception:
        log.exception('角色搜索失败')
        return jsonify(code=ERR_SERVER, message='角色搜索失败'), 500
    return jsonify(code=0, data={'items': items})


@bp.get('/mail/recipients')
@admin_required
def mail_recipients():
    mode = request.args.get('mode', '')
    if mode != 'all':
        return jsonify(code=ERR_PARAM, message='mode 仅支持 all'), 400
    try:
        with db.get_conn() as conn:
            count = maildb.count_characters(conn)
    except Exception:
        log.exception('收件人数查询失败')
        return jsonify(code=ERR_SERVER, message='收件人数查询失败'), 500
    return jsonify(code=0, data={'count': count})


def _parse_mail_request(data):
    """校验邮件请求字段；返回 (fields dict, error_response)。校验失败时 fields 为 None。"""
    mode = data.get('mode')
    if mode not in ('single', 'all'):
        return None, jsonify(code=ERR_PARAM, message='mode 必须为 single 或 all'), 400

    sender = str(data.get('sender') or '系统').strip()
    if not sender or len(sender) > MAX_SENDER:
        return None, jsonify(code=ERR_PARAM,
                             message='发件人需为 1~%d 个字符' % MAX_SENDER), 400

    text = str(data.get('text') or '').strip()
    if not text or len(text) > MAX_TEXT:
        return None, jsonify(code=ERR_PARAM,
                             message='正文需为 1~%d 个字符' % MAX_TEXT), 400

    gold = data.get('gold', 0)
    if not isinstance(gold, int) or gold < 0 or gold > MAX_GOLD:
        return None, jsonify(code=ERR_PARAM, message='金币必须为 0~%d 的整数' % MAX_GOLD), 400

    items, err = _mail_items_payload(data.get('items'))
    if err:
        return None, jsonify(code=ERR_PARAM, message=err), 400
    if not items and gold == 0:
        return None, jsonify(code=ERR_PARAM, message='邮件至少需要金币或一个附件'), 400

    fields = {'mode': mode, 'sender': sender, 'text': text, 'gold': gold,
              'items': items,
              'character': str(data.get('character') or '').strip(),
              'expected_count': data.get('expected_count')}
    return fields, None


def _check_items_in_pvf(items):
    """附件道具 id 必须存在于 PVF 道具清单；返回 error_response 或 None。"""
    try:
        known = {it['id'] for it in pvfitem.get_items()['items']}
    except Exception:
        log.exception('PVF 道具清单读取失败')
        return jsonify(code=ERR_SERVER, message='PVF 道具清单不可用'), 500
    unknown = [iid for iid, _cnt in items if iid not in known]
    if unknown:
        return jsonify(code=ERR_PARAM,
                       message='附件道具 id 不在 PVF 清单中: %s'
                               % ','.join(map(str, unknown[:5]))), 400
    return None


def _deliver_mail(conn, fields):
    """按 mode 投递邮件（须持游戏库连接）；返回 (result dict, error_response)。

    result: {'target_desc', 'sent', 'failed'}；校验类失败时两者为 None。
    """
    if fields['mode'] == 'single':
        if not fields['character']:
            return None, jsonify(code=ERR_PARAM, message='缺少收件角色名'), 400
        charac = maildb.find_character_by_name(conn, fields['character'])
        if charac is None:
            return None, jsonify(code=ERR_NO_USER, message='角色不存在'), 404
        maildb.insert_mail(conn, charac['charac_no'], fields['sender'],
                           fields['text'], fields['gold'], fields['items'])
        return {'target_desc': '角色 %s(charac_no=%d)' % (charac['charac_name'],
                                                         charac['charac_no']),
                'sent': 1, 'failed': []}, None

    expected = fields['expected_count']
    count_now = maildb.count_characters(conn)
    if not isinstance(expected, int) or expected != count_now:
        return None, jsonify(code=ERR_PARAM,
                             message='全服角色数已变化（当前 %d），请刷新后重试'
                                     % count_now), 400
    charac_nos = maildb.list_character_ids(conn)
    sent, failed = maildb.send_bulk(conn, charac_nos, fields['sender'],
                                    fields['text'], fields['gold'],
                                    fields['items'])
    return {'target_desc': '全服 %d 个角色' % len(charac_nos),
            'sent': sent, 'failed': failed}, None


@bp.post('/mail/send')
@admin_required
def mail_send():
    guard = _mail_items_building_guard()
    if guard is not None:
        return guard
    data = _json_payload()
    fields, err = _parse_mail_request(data)
    if err is not None:
        return err
    err = _check_items_in_pvf(fields['items'])
    if err is not None:
        return err
    try:
        maildb.validate_mail(fields['sender'], fields['text'])
    except ValueError as e:
        return jsonify(code=ERR_PARAM, message=str(e)), 400

    try:
        with db.get_conn() as conn:
            result, err = _deliver_mail(conn, fields)
    except Exception:
        log.exception('邮件发送失败')
        return jsonify(code=ERR_SERVER, message='邮件发送失败，请稍后重试'), 500
    if err is not None:
        return err

    detail_items = ','.join('%d×%d' % (iid, cnt)
                            for iid, cnt in fields['items']) or '无'
    store.audit('admin', _actor(), 'mail_send',
                detail='%s，发件人 %s，金币 %d，附件 %s，成功 %d 失败 %d'
                       % (result['target_desc'], fields['sender'],
                          fields['gold'], detail_items,
                          result['sent'], len(result['failed'])),
                ip=_client_ip())
    sent, failed = result['sent'], result['failed']
    return jsonify(code=0, message='邮件发送成功（%d/%d）' % (sent, sent + len(failed)),
                   data={'sent': sent, 'failed': failed})
