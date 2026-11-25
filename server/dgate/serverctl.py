# -*- coding: utf-8 -*-
"""游戏服务器控制（与 DGate 服务端同机部署）。

两种部署方式（config.json 的 game 节）：

- native（CentOS 7 原生部署）：
  - 启动：bash /root/run（脚本前台阻塞于 zergsvr，必须分离会话执行，
    且脚本开头自带两次 stop，因此“启动”实为“重启”语义）
  - 停止：bash /root/stop（killall 全部服务进程后返回）
  - 状态：pgrep -x df_game_r（精确进程名匹配，不受命令行干扰）
  - 游戏目录固定为 /home/neople/game

- docker（RHEL 8+ 容器部署，镜像如 1995chen/dnf:centos7-2.1.9.fix1）：
  游戏二进制依赖 CentOS 7 的 glibc 2.17，无法在 RHEL 8+（glibc>=2.28）
  原生运行，只能容器化：
  - 启动/停止：docker start / docker stop 游戏容器
  - 状态：docker inspect
  - df_game_r / Script.pvf / publickey.pem 位于镜像内游戏目录映射到
    宿主机的 game.data_dir（默认 /data/data，可在部署脚本中修改）——
    后台对这三个文件的全部读写（PVF 解析、密钥安装、游戏升级）直接走
    宿主机路径，与 native 模式共用同一套文件逻辑

- PVF 安装：install_pvf(src) 将游戏升级补丁中的 Script.pvf 覆盖到
  游戏目录（备份 .bak + 原子替换），由 upgrade 模块在确认
  服务器停止后调用
"""
import logging
import os
import shutil
import subprocess
import threading
import time

import config

log = logging.getLogger(__name__)

GAME_MODE = getattr(config, 'GAME_MODE', 'native')        # native | docker
GAME_CONTAINER = getattr(config, 'GAME_CONTAINER', '')    # docker 模式：容器名
GAME_DATA_DIR = getattr(config, 'GAME_DATA_DIR', '/data/data')

RUN_SCRIPT = '/root/run'
STOP_SCRIPT = '/root/stop'
PROCESS_NAME = 'df_game_r'

if GAME_MODE == 'docker':
    # docker：游戏文件在宿主机映射目录（1995chen/dnf 默认 /data/data）
    PVF_PATH = os.path.join(GAME_DATA_DIR, 'Script.pvf')
    GAME_PUBLIC_KEY_PATH = os.path.join(GAME_DATA_DIR, 'publickey.pem')
else:
    PVF_PATH = '/home/neople/game/Script.pvf'
    GAME_PUBLIC_KEY_PATH = '/home/neople/game/publickey.pem'


def _docker(*args, **kwargs):
    """执行 docker 命令，返回 CompletedProcess。"""
    return subprocess.run(['docker'] + list(args), capture_output=True, **kwargs)


# ---------------------------------------------------------------------------
# 启动窗口期：发起启动后、游戏进程真正出现前，server_status 仍为“未运行”。
# 此窗口内“需要服务器停止”的操作（游戏升级、密钥生成等）必须被拒绝，
# 否则会在游戏拉起过程中改写其文件（PVF / 公钥）。
# ---------------------------------------------------------------------------
_START_STATE_LOCK = threading.Lock()
_START_INITIATED_AT = None        # 最近一次成功发起启动的单调时刻
START_SETTLE_TIMEOUT = 180.0      # native：超时视为启动失败，恢复可操作
DOCKER_MIN_SETTLE = 30.0          # docker：容器 Running 后仍需的最小静置秒数
DOCKER_SETTLE_TIMEOUT = 300.0


def _transitioning_locked():
    """调用方须已持有 _START_STATE_LOCK。"""
    global _START_INITIATED_AT
    if _START_INITIATED_AT is None:
        return False
    elapsed = time.monotonic() - _START_INITIATED_AT
    if GAME_MODE == 'docker':
        timeout, min_settle = DOCKER_SETTLE_TIMEOUT, DOCKER_MIN_SETTLE
    else:
        timeout, min_settle = START_SETTLE_TIMEOUT, 0.0
    if elapsed > timeout:
        _START_INITIATED_AT = None    # 超时未拉起：视为启动失败，恢复可操作
        return False
    if server_status()['running'] and elapsed >= min_settle:
        _START_INITIATED_AT = None    # 已确认运行，窗口结束
        return False
    return True


def server_transitioning():
    """服务器是否处于“启动中”窗口（已发起启动、尚未确认运行）。"""
    with _START_STATE_LOCK:
        return _transitioning_locked()


def server_write_state():
    """供“需要服务器停止”的操作（游戏升级 / 密钥生成）判断状态。

    返回 'running'（运行中）、'starting'（启动窗口期）或 None（空闲）。
    """
    # 启动窗口优先判定：docker 模式下容器 docker start 返回即 Running，
    # 若先查 running 会架空 DOCKER_MIN_SETTLE 窗口（游戏进程尚未就绪时
    # 升级/密钥等写操作就被放行）。native 模式语义不变。
    if server_transitioning():
        return 'starting'
    if server_status()['running']:
        return 'running'
    return None


