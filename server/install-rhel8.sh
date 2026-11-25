#!/bin/bash
# =============================================================================
# DGate 服务端一键部署脚本 —— RHEL 8+（Rocky / AlmaLinux / Oracle Linux /
# CentOS Stream 8+）。游戏二进制依赖 CentOS 7 的 glibc 2.17，无法在
# RHEL 8+（glibc >= 2.28）原生运行，因此本脚本只支持 Docker 容器部署
# （镜像如 1995chen/dnf:centos7-2.1.9.fix1）。
#
# 用法（root 执行）：
#   部署包解压后：  bash install-rhel8.sh            （IP + HTTP 模式）
#                   bash install-rhel8.sh --domain   （域名 + HTTPS 模式）
#   仓库内开发布局（本脚本位于 server/ 目录内）同样支持。
#
# 八步流程：
#   1. 环境预检：OS 门（RHEL 8+ 及衍生版）、部署布局、基础工具（含 tar）
#   2. Docker 与游戏容器检测（镜像 ancestor 过滤；单容器自动选定、
#      多容器列表选择、无容器拦截引导）
#   3. 双映射解析：游戏目录（挂载源扫描 df_game_r）与 MySQL 端口
#      （PortBindings 3306/tcp）作为提问默认值，可修改；游戏目录仅检查存在性
#   4. Python 3.12：dnf AppStream 优先，失败回退 python-build-standalone
#   5. venv + 依赖（flask/pymysql/pycryptodome/zhconv）
#   6. 域名绑定（--domain 可选）：DNS IPv4 校验 + certbot（venv 内，
#      standalone 模式）签发 + 续期 systemd timer
#   7. 部署代码（保留已有 config.json / data/）+ 密钥落位：后台已有私钥则沿用
#      并由其重导公钥到游戏目录（游戏服务器只使用公钥，私钥不进游戏目录），
#      无私钥则全新生成；config.json 合并（python 生成，敌意密码安全）
#   8. pymysql 复核 MySQL 5.x + systemd 服务 + 4 个管理脚本装入
#      调用者主目录装入 dgate-{start,stop,restart,status}（root -> /root，
#      非 root 账户 -> ~）+ 健康自检
# =============================================================================
set -u
set -o pipefail

IMAGE='1995chen/dnf:centos7-2.1.9.fix1'
PREFIX=/opt/dgate
SERVER_DIR="$PREFIX/server"
VENV_DIR="$PREFIX/venv"
PYBS_TAG=20261123
PYBS_VER=3.12.16
# python-build-standalone 仅从大陆镜像下载（不使用 github）：
# npmmirror 二进制镜像（实测可用）为主源，TUNA github-release 镜像备选
PYBS_FILE="cpython-${PYBS_VER}%2B${PYBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYBS_URLS=(
    "https://registry.npmmirror.com/-/binary/python-build-standalone/${PYBS_TAG}/${PYBS_FILE}"
    "https://mirrors.tuna.tsinghua.edu.cn/github-release/astral-sh/python-build-standalone/${PYBS_TAG}/${PYBS_FILE}"
)

# ---------------------------------------------------------------------------
# 输出与交互工具
# ---------------------------------------------------------------------------
step() { echo; echo "===== [$1/8] $2 ====="; }
ok()   { printf '\033[32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[X]\033[0m %s\n' "$*" >&2; exit 1; }

ask() {
    # ask <提示> [默认值] —— 结果存入 REPLY；EOF/Ctrl+D 时干净退出（零副作用）
    local prompt="$1" def="${2-}"
    if [ -n "$def" ]; then
        printf '%s [%s]: ' "$prompt" "$def"
    else
        printf '%s: ' "$prompt"
    fi
    if ! read -r REPLY; then
        echo
        die "输入流已关闭（EOF），中止部署，未做任何变更。"
    fi
    [ -z "$REPLY" ] && REPLY="$def"
    return 0
}

