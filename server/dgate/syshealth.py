# -*- coding: utf-8 -*-
"""系统健康采样（Linux /proc，零第三方依赖）。

后台守护线程每 2 秒采样一次 CPU / 内存 / 磁盘，常驻运行（与是否打开
游戏服务页面无关）；峰值自采样启动时刻累计，服务重启后清零。
非 Linux 平台（无 /proc）不启动采样，snapshot 返回 available=False。

- CPU 使用率：/proc/stat 相邻两次采样的 delta 计算（首次采样无值）
- 内存：/proc/meminfo 的 MemTotal/MemAvailable；内核过老缺 MemAvailable 时
  按 MemFree+Buffers+Cached 估算（兼容 CentOS 7 的 3.10 内核）
- 磁盘：os.statvfs(config.PROJECT_ROOT) 所在文件系统的剩余/总容量
"""
import logging
import os
import threading
from datetime import datetime

import config

log = logging.getLogger(__name__)

INTERVAL = 2.0   # 采样周期（秒）

_lock = threading.Lock()
_start_lock = threading.Lock()
_stop = threading.Event()
_thread = None

_state = {
    'available': os.path.isdir('/proc'),
    'reason': None if os.path.isdir('/proc') else '仅支持 Linux 服务器（读取 /proc）',
    'cpu_percent': None,   # 当前 CPU 使用率 %（首次采样前为 None）
    'cpu_peak': None,      # 采样期最高 %
    'mem_used': None,      # bytes
    'mem_total': None,
    'mem_peak': None,
    'disk_free': None,     # bytes
    'disk_total': None,
    'since': None,         # 采样启动时间（峰值统计起点）
    'sampled_at': None,    # 最近一次采样时间
}


def _kb_to_bytes(field):
    # /proc/meminfo 数值行形如 "12345 kB"
    return int(field.split()[0]) * 1024


def parse_cpu_line(line):
    """解析 /proc/stat 首行，返回 (idle, total)（jiffies）。"""
    # user nice system idle iowait irq softirq steal 共 8 个字段
    vals = [int(x) for x in line.split()[1:9]]
    idle = vals[3] + vals[4]
    return idle, sum(vals)


# /proc/meminfo 关注的键 -> 内部名（缺 MemAvailable 时按 MemFree 系估算）
_MEMINFO_KEYS = {'MemTotal': 'total', 'MemAvailable': 'avail',
                 'MemFree': 'memfree', 'Buffers': 'buffers',
                 'Cached': 'cached'}


def parse_meminfo(lines):
    """解析 /proc/meminfo，返回 (total, available)；缺 MemAvailable 时估算。"""
    vals = {}
    for line in lines:
        key, _, rest = line.partition(':')
        name = _MEMINFO_KEYS.get(key.strip())
        if name:
            vals[name] = _kb_to_bytes(rest)
    total = vals.get('total')
    avail = vals.get('avail')
    if avail is None:
        avail = (vals.get('memfree') or 0) + (vals.get('buffers') or 0) \
            + (vals.get('cached') or 0)
    return total, avail


def read_cpu():
    with open('/proc/stat', 'r') as f:
        return parse_cpu_line(f.readline())


def read_mem():
    with open('/proc/meminfo', 'r') as f:
        return parse_meminfo(f.readlines())


def read_disk():
    st = os.statvfs(config.PROJECT_ROOT)
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


def _bump_current_peak(cur_key, peak_key, value):
    """更新当前值并维持峰值只增不减（须持 _lock 调用）。"""
    _state[cur_key] = value
    if _state[peak_key] is None or value > _state[peak_key]:
        _state[peak_key] = value


def _sample(prev_cpu):
    """执行一次采样并更新状态；返回本次 CPU 快照供下次 delta 计算。"""
    cpu_idle, cpu_total = read_cpu()
    mem_total, mem_avail = read_mem()
    disk_free, disk_total = read_disk()
    cpu_percent = None
    if prev_cpu is not None:
        d_total = cpu_total - prev_cpu[1]
        d_idle = cpu_idle - prev_cpu[0]
        if d_total > 0:
            cpu_percent = round(100.0 * (1.0 - float(d_idle) / d_total), 1)
    used = (mem_total - mem_avail) \
        if (mem_total is not None and mem_avail is not None) else None
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _lock:
        _state['sampled_at'] = now
        if cpu_percent is not None:
            _bump_current_peak('cpu_percent', 'cpu_peak', cpu_percent)
        if used is not None:
            _state['mem_total'] = mem_total
            _bump_current_peak('mem_used', 'mem_peak', used)
        _state['disk_free'] = disk_free
        _state['disk_total'] = disk_total
    return cpu_idle, cpu_total


def _loop():
    prev_cpu = None
    while not _stop.is_set():
        try:
            prev_cpu = _sample(prev_cpu)
        except Exception:
            log.exception('系统健康采样失败')
        _stop.wait(INTERVAL)


def ensure_started():
    """启动采样线程（幂等）；非 Linux 平台返回 False 且不启动。"""
    global _thread
    with _start_lock:
        if not _state['available']:
            return False
        if _thread is not None and _thread.is_alive():
            return True
        with _lock:
            if not _state.get('since'):
                _state['since'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _stop.clear()
        _thread = threading.Thread(target=_loop, name='syshealth', daemon=True)
        _thread.start()
        log.info('系统健康采样已启动（每 %d 秒）', int(INTERVAL))
        return True


def snapshot():
    with _lock:
        return dict(_state)


def _reset_for_test():
    """仅供测试：清空线程与状态。"""
    global _thread
    _stop.set()
    _thread = None
    for k in ('cpu_percent', 'cpu_peak', 'mem_used', 'mem_total', 'mem_peak',
              'disk_free', 'disk_total', 'since', 'sampled_at'):
        _state[k] = None
    _state['available'] = os.path.isdir('/proc')
    _state['reason'] = None if _state['available'] else '仅支持 Linux 服务器（读取 /proc）'
