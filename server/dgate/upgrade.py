# -*- coding: utf-8 -*-
"""游戏升级：补丁包累积更新 + 双版本号（基线/当前）+ 客户端更新包分发。

版本概念（2026-11-24 用户重新定义）：
- 基线版本 baseline：游戏运营方提供完整客户端的版本
- 当前版本 current：完整客户端基础上叠加补丁后的版本
- 首次部署 baseline = current = 1；上传补丁仅 current +1；
  「将当前版本设置为基线版本」清空 updates 目录并 baseline = current

- 管理员上传补丁 zip → 解压覆盖到 updates 目录（同名文件覆盖）→
  将 updates 目录全量重打包为 updates.zip → 当前版本 +1
- 补丁中含 Script.pvf 时，同步覆盖游戏服务器的 /home/neople/game/Script.pvf
  并触发道具清单重建（仅允许服务器停止时进行，上传前后各查一次状态）
- 启动器通过 /api/client/version 比对：本地 < 基线 → 拒绝升级
  （需重新下载完整客户端）；基线 <= 本地 < 当前 → 下载 updates.zip
- 版本状态存 data/upgrade.json；旧单版本格式 {version: N} 自动迁移为
  {baseline: 1, current: N}
"""
import json
import logging
import os
import shutil
import threading
import time
import zipfile

import config

from . import pvfitem, serverctl

log = logging.getLogger(__name__)

# 路径常量：函数内引用本模块属性，便于测试 mock.patch.object
UPDATES_DIR = os.path.join(config.DATA_DIR, 'updates')
UPDATES_ZIP = os.path.join(config.DATA_DIR, 'updates.zip')
STATE_PATH = os.path.join(config.DATA_DIR, 'upgrade.json')

# 同一时间只允许一个升级处理（解压/打包/重设基线期间 updates 目录不可并发写）
_UPGRADE_LOCK = threading.Lock()


def get_state():
    """读取升级状态 {baseline, current, baseline_at, current_at}。

    缺失/损坏视为首次部署（baseline = current = 1）；
    旧单版本格式 {version: N} 迁移为 baseline=1、current=max(N,1)
    （旧 updated_at 归入 current_at，baseline_at 未知留空）。
    """
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
        if 'baseline' in data or 'current' in data:
            baseline = int(data.get('baseline', 1))
            current = int(data.get('current', baseline))
            if baseline < 1 or current < baseline:
                raise ValueError('版本号不合法')
            return {'baseline': baseline, 'current': current,
                    'baseline_at': data.get('baseline_at', ''),
                    'current_at': data.get('current_at', '')}
        # 旧格式迁移：首部署即 1/1，N 次补丁后 1/N
        version = max(int(data.get('version', 0)), 1)
        return {'baseline': 1, 'current': version,
                'baseline_at': '',
                'current_at': data.get('updated_at', '')}
    except (OSError, ValueError, TypeError):
        return {'baseline': 1, 'current': 1, 'baseline_at': '', 'current_at': ''}