ask_secret() {
    # ask_secret <提示> [默认值] —— 密码输入不回显；结果存入 REPLY；
    # 直接回车采用默认值（给了默认值时，不回显）；自设密码时回显一遍
    # 供确认；EOF/Ctrl+D 干净退出
    local prompt="$1" def="${2-}"
    if [ -n "$def" ]; then
        printf '%s [%s]: ' "$prompt" "$def"
    else
        printf '%s: ' "$prompt"
    fi
    if ! read -rs REPLY; then
        echo
        die "输入流已关闭（EOF），中止部署，未做任何变更。"
    fi
    if [ -z "$REPLY" ] && [ -n "$def" ]; then
        REPLY="$def"
        echo
        return 0
    fi
    if [ -n "$REPLY" ]; then
        echo "$REPLY"    # 自设密码：回显一遍供确认
    else
        echo
    fi
    return 0
}

# ---------------------------------------------------------------------------
# [1/8] 环境预检
# ---------------------------------------------------------------------------
step 1 "环境预检"

[ "$(id -u)" -eq 0 ] || die "请以 root 执行本脚本。"

DOMAIN_MODE=0
if [ "${1-}" = "--domain" ]; then
    DOMAIN_MODE=1
    DOMAIN="${2-}"
elif [ -n "${1-}" ]; then
    die "未知参数：$1（用法：install-rhel8.sh [--domain [域名]]）"
fi

# OS 门：仅放行 RHEL 8+ 及衍生版（本脚本为 docker-only 设计）
if [ ! -r /etc/os-release ]; then
    die "无法读取 /etc/os-release，无法识别操作系统。"
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID-}" in
    rhel|rocky|almalinux|ol|centos) ;;
    *) die "本脚本仅支持 RHEL 8+ 及衍生版（当前：${ID-unknown}）。CentOS 7 请用 install-centos7.sh。" ;;
esac
case "${VERSION_ID-}" in
    8*|9*|1[0-9]*) ;;
    *) die "仅支持 RHEL 8+（当前版本：${VERSION_ID-unknown}）。CentOS 7 请用 install-centos7.sh。" ;;
esac
ok "操作系统：${PRETTY_NAME-unknown}"

# 部署布局：部署包布局（脚本与 server/ 同级）或仓库内布局（脚本在 server/ 内）
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [ -f "$SCRIPT_DIR/server/app.py" ]; then
    SRC_SERVER="$SCRIPT_DIR/server"
elif [ -f "$SCRIPT_DIR/app.py" ] && [ -d "$SCRIPT_DIR/dgate" ]; then
    SRC_SERVER="$SCRIPT_DIR"
else
    die "找不到服务端代码：部署包解压后应与本脚本同级存在 server/ 目录（含 app.py）。"
fi
for s in dgate-start dgate-stop dgate-restart dgate-status; do
    [ -f "$SCRIPT_DIR/$s" ] || die "缺少服务管理脚本 $s（应与本脚本同目录）。"
done
ok "服务端代码：$SRC_SERVER（4 个管理脚本齐备）"

# 基础工具：Rocky/Alma 最小安装常缺 tar，一次补齐
MISSING=()
for t in tar curl openssl ip; do
    command -v "$t" >/dev/null 2>&1 || MISSING+=("$t")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    PKGS=()
    for t in "${MISSING[@]}"; do
        [ "$t" = "ip" ] && t=iproute
        PKGS+=("$t")
    done
    echo "安装基础工具：${PKGS[*]} ..."
    dnf install -y "${PKGS[@]}" >/dev/null 2>&1 || die "基础工具安装失败，请检查软件源。"
fi
ok "基础工具齐备（tar/curl/openssl/ip）"

# ---------------------------------------------------------------------------
# [2/8] Docker 与游戏容器检测
# ---------------------------------------------------------------------------
step 2 "Docker 与游戏容器检测"

command -v docker >/dev/null 2>&1 || die "未安装 docker。请先安装并启动 docker 后重跑本脚本。"
systemctl is-active docker >/dev/null 2>&1 || die "docker 服务未运行（systemctl start docker）。"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    die "缺少游戏镜像 $IMAGE。
    请先拉取并按需创建容器（含游戏目录与 MySQL 端口映射），例如：
      docker pull $IMAGE
    然后重跑本脚本。"