def _mark_start_initiated():
    global _START_INITIATED_AT
    with _START_STATE_LOCK:
        _START_INITIATED_AT = time.monotonic()


def _clear_start_initiated():
    global _START_INITIATED_AT
    with _START_STATE_LOCK:
        _START_INITIATED_AT = None


def server_status():
    """检测游戏服务器是否运行。

    返回 {running, pids, mode, container_id}：
    - docker：running=容器 Running，container_id=容器短 ID（12 位，docker ps
      惯例），pids 恒为空（容器内 df_game_r 进程号对后台无意义）
    - native：running=存在 df_game_r 进程，pids=其进程号列表
    """
    if GAME_MODE == 'docker':
        if not GAME_CONTAINER:
            log.error('docker 模式未配置游戏容器名（config.json 的 game.container）')
            return {'running': False, 'pids': [],
                    'mode': 'docker', 'container_id': ''}
        try:
            r = _docker('inspect', '-f', '{{.State.Running}} {{.Id}}',
                        GAME_CONTAINER, timeout=10)
            parts = r.stdout.decode().split()
            running = bool(parts) and parts[0] == 'true'
            container_id = parts[1][:12] if running and len(parts) > 1 else ''
            return {'running': running, 'pids': [],
                    'mode': 'docker', 'container_id': container_id}
        except Exception:
            log.exception('检测容器状态失败：%s', GAME_CONTAINER)
            return {'running': False, 'pids': [],
                    'mode': 'docker', 'container_id': ''}
    try:
        r = subprocess.run(['pgrep', '-x', PROCESS_NAME],
                           capture_output=True, timeout=10)
        pids = [int(x) for x in r.stdout.split()]
    except Exception:
        log.exception('检测服务器状态失败')
        return {'running': False, 'pids': [],
                'mode': 'native', 'container_id': ''}
    return {'running': len(pids) > 0, 'pids': pids,
            'mode': 'native', 'container_id': ''}


def server_start():
    """启动游戏服务器。native：发起启动（分离会话，不等待脚本退出）；docker：启动容器。"""
    if GAME_MODE == 'docker':
        if not GAME_CONTAINER:
            log.error('docker 模式未配置游戏容器名，无法启动')
            return False
        r = _docker('start', GAME_CONTAINER, timeout=180)
        log.info('游戏容器 %s 已发起启动，rc=%d', GAME_CONTAINER, r.returncode)
        if r.returncode == 0:
            _mark_start_initiated()
        return r.returncode == 0
    subprocess.Popen(['bash', RUN_SCRIPT], cwd='/root',
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     start_new_session=True)
    log.info('已发起游戏服务器启动：%s', RUN_SCRIPT)
    _mark_start_initiated()
    return True


def server_stop():
    """停止游戏服务器。native：执行停止脚本并等待返回；docker：停止容器（等待退出）。

    native：killall -9 发出后进程退出存在短暂延迟，脚本返回后稍作等待，
    避免随后立即查询状态时误报“仍在运行”。
    docker：docker stop 本身等待容器退出后才返回，无需额外等待。
    """
    if GAME_MODE == 'docker':
        if not GAME_CONTAINER:
            log.error('docker 模式未配置游戏容器名，无法停止')
            return False
        r = _docker('stop', '-t', '60', GAME_CONTAINER, timeout=300)
        log.info('游戏容器 %s 已停止，rc=%d', GAME_CONTAINER, r.returncode)
        _clear_start_initiated()
        return r.returncode == 0
    r = subprocess.run(['bash', STOP_SCRIPT], cwd='/root',
                       capture_output=True, timeout=120)
    log.info('停止脚本已执行，rc=%d', r.returncode)
    _clear_start_initiated()
    time.sleep(2)
    # killall 对不存在的进程返回非零属正常，实际状态以 server_status 为准
    return True


def pvf_info():
    """当前 PVF 文件信息（大小与修改时间）。"""
    try:
        st = os.stat(PVF_PATH)
        return {
            'exists': True,
            'size': st.st_size,
            'mtime': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)),
        }
    except OSError:
        return {'exists': False, 'size': 0, 'mtime': ''}


def install_file(src_path, dest_path):
    """将本地文件覆盖安装到 dest_path（备份 .bak + 原子替换）。

    调用方须自行确认服务器已停止。返回新文件大小。
    """
    tmp = dest_path + '.installing.tmp'
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    try:
        if os.path.exists(dest_path):   # 覆盖前备份现有文件
            bak = dest_path + '.bak'
            if os.path.exists(bak):
                os.remove(bak)
            shutil.copy2(dest_path, bak)

        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest_path)      # 同目录原子替换
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    size = os.path.getsize(dest_path)
    log.info('文件已安装：%s（%d 字节，源：%s）', dest_path, size, src_path)
    return size


def install_pvf(src_path):
    """将补丁中的 Script.pvf 安装到游戏目录（备份 .bak + 原子替换）。"""
    return install_file(src_path, PVF_PATH)
