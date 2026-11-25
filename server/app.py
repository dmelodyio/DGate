# -*- coding: utf-8 -*-
"""DGate 服务端入口。

用法：
    python app.py            # 开发运行（默认 0.0.0.0:5000）
可被 WSGI（gunicorn/waitress）直接引用：create_app()
"""
import logging
import os
import secrets
import sys

from flask import Flask, jsonify, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from dgate import admins, db, store
from dgate.api import register_blueprints

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('dgate')

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')


def _load_secret_key():
    """Flask session 签名密钥：首次生成，持久化于 data/secret_key。"""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if os.path.exists(config.SECRET_KEY_PATH):
        with open(config.SECRET_KEY_PATH, 'r', encoding='ascii') as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(config.SECRET_KEY_PATH, 'w', encoding='ascii') as f:
        f.write(key)
    return key


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config['SECRET_KEY'] = _load_secret_key()
    app.config['SESSION_PERMANENT'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = 8 * 3600  # 后台会话 8 小时
    app.json.ensure_ascii = False  # JSON 响应中的中文不转义

    store.init_store()     # 审计库表结构（幂等）
    admins.init_admins()   # 管理员账户库 + 种子超管（幂等）

    register_blueprints(app)

    @app.route('/')
    @app.route('/<path:filename>')
    def static_files(filename='index.html'):
        return send_from_directory(WEB_DIR, filename)

    @app.route('/api/health')
    def health():
        return jsonify(code=0, message='ok', data={'service': 'dgate'})

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(code=404, message='接口不存在'), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(code=405, message='请求方法不支持'), 405

    return app


def startup_checks():
    """启动自检：私钥、SQLite、游戏库连接、缺失的 d_gmaster、PVF 清单构建。"""
    from dgate import crypto
    key = crypto.load_private_key()
    log.info('RSA 私钥已加载：%d bit', key.n.bit_length())

    store.init_store()
    admins.init_admins()
    log.info('本地存储就绪：%s', config.SQLITE_PATH)
    log.info('管理员存储就绪：%s', config.ADMINS_DB_PATH)

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION()')
            log.info('游戏库连接正常：MySQL %s @ %s', cur.fetchone()[0],
                     config.MYSQL_HOST)
        # 只做只读检查，绝不创建任何库表
        missing = {('d_taiwan', 'accounts')} - db.existing_tables(
            conn, [('d_taiwan', 'accounts')])
        if missing:
            log.error('核心表缺失，注册/登录不可用：%s', ', '.join(
                '%s.%s' % t for t in missing))
    finally:
        conn.close()

    # PVF 道具清单构建（缓存有效则跳过）；构建期间锁定邮件发送/启动服务器
    from dgate import pvfitem
    try:
        if pvfitem.start_build():
            log.info('PVF 道具清单后台构建已启动（构建期间邮件发送与服务器启动锁定）')
        else:
            log.info('PVF 道具清单缓存仍有效，跳过构建')
    except Exception as e:                       # noqa: BLE001
        log.error('PVF 道具清单构建无法启动：%s', e)

    # 系统健康采样（Linux /proc，常驻每 2 秒；与页面是否打开无关）
    from dgate import syshealth
    if syshealth.ensure_started():
        log.info('系统健康采样线程已就绪')
    else:
        log.info('系统健康采样不可用（非 Linux 平台，%s）', syshealth.snapshot()['reason'])


def main():
    startup_checks()
    app = create_app()
    if config.SSL_CERT and config.SSL_KEY:
        for p in (config.SSL_CERT, config.SSL_KEY):
            if not os.path.exists(p):
                log.error('SSL 证书文件不存在：%s，改为 HTTP 启动', p)
                log.info('管理后台：http://%s:%d/', config.HOST, config.PORT)
                app.run(host=config.HOST, port=config.PORT, threaded=True)
                return
        log.info('管理后台（HTTPS）：https://%s:%d/（证书 %s）',
                 config.HOST, config.PORT, config.SSL_CERT)
        app.run(host=config.HOST, port=config.PORT, threaded=True,
                ssl_context=(config.SSL_CERT, config.SSL_KEY))
    else:
        log.info('管理后台：http://%s:%d/（未配置 SSL 证书，HTTP 运行）',
                 config.HOST, config.PORT)
        app.run(host=config.HOST, port=config.PORT, threaded=True)


if __name__ == '__main__':
    main()