fi

mapfile -t CONTAINERS < <(docker ps -a --filter "ancestor=$IMAGE" --format '{{.Names}}')
if [ "${#CONTAINERS[@]}" -eq 0 ]; then
    die "已拉取镜像但未找到基于它的容器（docker ps -a --filter ancestor=$IMAGE）。
    请创建游戏容器（含游戏目录与 MySQL 端口映射）后重跑本脚本。"
fi
if [ "${#CONTAINERS[@]}" -eq 1 ]; then
    CONTAINER="${CONTAINERS[0]}"
    ok "游戏容器（自动选定）：$CONTAINER"
else
    echo "检测到多个游戏容器："
    i=1
    for c in "${CONTAINERS[@]}"; do
        echo "  $i) $c"
        i=$((i + 1))
    done
    ask "请选择容器序号" "1"
    case "$REPLY" in
        ''|*[!0-9]*) die "无效序号：$REPLY" ;;
    esac
    [ "$REPLY" -ge 1 ] && [ "$REPLY" -le "${#CONTAINERS[@]}" ] || die "序号超出范围：$REPLY"
    CONTAINER="${CONTAINERS[$((REPLY - 1))]}"
    ok "游戏容器（已选择）：$CONTAINER"
fi

# ---------------------------------------------------------------------------
# [3/8] 双映射解析（游戏目录 + MySQL 端口）
# ---------------------------------------------------------------------------
step 3 "游戏目录与 MySQL 端口解析"

# 游戏目录：扫描容器挂载源，实解包含 df_game_r 的宿主机目录（不按固定容器
# 路径匹配——镜像内路径可能被修改，挂载源扫描与布局无关）
mapfile -t MOUNTS < <(docker inspect "$CONTAINER" \
    --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}')
GAME_DIR_CANDIDATES=()
for src in "${MOUNTS[@]}"; do
    [ -n "$src" ] && [ -d "$src" ] && [ -e "$src/df_game_r" ] || continue
    GAME_DIR_CANDIDATES+=("$src")
done

if [ "${#GAME_DIR_CANDIDATES[@]}" -eq 1 ]; then
    GAME_DIR_DEF="${GAME_DIR_CANDIDATES[0]}"
elif [ "${#GAME_DIR_CANDIDATES[@]}" -gt 1 ]; then
    echo "多个挂载目录包含 df_game_r："
    i=1
    for d in "${GAME_DIR_CANDIDATES[@]}"; do
        echo "  $i) $d"
        i=$((i + 1))
    done
    ask "请选择游戏目录序号" "1"
    case "$REPLY" in
        ''|*[!0-9]*) die "无效序号：$REPLY" ;;
    esac
    [ "$REPLY" -ge 1 ] && [ "$REPLY" -le "${#GAME_DIR_CANDIDATES[@]}" ] || die "序号超出范围：$REPLY"
    GAME_DIR_DEF="${GAME_DIR_CANDIDATES[$((REPLY - 1))]}"
else
    GAME_DIR_DEF=""
fi

# MySQL 宿主机端口：PortBindings 中 3306/tcp 的映射
MYSQL_PORT_DEF=$(docker inspect "$CONTAINER" --format \
    '{{range $p, $b := .HostConfig.PortBindings}}{{if eq $p "3306/tcp"}}{{(index $b 0).HostPort}}{{end}}{{end}}')
[ -n "$MYSQL_PORT_DEF" ] || MYSQL_PORT_DEF=3306

echo "已从容器配置解析出默认值（可直接回车采用，或输入覆盖）："
ask "游戏目录（df_game_r / Script.pvf / publickey.pem 宿主机路径）" "$GAME_DIR_DEF"
GAME_DATA_DIR=$REPLY
[ -d "$GAME_DATA_DIR" ] || die "游戏目录不存在：$GAME_DATA_DIR"
ok "游戏目录：$GAME_DATA_DIR"

