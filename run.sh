#!/usr/bin/env bash
# 一条命令装好并在后台启动，终端马上还给你。第一次运行会自动：生成 .env 和密钥、问一次 DeepSeek 的 key、
# 建 Python 虚拟环境装依赖、建表；之后再运行只做有变化的那几步。前端已经构建好放在仓库里，服务器上不用装 Node。
#
#   ./run.sh            在后台启动（网页 + 同步）
#   ./run.sh stop       停止
#   ./run.sh restart    重启
#   ./run.sh status     看在不在跑
#   ./run.sh log        看运行日志（Ctrl+C 只是退出看日志，程序继续跑）
#   ./run.sh update     拉最新代码，更新依赖，重启
#   ./run.sh demo       用演示数据在后台启动（单独一个库，不碰真实数据）
#   ./run.sh fg         在前台运行（调试用，Ctrl+C 停止）
#   ./run.sh deps       在 Ubuntu / Debian 上装 Python 和 gcc（要 sudo，只需一次）
#   ./run.sh service    装成开机自启的系统服务（要 sudo）
#
# 端口默认 8000，可以 PORT=8080 ./run.sh 改。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
PIDFILE="$ROOT/data/server.pid"
LOGFILE="$ROOT/data/server.log"

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
  [ -f frontend/dist/index.html ] && return 0   # 仓库里带着构建好的前端
  command -v node >/dev/null 2>&1 || die "缺少 frontend/dist（构建好的前端），也没有 Node。先 git pull 拿到构建好的前端"
  say "构建前端"
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent >/dev/null)
}

setup_all() {
  PY="$(find_python)"
  setup_env
  setup_backend
  setup_frontend
}

# 以前按旧步骤把演示数据灌进了正式的库：挪开（不删），让正式的库只有真实的 XDOJ 账号
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

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

# 装成了系统服务（./run.sh service）：停止、重启、更新、看日志都交给 systemd，不再自己起一份抢端口
UNIT=agent-c
service_on() {
  command -v systemctl >/dev/null 2>&1 && [ -f "/etc/systemd/system/$UNIT.service" ] \
    && systemctl is-enabled --quiet "$UNIT" 2>/dev/null
}

# 日志超过 20 MB 就换一个新文件，旧的留一份 server.log.1
LOG_MAX=$((20 * 1024 * 1024))
rotate_log() {
  [ -f "$LOGFILE" ] || return 0
  if [ "$(stat -c %s "$LOGFILE" 2>/dev/null || echo 0)" -gt "$LOG_MAX" ]; then
    mv -f "$LOGFILE" "$LOGFILE.1"
    say "日志超过 20 MB，旧的挪到了 server.log.1"
  fi
}

url_hint() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "http://127.0.0.1:$PORT${ip:+ （别的电脑：http://$ip:$PORT）}"
}

prepare_db() {
  mkdir -p "$ROOT/data"
  (cd "$ROOT/backend" && .venv/bin/python -m app.cli init-db >/dev/null)
}

fg_start() {
  prepare_db
  cd "$ROOT/backend"
  say "在前台运行：$(url_hint)，Ctrl+C 停止"
  exec .venv/bin/python -m app.cli serve --host "$HOST" --port "$PORT"
}

bg_start() {
  if running; then
    say "已经在运行了：$(url_hint)"
    return 0
  fi
  prepare_db
  rotate_log
  cd "$ROOT/backend"
  nohup .venv/bin/python -m app.cli serve --host "$HOST" --port "$PORT" < /dev/null >> "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  disown 2>/dev/null || true
  cd "$ROOT"
  sleep 2
  if running; then
    say "已在后台启动：$(url_hint)"
    echo "    停止：./run.sh stop    看日志：./run.sh log    看状态：./run.sh status    更新：./run.sh update"
  else
    tail -n 20 "$LOGFILE" || true
    die "没有启动起来，上面是日志的最后几行"
  fi
}

bg_stop() {
  if running; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    for _ in 1 2 3 4 5; do running || break; sleep 1; done
    if running; then kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true; fi
    say "已停止"
  else
    say "本来就没有在运行"
  fi
  rm -f "$PIDFILE"
}

install_service() {
  local unit=/etc/systemd/system/agent-c.service
  {
    echo "[Unit]"
    echo "Description=程序设计智能体（网页和 XDOJ 同步）"
    echo "After=network-online.target"
    echo
    echo "[Service]"
    echo "User=$(id -un)"
    echo "WorkingDirectory=$ROOT"
    echo "Environment=PORT=$PORT HOST=$HOST"
    echo "ExecStart=$ROOT/run.sh fg"
    echo "Restart=always"
    echo "RestartSec=5"
    echo
    echo "[Install]"
    echo "WantedBy=multi-user.target"
  } | sudo tee "$unit" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now agent-c
  say "已经装成系统服务，开机会自己启动。看状态：systemctl status agent-c；看日志：journalctl -u agent-c -f"
}

case "${1:-}" in
  "")
    setup_all
    move_demo_out
    if service_on; then sudo systemctl start "$UNIT"; say "系统服务已启动：$(url_hint)"; else bg_start; fi
    ;;
  stop)
    if service_on; then sudo systemctl stop "$UNIT"; say "已停止（系统服务）"; else bg_stop; fi
    ;;
  restart)
    if service_on; then sudo systemctl restart "$UNIT"; say "已重启（系统服务）：$(url_hint)"; else bg_stop; bg_start; fi
    ;;
  status)
    if service_on; then
      if systemctl is-active --quiet "$UNIT"; then say "系统服务在运行：$(url_hint)"; else say "系统服务没有在运行。启动：./run.sh"; fi
    elif running; then say "在运行（进程 $(cat "$PIDFILE")）：$(url_hint)"; else say "没有在运行。启动：./run.sh"; fi
    ;;
  log)
    if service_on; then journalctl -u "$UNIT" -n 50 -f; else tail -n 50 -f "$LOGFILE"; fi
    ;;
  fg|start)  # start：给以前装的系统服务用
    fg_start
    ;;
  demo)
    setup_all
    bg_stop >/dev/null
    export DATABASE_URL="sqlite:///$ROOT/data/demo.sqlite3"
    if ! grep -q '^DEV_LOGIN_PASSWORD=..*' .env; then
      sed -i "s|^DEV_LOGIN_PASSWORD=.*|DEV_LOGIN_PASSWORD=demo|" .env
      say "演示口令设成了 demo（在 .env 的 DEV_LOGIN_PASSWORD）"
    fi
    mkdir -p data
    (cd backend && .venv/bin/python -m app.cli demo-seed >/dev/null)
    say "演示账号：学生 2500000001，老师 demo-teacher（同一个登录框）。密码都是 .env 里的 DEV_LOGIN_PASSWORD"
    bg_start
    ;;
  update)
    say "拉最新代码"
    git pull --ff-only
    setup_all
    if service_on; then sudo systemctl restart "$UNIT"; say "已更新并重启（系统服务）：$(url_hint)"; else bg_stop; bg_start; fi
    ;;
  deps)
    command -v apt-get >/dev/null 2>&1 || die "这个命令只支持 Ubuntu / Debian，别的系统请自己装 Python 3.11+ 和 gcc"
    sudo apt-get update
    sudo apt-get install -y git gcc python3 python3-venv python3-pip
    PY="$(find_python)"
    say "好了，接着运行 ./run.sh"
    ;;
  service)
    setup_all
    bg_stop >/dev/null   # 脚本自己在后台起的那份先停掉，免得和服务抢端口
    install_service
    ;;
  *)
    sed -n '2,16p' "$0"
    ;;
esac
