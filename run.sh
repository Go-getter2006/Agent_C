#!/usr/bin/env bash
# 一条命令装好并启动。第一次运行会自动：生成 .env 和密钥、问一次 DeepSeek 的 key、
# 建 Python 虚拟环境装依赖、装前端依赖并构建、建表；之后再运行只做有变化的那几步。
#
#   ./run.sh            启动（网页 + 后台同步）。老师在登录页选“教师”用 XDOJ 账号登录，自动同步班级和名单；
#                       学生用名单里的学号和自己的 XDOJ 密码登录
#   ./run.sh demo       用演示数据启动（单独一个库，不碰真实数据）
#   ./run.sh update     拉最新代码、更新依赖、重新构建，然后启动
#   ./run.sh deps       在 Ubuntu / Debian 上装好 Python、gcc、Node 20（要 sudo，只需一次）
#   ./run.sh service    装成开机自启的系统服务（要 sudo）
#
# 端口默认 8000，可以 PORT=8080 ./run.sh 改。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

find_python() {
  for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      echo "$p"; return
    fi
  done
  die "需要 Python 3.11 以上。Ubuntu / Debian 上可以先运行 ./run.sh deps"
}

stamp_ok() { [ -f "$2" ] && [ "$(sha1sum "$1" | cut -d' ' -f1)" = "$(cat "$2")" ]; }
stamp_set() { sha1sum "$1" | cut -d' ' -f1 > "$2"; }

setup_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    say "已生成 .env"
  fi
  if ! grep -q '^SECRET_KEY=..*' .env; then
    local key
    key="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$key|" .env
    say "已生成 SECRET_KEY（别删，教师的 XDOJ 密码是用它加密的）"
  fi
  if grep -q '^DEEPSEEK_API_KEY=$' .env && [ -t 0 ]; then
    printf 'DeepSeek 的 API key（没有就直接回车，以后可以在 .env 里补）: '
    read -rs dskey; echo
    if [ -n "$dskey" ]; then
      sed -i "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=$dskey|" .env
      say "已写进 .env"
    fi
  fi
}

setup_backend() {
  [ -d backend/.venv ] || { say "建 Python 虚拟环境"; "$PY" -m venv backend/.venv; }
  if ! stamp_ok backend/requirements.txt backend/.venv/.req-stamp; then
    say "安装后端依赖"
    backend/.venv/bin/pip install -q --upgrade pip
    backend/.venv/bin/pip install -q -r backend/requirements.txt
    stamp_set backend/requirements.txt backend/.venv/.req-stamp
  fi
  command -v gcc >/dev/null 2>&1 || printf '\033[33m没有 gcc：小题的“运行检查”用不了。Ubuntu 上 sudo apt install gcc\033[0m\n'
}

setup_frontend() {
  command -v node >/dev/null 2>&1 || die "需要 Node 20 以上。Ubuntu / Debian 上可以先运行 ./run.sh deps"
  if [ ! -d frontend/node_modules ] || ! stamp_ok frontend/package-lock.json frontend/node_modules/.lock-stamp; then
    say "安装前端依赖"
    (cd frontend && npm ci --no-audit --no-fund --loglevel=error)
    stamp_set frontend/package-lock.json frontend/node_modules/.lock-stamp
  fi
  if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html frontend/public -newer frontend/dist/index.html -print -quit)" ]; then
    say "构建前端"
    (cd frontend && npm run build --silent >/dev/null)
  fi
}

setup_all() {
  PY="$(find_python)"
  setup_env
  setup_backend
  setup_frontend
}

# 以前按旧步骤把演示数据灌进了正式的库：挪开（不删），让正式的库从老师登录开始只有真实的 XDOJ 账号
move_demo_out() {
  local f="$ROOT/data/agent.sqlite3"
  [ -f "$f" ] && [ -z "${DATABASE_URL:-}" ] || return 0
  if backend/.venv/bin/python - "$f" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    sys.exit(0 if c.execute("select 1 from xdoj_servers where code='demo'").fetchone() else 1)
except Exception:
    sys.exit(1)
PY
  then
    local bak="$ROOT/data/agent-with-demo-$(date +%Y%m%d%H%M%S).sqlite3"
    mv "$f" "$bak"
    rm -f "$f-wal" "$f-shm"
    say "原来的库里混着演示数据，已经挪到 $(basename "$bak")，正式的库从空的开始；老师需要重新登录一次"
  fi
}

start() {
  cd "$ROOT/backend"
  mkdir -p "$ROOT/data"
  .venv/bin/python -m app.cli init-db
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  say "打开 http://127.0.0.1:$PORT ${ip:+（别的电脑：http://$ip:$PORT）}，按 Ctrl+C 停止。后台同步在同一个进程里运行"
  exec .venv/bin/python -m app.cli serve --host "$HOST" --port "$PORT"
}

case "${1:-}" in
  "")
    setup_all
    move_demo_out
    start
    ;;
  start)  # 系统服务用：不做安装，直接启动
    start
    ;;
  demo)
    setup_all
    export DATABASE_URL="sqlite:///$ROOT/data/demo.sqlite3"
    if ! grep -q '^DEV_LOGIN_PASSWORD=..*' .env; then
      sed -i "s|^DEV_LOGIN_PASSWORD=.*|DEV_LOGIN_PASSWORD=demo|" .env
      say "演示口令设成了 demo（在 .env 的 DEV_LOGIN_PASSWORD）"
    fi
    mkdir -p data
    (cd backend && .venv/bin/python -m app.cli demo-seed >/dev/null)
    say "演示账号：学生 2500000001；教师在登录页选“教师”，账号 demo-teacher。密码都是 .env 里的 DEV_LOGIN_PASSWORD"
    start
    ;;
  update)
    say "拉最新代码"
    git pull --ff-only
    setup_all
    start
    ;;
  deps)
    command -v apt-get >/dev/null 2>&1 || die "这个命令只支持 Ubuntu / Debian，别的系统请自己装 Python 3.11+、gcc、Node 20+"
    sudo apt-get update
    sudo apt-get install -y git curl gcc python3 python3-venv python3-pip
    if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
      sudo apt-get install -y nodejs
    fi
    PY="$(find_python)"
    say "好了，接着运行 ./run.sh"
    ;;
  service)
    setup_all
    unit=/etc/systemd/system/agent-c.service
    sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=程序设计智能体（网页和 XDOJ 同步）
After=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$ROOT
Environment=PORT=$PORT HOST=$HOST
ExecStart=$ROOT/run.sh start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now agent-c
    say "已经装成系统服务，开机会自己启动。看状态：systemctl status agent-c；看日志：journalctl -u agent-c -f"
    ;;
  *)
    sed -n '2,12p' "$0"
    ;;
esac