ask "MySQL 宿主机端口" "$MYSQL_PORT_DEF"
case "$REPLY" in
    ''|*[!0-9]*) die "MySQL 端口必须是数字：$REPLY" ;;
esac
MYSQL_PORT=$REPLY
ok "MySQL 端口：$MYSQL_PORT"

ask "MySQL 用户名" "game"
MYSQL_USER=$REPLY
[ -n "$MYSQL_USER" ] || die "MySQL 用户名不能为空。"
ask_secret "MySQL 密码" "uu5!^%jg"
MYSQL_PASSWORD=$REPLY
[ -n "$MYSQL_PASSWORD" ] || die "MySQL 密码不能为空。"

# ---------------------------------------------------------------------------
# [4/8] Python 3.12
# ---------------------------------------------------------------------------
step 4 "Python 3.12"

PYBIN=""
if command -v python3.12 >/dev/null 2>&1; then
    PYBIN=$(command -v python3.12)
    ok "已有 python3.12：$PYBIN（$("$PYBIN" --version 2>&1)）"
else
    echo "尝试 dnf AppStream 安装 python3.12 ..."
    if dnf install -y python3.12 >/dev/null 2>&1 && command -v python3.12 >/dev/null 2>&1; then
        PYBIN=$(command -v python3.12)
        ok "dnf 安装成功：$PYBIN（$("$PYBIN" --version 2>&1)）"
    fi
fi

if [ -z "$PYBIN" ]; then
    echo "dnf 路径不可用，回退 python-build-standalone 预编译独立版 ..."
    mkdir -p /opt/python312
    PYBS_OK=""
    for u in "${PYBS_URLS[@]}"; do
        echo "尝试下载 $u ..."
        if curl -fL --retry 2 --connect-timeout 10 -o /tmp/pybs.tar.gz "$u"; then
            PYBS_OK="$u"
            break
        fi
        rm -f /tmp/pybs.tar.gz
    done
    [ -n "$PYBS_OK" ] || die "pybs 下载失败（已尝试全部大陆镜像源），请检查网络。"
    tar -xzf /tmp/pybs.tar.gz -C /opt/python312 --strip-components=1 \
        || die "pybs 解压失败。"
    rm -f /tmp/pybs.tar.gz
    PYBIN=/opt/python312/bin/python3.12
    [ -x "$PYBIN" ] || die "pybs 安装后未找到 $PYBIN。"
    ok "pybs 安装成功：$PYBIN（$("$PYBIN" --version 2>&1)）"
fi

# ---------------------------------------------------------------------------
# [5/8] venv 与依赖
# ---------------------------------------------------------------------------
step 5 "venv 与依赖"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYBIN" -m venv "$VENV_DIR" || die "venv 创建失败（$PYBIN -m venv $VENV_DIR）。"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q \
    || warn "pip 升级失败（非致命），继续。"
"$VENV_DIR/bin/pip" install -r "$SRC_SERVER/requirements.txt" -q \
    || die "依赖安装失败（flask/pymysql/pycryptodome/zhconv），请检查网络。"
ok "venv 就绪：$VENV_DIR"

# ---------------------------------------------------------------------------
# [6/8] 域名绑定（--domain 可选）
# ---------------------------------------------------------------------------
SSL_CERT_CFG=""
SSL_KEY_CFG=""

step 6 "域名绑定（可选）"

