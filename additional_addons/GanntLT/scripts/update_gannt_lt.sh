#!/usr/bin/env bash
# ==============================================================================
# GanntLT Git-Based Updater for Odoo 19
# ==============================================================================
# Updates GanntLT directly from GitHub (no .zip files needed), upgrades the
# module in Odoo, and optionally restarts the Odoo systemd service.
#
# Examples:
#   # 1. Update to latest main branch and upgrade DB 'testas':
#   ./scripts/update_gannt_lt.sh -d testas -c /etc/odoo/odoo.conf
#
#   # 2. Update only when a new v* release tag is published (ideal for cron):
#   ./scripts/update_gannt_lt.sh --latest-tag -d production_db -s odoo
#
#   # 3. Check if a newer release tag exists without making changes:
#   ./scripts/update_gannt_lt.sh --latest-tag --dry-run
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ODOO_BIN="${ODOO_BIN:-odoo}"
ODOO_CONF="${ODOO_CONF:-/etc/odoo/odoo.conf}"
DB_NAME="${DB_NAME:-}"
SERVICE_NAME="${SERVICE_NAME:-}"
BRANCH="main"
EXPLICIT_TAG=""
USE_LATEST_TAG=0
DRY_RUN=0
SKIP_UPGRADE=0
EXTRA_ODOO_ARGS=""

usage() {
  cat << 'EOF'
Usage: update_gannt_lt.sh [options]
  -d, --db <name>          Odoo database name to upgrade (-u GanntLT)
  -c, --conf <path>        Path to odoo.conf (default: /etc/odoo/odoo.conf)
  -s, --service <name>     Systemd service name to restart after update (optional)
  -b, --branch <branch>    Git branch to pull (default: main)
  -t, --tag <tag>          Checkout a specific Git tag (e.g., v3.0)
      --latest-tag         Checkout the newest v* release tag (no-op if already on it)
      --odoo-bin <path>    Path to odoo binary (default: odoo)
      --extra-args <args>  Extra flags passed to odoo upgrade (e.g., "--http-port=18099")
      --skip-upgrade       Pull Git changes only; skip running odoo -u GanntLT
      --dry-run            Fetch and report whether an update is available without changing files
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--db) DB_NAME="$2"; shift 2 ;;
    -c|--conf) ODOO_CONF="$2"; shift 2 ;;
    -s|--service) SERVICE_NAME="$2"; shift 2 ;;
    -b|--branch) BRANCH="$2"; shift 2 ;;
    -t|--tag) EXPLICIT_TAG="$2"; shift 2 ;;
    --latest-tag) USE_LATEST_TAG=1; shift ;;
    --odoo-bin) ODOO_BIN="$2"; shift 2 ;;
    --extra-args) EXTRA_ODOO_ARGS="$2"; shift 2 ;;
    --skip-upgrade) SKIP_UPGRADE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

cd "$REPO_DIR"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $REPO_DIR is not a Git repository. Clone GanntLT via git first." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: Local tracked modifications exist in $REPO_DIR. Resolve or stash them before updating." >&2
  exit 1
fi

echo "[GanntLT] Fetching updates from origin..."
git fetch --tags --prune origin

CURRENT_COMMIT="$(git rev-parse HEAD)"
UPDATED=0

if [[ -n "$EXPLICIT_TAG" ]]; then
  TARGET_COMMIT="$(git rev-parse "${EXPLICIT_TAG}^{commit}")"
  if [[ "$CURRENT_COMMIT" == "$TARGET_COMMIT" ]]; then
    echo "[GanntLT] Already at tag $EXPLICIT_TAG ($CURRENT_COMMIT)."
  else
    echo "[GanntLT] Update available: $CURRENT_COMMIT -> tag $EXPLICIT_TAG ($TARGET_COMMIT)"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      git checkout -q "$EXPLICIT_TAG"
      UPDATED=1
    fi
  fi
elif [[ "$USE_LATEST_TAG" -eq 1 ]]; then
  LATEST_TAG="$(git tag -l 'v*' --sort=-v:refname | head -n 1)"
  if [[ -z "$LATEST_TAG" ]]; then
    echo "ERROR: No v* release tags found in repository." >&2
    exit 1
  fi
  TARGET_COMMIT="$(git rev-parse "${LATEST_TAG}^{commit}")"
  if [[ "$CURRENT_COMMIT" == "$TARGET_COMMIT" ]]; then
    echo "[GanntLT] Already at latest release tag $LATEST_TAG ($CURRENT_COMMIT). Nothing to do."
  else
    echo "[GanntLT] New release tag found: $LATEST_TAG ($TARGET_COMMIT)"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      git checkout -q "$LATEST_TAG"
      UPDATED=1
    fi
  fi
else
  TARGET_COMMIT="$(git rev-parse "origin/${BRANCH}")"
  if [[ "$CURRENT_COMMIT" == "$TARGET_COMMIT" ]]; then
    echo "[GanntLT] Already up to date on origin/$BRANCH ($CURRENT_COMMIT)."
  else
    echo "[GanntLT] Update available on origin/$BRANCH: $CURRENT_COMMIT -> $TARGET_COMMIT"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      git checkout -q "$BRANCH"
      git pull --ff-only origin "$BRANCH"
      UPDATED=1
    fi
  fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[GanntLT] Dry run complete."
  exit 0
fi

if [[ "$UPDATED" -eq 0 ]]; then
  exit 0
fi

if [[ "$SKIP_UPGRADE" -eq 0 ]]; then
  if [[ -z "$DB_NAME" ]]; then
    echo "ERROR: --db <name> is required to run Odoo module upgrade (or pass --skip-upgrade)." >&2
    exit 1
  fi
  echo "[GanntLT] Upgrading GanntLT in Odoo database '$DB_NAME'..."
  # shellcheck disable=SC2086
  "$ODOO_BIN" -c "$ODOO_CONF" -d "$DB_NAME" -u GanntLT --stop-after-init --log-level=warn $EXTRA_ODOO_ARGS
fi

if [[ -n "$SERVICE_NAME" ]]; then
  echo "[GanntLT] Restarting service '$SERVICE_NAME'..."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl restart "$SERVICE_NAME"
  else
    service "$SERVICE_NAME" restart
  fi
fi

echo "[GanntLT] Update completed successfully (commit: $(git rev-parse --short HEAD))."
