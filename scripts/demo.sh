#!/usr/bin/env bash
# 答辩演示的一键准备：起库、重建、写入离线演示数据。
#
#   ./scripts/demo.sh reset   删库重来 + 迁移 + 写入两份演示结果（会清空本地库）
#   ./scripts/demo.sh seed    只写入演示结果（库必须为空）
#   ./scripts/demo.sh up      起库并迁移，不写数据
#
# 演示步骤见 docs/demo.md。数据库口令等默认值沿用 docker-compose.yml 与 .env.example。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_URL="mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3307/deepjudge?charset=utf8mb4"
# 与后端一致：没显式给就用 .env 里的 DATABASE_URL，再退回本地默认值
if [[ -z "${DATABASE_URL:-}" && -f "$ROOT_DIR/.env" ]]; then
    DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' "$ROOT_DIR/.env" | tail -n 1)"
fi
DATABASE_URL="${DATABASE_URL:-$DEFAULT_URL}"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

info() { printf '\033[36m%s\033[0m\n' "$*"; }
fail() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

seed() {
    [[ -x "$PYTHON" ]] || fail "找不到 $PYTHON。先建虚拟环境：python3 -m venv .venv && .venv/bin/pip install -e backend"
    "$PYTHON" "$ROOT_DIR/backend/scripts/seed_demo.py" --database-url "$DATABASE_URL"
    info "演示数据已就绪。下一步：启动后端与前端，按 docs/demo.md 走。"
}

case "${1:-help}" in
    up)
        DATABASE_URL="$DATABASE_URL" "$ROOT_DIR/scripts/dev-db.sh" up
        info "库已就绪；要写入演示数据，跑 ./scripts/demo.sh seed"
        ;;
    seed)
        seed
        ;;
    reset)
        info "会清空 $DATABASE_URL 指向的本地库，仅用于演示环境。"
        DATABASE_URL="$DATABASE_URL" "$ROOT_DIR/scripts/dev-db.sh" reset
        seed
        ;;
    help|*)
        cat <<'TXT'
用法：./scripts/demo.sh {up|seed|reset}

  up      起 MySQL 并升级表结构（幂等）
  seed    写入两份离线演示结果（复核队列各一份 human/auto）
  reset   删库重来 + 迁移 + seed（会清空本地库）

演示步骤与降级路径见 docs/demo.md。
TXT
        ;;
esac