if [ "$DOMAIN_MODE" -eq 1 ]; then
    if [ -z "$DOMAIN" ]; then
        ask "绑定域名（如 dof.example.io）"
        DOMAIN=$REPLY
    fi
    [ -n "$DOMAIN" ] || die "域名不能为空。"

    # DNS IPv4 校验：getent ahostsv4 只返回 A 记录（天然过滤 AAAA 误报）
    LOCAL_IPS=$(ip -4 -o addr show scope global 2>/dev/null \
        | awk '{split($4, a, "/"); print a[1]}')
    A_RECORDS=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u)
    [ -n "$A_RECORDS" ] || die "域名 $DOMAIN 无 IPv4 A 记录（getent ahostsv4）。请先配置 DNS 解析。"
    echo "域名解析 A 记录：$A_RECORDS"
    echo "本机 IPv4：$LOCAL_IPS"
    HIT=0
    for ip in $A_RECORDS; do
        for lip in $LOCAL_IPS; do
            [ "$ip" = "$lip" ] && HIT=1 && break 2
        done
    done
    [ "$HIT" -eq 1 ] || die "域名 $DOMAIN 未解析到本机（A 记录与本机 IPv4 无交集），拦截。请先修正 DNS。"
    ok "DNS 校验通过：$DOMAIN -> 本机"

    ask "Let's Encrypt 邮箱"
    LE_EMAIL=$REPLY
    [ -n "$LE_EMAIL" ] || die "LE 邮箱不能为空。"

    "$VENV_DIR/bin/pip" install -q certbot || die "certbot 安装失败。"
    echo "申请证书（standalone 模式，需 80 端口空闲）..."
    "$VENV_DIR/bin/certbot" certonly --standalone --agree-tos -n \
        -m "$LE_EMAIL" -d "$DOMAIN" || die "证书签发失败（确认 80 端口空闲、DNS 已生效）。"
    SSL_CERT_CFG="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    SSL_KEY_CFG="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
    ok "证书已签发：$SSL_CERT_CFG"

    # 续期 systemd timer
    cat > /etc/systemd/system/dgate-certbot-renew.service <<'UNITEOF'
[Unit]
Description=DGate Let's Encrypt certificate renewal

[Service]
Type=oneshot
ExecStart=/opt/dgate/venv/bin/certbot -q renew
UNITEOF
    cat > /etc/systemd/system/dgate-certbot-renew.timer <<'UNITEOF'
[Unit]
Description=DGate Let's Encrypt renewal timer

[Timer]
OnCalendar=monthly
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
UNITEOF
    systemctl daemon-reload
    systemctl enable --now dgate-certbot-renew.timer >/dev/null 2>&1
    ok "证书续期 timer 已启用（每月）"
else
    echo "IP + HTTP 模式（未传 --domain，跳过域名与证书）。"
fi

# ---------------------------------------------------------------------------
# [7/8] 代码部署 + 密钥落位 + config.json
# ---------------------------------------------------------------------------
step 7 "代码部署、密钥落位与配置"

mkdir -p "$SERVER_DIR"
# tar 管道覆盖代码，排除运行期数据与配置（保留目标机已有 config.json / data/ /
# __pycache__ 不被覆盖；排除脚本自身与密钥）
tar -cf - -C "$SRC_SERVER" \
        --exclude='./config.json' \
        --exclude='./data' \
        --exclude='./__pycache__' \
        --exclude='__pycache__' \
        --exclude='./install-rhel8.sh' \
        --exclude='./install-centos7.sh' \
        --exclude='./dgate-start' \
        --exclude='./dgate-stop' \
        --exclude='./dgate-restart' \
        --exclude='./dgate-status' \
        --exclude='./privatekey.pem' \
        --exclude='./publickey.pem' \
        . | tar -xf - -C "$SERVER_DIR" || die "代码部署失败（tar 管道）。"
ok "代码已部署：$SERVER_DIR（已有 config.json/data/ 密钥保留）"

# 密钥处理（游戏服务器只使用公钥，私钥仅存在于后台 /opt/dgate/privatekey.pem，
# 任何情况下不进入游戏目录）：
#   a) 后台已有私钥 → 沿用；由其重导公钥覆盖游戏目录与项目根（旧文件备份 .bak），
#      配对由导出关系天然保证（与游戏目录公钥此前是否失配无关）
#   b) 后台无私钥 → 全新生成并部署公钥
#   c) 游戏目录若存在 privatekey.pem（游戏服务器不使用私钥）→ 一律视为历史残留：
#      不读取、不采纳、不复制，仅输出提醒
GAME_PUB="$GAME_DATA_DIR/publickey.pem"
DGATE_PRIV="$PREFIX/privatekey.pem"
DGATE_PUB="$PREFIX/publickey.pem"