def _write_state(baseline, current, touch_baseline=False, touch_current=False):
    """原子写入升级状态；touch_* 为 True 时把对应版本号的更新时间置为现在。"""
    state = get_state()
    tmp = STATE_PATH + '.tmp'
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(tmp, 'w', encoding='utf-8') as fp:
        json.dump({'baseline': baseline, 'current': current,
                   'baseline_at': now if touch_baseline else state['baseline_at'],
                   'current_at': now if touch_current else state['current_at'],
                   'updated_at': now},
                  fp, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def _zip_size_mtime():
    try:
        st = os.stat(UPDATES_ZIP)
        return st.st_size, time.strftime('%Y-%m-%d %H:%M:%S',
                                         time.localtime(st.st_mtime))
    except OSError:
        return 0, ''


def _count_updates_files():
    """updates 目录内文件数（递归，不含目录本身）。"""
    if not os.path.isdir(UPDATES_DIR):
        return 0
    n = 0
    for _root, _dirs, files in os.walk(UPDATES_DIR):
        n += len(files)
    return n


def upgrade_info():
    """管理端信息：基线/当前版本（各带更新时间）/ 补丁包 / updates 目录 / PVF。"""
    state = get_state()
    size, mtime = _zip_size_mtime()
    return {
        'baseline': state['baseline'],
        'current': state['current'],
        'baseline_at': state['baseline_at'],
        'current_at': state['current_at'],
        'zip_size': size,
        'zip_mtime': mtime,
        'files': _count_updates_files(),
        'pvf': serverctl.pvf_info(),
    }


def _safe_member_name(name):
    """校验 zip 条目名：转成平台分隔符后必须落在目标目录内。

    返回相对 Parts 元组；不安全（绝对路径/盘符/.. 穿越）返回 None。
    """
    name = name.replace('\\', '/')
    if not name or name.startswith('/'):
        return None
    parts = []
    for seg in name.split('/'):
        if seg in ('', '.'):
            continue
        if seg == '..' or ':' in seg:
            return None
        parts.append(seg)
    if not parts:
        return None
    # Windows 保留盘符形式（C:、C|x 等）再挡一道
    first = parts[0]
    if len(first) == 2 and first[1] in ':|' and first[0].isalpha():
        return None
    return tuple(parts)


def _extract_zip(src_zip, dest_dir):
    """解压 zip 到 dest_dir，同名覆盖；返回解出的文件数。

    逐条目流式写临时文件后 os.replace，避免解压中途留下半个文件。
    """
    extracted = 0
    with zipfile.ZipFile(src_zip, 'r') as zf:
        for info in zf.infolist():
            parts = _safe_member_name(info.filename)
            if parts is None:
                raise ValueError('压缩包内含不安全路径：%s' % info.filename)
            target = os.path.join(dest_dir, *parts)
            if info.is_dir() or info.filename.endswith('/'):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + '.extract.tmp'
            try:
                with zf.open(info) as src, open(tmp, 'wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                os.replace(tmp, target)
            except Exception:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                raise
            extracted += 1
    return extracted


def _pack_updates_dir():
    """将 updates 目录全量打包为 updates.zip（原子替换）。"""
    tmp = UPDATES_ZIP + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    count = 0
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(UPDATES_DIR):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, UPDATES_DIR)
                zf.write(full, rel.replace(os.sep, '/'))
                count += 1
    os.replace(tmp, UPDATES_ZIP)
    return count


def _pvf_sync_error(patch_has_pvf, pvf_src):
    """补丁含 Script.pvf 时同步覆盖游戏服务器源文件（确认服务器已停止）。

    返回 (pvf_updated, error_message)：同步失败时 error_message 给出
    处置提示（此时补丁包已入库），成功为 (True, None)。
    """
    if not patch_has_pvf or not os.path.isfile(pvf_src):
        return False, None
    if serverctl.server_status()['running']:
        return False, ('补丁包含 Script.pvf，但服务器已在上传期间启动，'
                       'PVF 未同步（补丁包已入库，请停止服务器后重新上传）')
    if serverctl.server_transitioning():
        return False, ('补丁包含 Script.pvf，但服务器已进入启动流程，'
                       'PVF 未同步（补丁包已入库，请等待启动完成并停止服务器后重新上传）')
    serverctl.install_pvf(pvf_src)
    return True, None


def _ingest_patch(tmp_zip):
    """读取补丁内容判定是否含 Script.pvf，并解压覆盖到 updates 目录。

    返回 (patch_has_pvf, extracted, error_message)；损坏包/不安全路径时
    error_message 非空。
    """
    try:
        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            patch_has_pvf = any(
                _safe_member_name(n) == ('Script.pvf',)
                for n in zf.namelist())
        os.makedirs(UPDATES_DIR, exist_ok=True)
        extracted = _extract_zip(tmp_zip, UPDATES_DIR)
        return patch_has_pvf, extracted, None
    except zipfile.BadZipFile:
        return False, 0, '补丁包不是有效的 zip 文件'
    except ValueError as e:
        return False, 0, str(e)


def _save_upload_and_recheck(file_storage, tmp_zip):
    """流式保存上传的补丁包并做上传后二次状态检查（上传耗时较长，期间可能
    被他人启动）。失败返回错误消息，成功返回 None。"""
    os.makedirs(os.path.dirname(UPDATES_ZIP), exist_ok=True)
    file_storage.save(tmp_zip)        # 流式保存，不占内存
    if serverctl.server_status()['running']:
        return '上传期间服务器已启动，本次升级已取消'
    if serverctl.server_transitioning():
        return '上传期间服务器已进入启动流程，本次升级已取消'
    return None


def apply_patch(file_storage):
    """处理管理员上传的补丁 zip，返回 (ok, data_or_message)。

    流程：仅允许服务器停止时进行 → 保存 zip → 解压覆盖到 updates 目录 →
    重打包 updates.zip →（含 Script.pvf 时）同步到游戏服务器并触发清单
    重建 → 基线版本号 +1。
    """
    if serverctl.server_status()['running']:
        return False, '服务器正在运行，请先停止服务器再升级游戏'
    if serverctl.server_transitioning():
        return False, '服务器正在启动中，请等待启动完成后再升级游戏'

    if not file_storage or not (file_storage.filename or '').lower().endswith('.zip'):
        return False, '仅接受 .zip 补丁包文件'

    acquired = _UPGRADE_LOCK.acquire(blocking=False)
    if not acquired:
        return False, '已有升级处理正在进行，请稍候'

    tmp_zip = UPDATES_ZIP + '.uploading.tmp'
    try:
        save_err = _save_upload_and_recheck(file_storage, tmp_zip)
        if save_err:
            return False, save_err

        patch_has_pvf, extracted, ingest_err = _ingest_patch(tmp_zip)
        if ingest_err:
            return False, ingest_err

        packed = _pack_updates_dir()

        # 补丁含 Script.pvf：同步覆盖游戏服务器源文件（再次确认已停止）
        pvf_updated, sync_err = _pvf_sync_error(
            patch_has_pvf, os.path.join(UPDATES_DIR, 'Script.pvf'))
        if sync_err:
            return False, sync_err

        state = get_state()
        _write_state(state['baseline'], state['current'] + 1, touch_current=True)

        size = os.path.getsize(UPDATES_ZIP)
        log.info('游戏升级完成：基线 v%d 当前 v%d，updates %d 个文件，'
                 '包 %d 字节，PVF 同步=%s',
                 state['baseline'], state['current'] + 1, packed, size,
                 pvf_updated)
        return True, {'baseline': state['baseline'],
                      'current': state['current'] + 1,
                      'files': packed, 'zip_size': size,
                      'extracted': extracted, 'pvf_updated': pvf_updated}
    finally:
        if os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
        _UPGRADE_LOCK.release()


def after_upgrade_pvf_rebuild():
    """补丁更新了 PVF 后触发道具清单重建（由端点调用，失败不影响升级结果）。"""
    return pvfitem.start_build(force=True)


def set_baseline():
    """将当前版本设置为基线版本：清空 updates 目录、移除补丁包、
    baseline = current。返回 (ok, data_or_message)。

    重设后，本地版本低于新基线的客户端将被拒绝增量升级
    （提示重新下载完整客户端）。
    """
    acquired = _UPGRADE_LOCK.acquire(blocking=False)
    if not acquired:
        return False, '已有升级处理正在进行，请稍候'
    try:
        state = get_state()
        # 清空 updates 目录内容（目录本身保留）
        if os.path.isdir(UPDATES_DIR):
            for name in os.listdir(UPDATES_DIR):
                path = os.path.join(UPDATES_DIR, name)
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                except OSError:
                    log.exception('清理 updates 目录失败：%s', path)
                    return False, '清理 updates 目录失败：%s' % name
        # 当前即基线：不再需要补丁包
        if os.path.exists(UPDATES_ZIP):
            os.remove(UPDATES_ZIP)
        for tmp in (UPDATES_ZIP + '.tmp', UPDATES_ZIP + '.uploading.tmp'):
            if os.path.exists(tmp):
                os.remove(tmp)

        _write_state(state['current'], state['current'], touch_baseline=True)
        log.info('基线版本已重设为 v%d（updates 目录已清空）', state['current'])
        return True, {'baseline': state['current'],
                      'current': state['current']}
    finally:
        _UPGRADE_LOCK.release()
