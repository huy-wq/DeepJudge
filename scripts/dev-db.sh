#!/usr/bin/env bash
# 本地数据库的一键操作：起库 → 等就绪 → 跑迁移。
#
#   ./scripts/dev-db.sh up      起库并升级到最新表结构（幂等，可重复执行）
#   ./scripts/dev-db.sh reset   删库重来（连数据一起，别在有用的库上跑）
#   ./scripts/dev-db.sh down    停库，保留数据
#   ./scripts/dev-db.sh shell   进 MySQL 命令行
#
# 口令等默认值都在 docker-compose.yml 里，需要改就设环境变量，不要把口令写进脚本。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_URL="mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3307/deepjudge?charset=utf8mb4"
DATABASE_URL="${DATABASE_URL:-$DEFAULT_URL}"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

info() { printf '\033[36m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "找不到 docker。装 Docker，或手动准备一个 MySQL 8 并设 DATABASE_URL。"
  docker compose version >/dev/null 2>&1 || fail "docker compose 子命令不可用（需要 Docker Compose v2）。"
}

# 等 compose 的 healthcheck 变成 healthy，而不是自己另写一套探测：
# 两套探测迟早会不一致，而且 healthcheck 才看得到容器启动的真实阶段。
wait_ready() {
  info "等待 MySQL 通过健康检查……"
  local retries=60 status
  while (( retries > 0 )); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' \
      deepjudge-mysql 2>/dev/null || echo unknown)"
    case "$status" in
      healthy)
        info "MySQL 已就绪。"
        return 0
        ;;
      unhealthy)
        fail "MySQL 健康检查失败。看日志：docker compose logs mysql"
        ;;
    esac
    (( retries-- )) || true
    sleep 2
  done
  fail "MySQL 60 次探测仍未就绪（最后状态：$status）。看日志：docker compose logs mysql"
}

migrate() {
  [[ -x "$PYTHON" ]] || fail "找不到 $PYTHON。先建虚拟环境：python3 -m venv .venv && .venv/bin/pip install -e backend"
  info "执行迁移：$DATABASE_URL"
  (cd "$ROOT_DIR/backend" && DATABASE_URL="$DATABASE_URL" "$PYTHON" -m alembic upgrade head)
  info "表结构已是最新。"
}

case "${1:-up}" in
  up)
    require_docker
    (cd "$ROOT_DIR" && docker compose up -d mysql)
    wait_ready
    migrate
    ;;
  reset)
    require_docker
    (cd "$ROOT_DIR" && docker compose down -v)
    "$0" up
    ;;
  down)
    require_docker
    (cd "$ROOT_DIR" && docker compose down)
    ;;
  shell)
    require_docker
    # 口令从环境变量取，默认值与 docker-compose.yml 一致；不在脚本里再写一遍明文
    (cd "$ROOT_DIR" && docker compose exec mysql \
      mysql -u"${MYSQL_USER:-deepjudge}" -p"${MYSQL_PASSWORD:-deepjudge}" "${MYSQL_DATABASE:-deepjudge}")
    ;;
  *)
    fail "用法：$0 {up|reset|down|shell}"
    ;;
esac