if [ -f "$DGATE_PRIV" ]; then
    # 重装：沿用既有私钥，重导公钥到游戏目录与项目根（备份 .bak）
    cp -a "$GAME_PUB" "$GAME_PUB.bak" 2>/dev/null || true
    openssl rsa -in "$DGATE_PRIV" -pubout -out "$GAME_PUB" 2>/dev/null \
        || die "从既有私钥导出公钥失败。"
    openssl rsa -in "$DGATE_PRIV" -pubout -out "$DGATE_PUB" 2>/dev/null \
        || die "从既有私钥导出公钥失败。"
    ok "沿用既有私钥 $DGATE_PRIV，公钥已重新部署到 $GAME_PUB"
else
    openssl genrsa -out "$DGATE_PRIV" 2048 2>/dev/null || die "私钥生成失败（openssl）。"
    openssl rsa -in "$DGATE_PRIV" -pubout -out "$GAME_PUB" 2>/dev/null \
        || die "公钥导出失败。"
    openssl rsa -in "$DGATE_PRIV" -pubout -out "$DGATE_PUB" 2>/dev/null \
        || die "公钥导出失败。"
    ok "已生成全新 RSA-2048 密钥对（$DGATE_PRIV / $GAME_PUB）"
fi
chmod 600 "$DGATE_PRIV"

if [ -f "$GAME_DATA_DIR/privatekey.pem" ]; then
    echo "   提示：游戏目录存在 privatekey.pem（游戏服务器不使用私钥，应为历史残留），可手动删除。"
fi

# config.json 合并：python 生成（argv 传值，敌意密码中的 $、反引号、反斜杠
# 原样落盘；引号 heredoc 避免 shell 二次展开）
"$VENV_DIR/bin/python" - "$MYSQL_PORT" "$MYSQL_USER" "$MYSQL_PASSWORD" \
        "$CONTAINER" "$GAME_DATA_DIR" "$SSL_CERT_CFG" "$SSL_KEY_CFG" <<'PYEOF'
import json
import os
import sys

port, user, password, container, data_dir, ssl_cert, ssl_key = sys.argv[1:8]
path = '/opt/dgate/server/config.json'
cfg = {}
if os.path.exists(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
if not isinstance(cfg, dict):
    cfg = {}

listen = cfg.setdefault('listen', {})
listen.setdefault('host', '0.0.0.0')
listen.setdefault('port', 5000)
ssl = cfg.setdefault('ssl', {})
if ssl_cert:
    ssl['cert'] = ssl_cert
if ssl_key:
    ssl['key'] = ssl_key
mysql = cfg.setdefault('mysql', {})
mysql['host'] = '127.0.0.1'
mysql['port'] = int(port)
mysql['user'] = user
mysql['password'] = password
game = cfg.setdefault('game', {})
game['mode'] = 'docker'
game['container'] = container
game['data_dir'] = data_dir

with open(path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write('\n')
print('\033[32m[OK]\033[0m config.json 已写入（mysql/game 节合并，监听/SSL 保留原值）')
PYEOF
[ $? -eq 0 ] || die "config.json 生成失败。"

# ---------------------------------------------------------------------------
# [8/8] MySQL 复核、systemd 服务与管理脚本
# ---------------------------------------------------------------------------
step 8 "MySQL 复核、systemd 服务与自检"

"$VENV_DIR/bin/python" - <<'PYEOF'
import json
import sys

import pymysql

with open('/opt/dgate/server/config.json', encoding='utf-8') as f:
    cfg = json.load(f)
m = cfg['mysql']
try:
    # charset 必须显式 utf8：pymysql 默认 utf8mb4，MySQL 5.0.x 不认识
    # （与 dgate/db.py 的 MYSQL_CHARSET 保持一致）
    conn = pymysql.connect(host=m['host'], port=m['port'],
                           user=m['user'], password=m['password'],
                           charset='utf8', connect_timeout=10)
except Exception as e:                                   # noqa: BLE001
    print('\033[31m[X]\033[0m MySQL 连接失败：%s' % e)
    sys.exit(2)
try:
    with conn.cursor() as cur:
        cur.execute('SELECT VERSION()')
        ver = cur.fetchone()[0]
finally:
    conn.close()
major = int(str(ver).split('.')[0])
print('\033[32m[OK]\033[0m MySQL 连接正常：%s @ %s:%d' % (ver, m['host'], m['port']))
if major != 5:
    print('\033[31m[X]\033[0m MySQL 版本非 5.x（游戏服要求 MySQL 5.x）。')
    sys.exit(3)
PYEOF
rc=$?
[ $rc -eq 0 ] || die "MySQL 复核未通过（rc=$rc）：请核对地址/端口/账号密码。"

# systemd 服务单元
cat > /etc/systemd/system/dgate.service <<'UNITEOF'
[Unit]
Description=DGate game account admin backend
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=/opt/dgate/server
ExecStart=/opt/dgate/venv/bin/python app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF

# 4 个管理脚本装入调用者主目录（root -> /root；非 root 账户 -> $HOME；
# sudo 提权执行时取 SUDO_USER 的主目录；dgate- 前缀与游戏 /root/run、
# /root/stop 命名零冲突），并清理旧版装入 /usr/local/sbin 的残留
DEPLOY_USER=$(id -un)
TARGET_HOME=$(getent passwd "$DEPLOY_USER" 2>/dev/null | cut -d: -f6)
if [ -n "${SUDO_USER-}" ]; then
    TARGET_HOME=$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)
fi
[ -d "$TARGET_HOME" ] || TARGET_HOME="$PREFIX"
for s in dgate-start dgate-stop dgate-restart dgate-status; do
    cp -a "$SCRIPT_DIR/$s" "$TARGET_HOME/$s"
    chmod 755 "$TARGET_HOME/$s"
    rm -f /usr/local/sbin/"$s"
done
ok "管理脚本已安装：$TARGET_HOME/dgate-{start,stop,restart,status}"

systemctl daemon-reload
systemctl enable dgate >/dev/null 2>&1
systemctl restart dgate || {
    journalctl -u dgate -n 30 --no-pager >&2 2>/dev/null || true
    die "dgate 服务启动失败（systemctl restart dgate）。"
}

# 健康自检：/api/health 200（最多 60 秒）
APP_PORT=$("$VENV_DIR/bin/python" -c \
    "import json;print(json.load(open('/opt/dgate/server/config.json'))['listen']['port'])" \
    2>/dev/null || echo 5000)
echo "健康自检：http://127.0.0.1:${APP_PORT}/api/health ..."
HEALTH_OK=0
for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${APP_PORT}/api/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done
if [ "$HEALTH_OK" -ne 1 ]; then
    echo "[X] 健康自检未通过（60 秒内 /api/health 未返回 200）。最近日志：" >&2
    journalctl -u dgate -n 30 --no-pager >&2 2>/dev/null || true
    die "健康自检失败。"
fi
ok "健康自检通过（/api/health 200）"

echo
echo "==============================================================="
echo " DGate 部署完成"
if [ "$DOMAIN_MODE" -eq 1 ]; then
    echo "   后台地址：https://$DOMAIN:${APP_PORT}/"
else
    LOCAL_IP=$(ip -4 -o addr show scope global 2>/dev/null | awk 'NR==1{split($4,a,"/");print a[1]}')
    echo "   后台地址：http://${LOCAL_IP:-<服务器IP>}:${APP_PORT}/"
fi
echo "   初始超管：admin / admin123456（登录后请立即修改）"
echo "   服务管理：$TARGET_HOME/dgate-start | $TARGET_HOME/dgate-stop | $TARGET_HOME/dgate-restart | $TARGET_HOME/dgate-status"
echo "   服务日志：journalctl -u dgate -f"
printf '\033[33m   注意：若游戏服务器已经启动，用户可能无法正常登录游戏，请在后台管理页面重启游戏服务器（后台「游戏服务」页）。\033[0m\n'
echo "==============================================================="
