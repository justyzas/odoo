#!/usr/bin/env bash

set -Eeuo pipefail

REMOTE_HOST="odoo-qa-test"

QA_SERVICE="odoo-qa"
QA_USER="odoo_qa"
QA_GROUP="odoo_qa"

QA_DATABASE_NAME="odoo_armetlina_qa"

TRANSFER_ROOT="/odoo_transfer"
QA_DATA_DIR="/var/lib/odoo-qa"

# Be argumento naudojama šiandienos data:
#
#   ./deploy-odoo-qa-filestore.sh
#
# Galima perduoti konkrečią datą:
#
#   ./deploy-odoo-qa-filestore.sh 2026-07-16
BACKUP_DATE="${1:-$(date +%Y-%m-%d)}"

SOURCE_FILESTORE="${TRANSFER_ROOT}/${BACKUP_DATE}/odoo/filestore/${QA_DATABASE_NAME}"
TARGET_FILESTORE="${QA_DATA_DIR}/filestore/${QA_DATABASE_NAME}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

fail() {
    echo "KLAIDA: $*" >&2
    exit 1
}

error_handler() {
    local exit_code=$?

    echo "KLAIDA: skriptas sustojo ties ${BASH_SOURCE[0]}:${BASH_LINENO[0]}." >&2
    exit "${exit_code}"
}

trap error_handler ERR

###############################################################################
# PRADINIAI PATIKRINIMAI PRODUCTION SERVERYJE
###############################################################################

if [[ "${EUID}" -ne 0 ]]; then
    fail "Šį skriptą reikia paleisti kaip root production serveryje."
fi

log "Tikrinamas SSH ryšys su ${REMOTE_HOST}..."

ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    "${REMOTE_HOST}" \
    "true"

log "Tikrinamas nuotolinis vartotojas..."

REMOTE_USER="$(
    ssh "${REMOTE_HOST}" "whoami"
)"

if [[ "${REMOTE_USER}" != "${QA_USER}" ]]; then
    fail "SSH alias jungiasi kaip '${REMOTE_USER}', tikėtasi '${QA_USER}'."
fi

log "Tikrinamos sudo teisės testiniame serveryje..."

# systemctl status grąžina 3, kai servisas sustabdytas (pvz. full-deploy jį
# jau sustabdė) – tai nėra klaida, sudo teisės vis tiek patikrintos.
ssh "${REMOTE_HOST}" "
    sudo -n systemctl status '${QA_SERVICE}' --no-pager >/dev/null || [ \$? -eq 3 ]
"

log "Tikrinamas šaltinio filestore katalogas..."

ssh "${REMOTE_HOST}" "
    test -d '${SOURCE_FILESTORE}'
"

log "Šaltinio filestore rastas:"
log "${REMOTE_HOST}:${SOURCE_FILESTORE}"

###############################################################################
# TESTINĖS ODOO SUSTABDYMAS IR FILESTORE ATNAUJINIMAS
###############################################################################

ssh "${REMOTE_HOST}" bash -s -- \
    "${QA_SERVICE}" \
    "${QA_USER}" \
    "${QA_GROUP}" \
    "${SOURCE_FILESTORE}" \
    "${TARGET_FILESTORE}" <<'REMOTE_SCRIPT'

set -Eeuo pipefail

QA_SERVICE="$1"
QA_USER="$2"
QA_GROUP="$3"
SOURCE_FILESTORE="$4"
TARGET_FILESTORE="$5"

SERVICE_STOPPED=false

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [odoo-test] $*"
}

error_handler() {
    local exit_code=$?

    echo "KLAIDA: nuotolinis skriptas sustojo ties eilute ${BASH_LINENO[0]}." >&2

    # Jeigu servisas jau buvo sustabdytas, bandome jį paleisti,
    # kad QA aplinka neliktų išjungta po nesėkmingo failų kopijavimo.
    if [[ "${SERVICE_STOPPED}" == "true" ]]; then
        echo "Bandoma vėl paleisti ${QA_SERVICE} servisą..." >&2
        sudo -n systemctl start "${QA_SERVICE}" >/dev/null 2>&1 || true
    fi

    exit "${exit_code}"
}

trap error_handler ERR

###############################################################################
# NUOTOLINIAI PATIKRINIMAI
###############################################################################

if [[ ! -d "${SOURCE_FILESTORE}" ]]; then
    echo "KLAIDA: šaltinio filestore katalogas nerastas:" >&2
    echo "${SOURCE_FILESTORE}" >&2
    exit 1
fi

if ! id "${QA_USER}" >/dev/null 2>&1; then
    echo "KLAIDA: Linux vartotojas '${QA_USER}' neegzistuoja." >&2
    exit 1
fi

# Patikriname visas būtinas sudo teises dar prieš sustabdant servisą.
sudo -n systemctl status "${QA_SERVICE}" --no-pager >/dev/null || [[ $? -eq 3 ]]
sudo -n systemctl stop "${QA_SERVICE}" --dry-run >/dev/null 2>&1 || true

###############################################################################
# 1. SUSTABDOMAS ODOO-QA SERVISAS
###############################################################################

log "Stabdomas ${QA_SERVICE} servisas..."

sudo -n systemctl stop "${QA_SERVICE}"
SERVICE_STOPPED=true

log "Tikrinama, ar ${QA_SERVICE} tikrai sustabdytas..."

if sudo -n systemctl is-active --quiet "${QA_SERVICE}"; then
    echo "KLAIDA: serviso ${QA_SERVICE} sustabdyti nepavyko." >&2
    exit 1
fi

###############################################################################
# 2. PAŠALINAMAS SENAS QA FILESTORE
###############################################################################

log "Šalinamas senas QA filestore..."

sudo -n rm -rf -- "${TARGET_FILESTORE}"

###############################################################################
# 3. PARUOŠIAMAS TIKSLINIS KATALOGAS
###############################################################################

log "Sukuriamas tikslinio filestore tėvinis katalogas..."

sudo -n mkdir -p "$(dirname "${TARGET_FILESTORE}")"

###############################################################################
# 4. NUKOPIJUOJAMAS NAUJAS QA FILESTORE
###############################################################################

log "Kopijuojamas naujas QA filestore..."

sudo -n cp -a \
    "${SOURCE_FILESTORE}" \
    "${TARGET_FILESTORE}"

###############################################################################
# 5. SUTVARKOMAS SAVININKAS
###############################################################################

log "Priskiriamas savininkas ${QA_USER}:${QA_GROUP}..."

sudo -n chown -R \
    "${QA_USER}:${QA_GROUP}" \
    "${TARGET_FILESTORE}"

###############################################################################
# 6. PATIKRINAMAS FILESTORE
###############################################################################

log "Tikrinamas nukopijuotas filestore..."

if [[ ! -d "${TARGET_FILESTORE}" ]]; then
    echo "KLAIDA: tikslinis filestore katalogas nesukurtas:" >&2
    echo "${TARGET_FILESTORE}" >&2
    exit 1
fi

if find "${TARGET_FILESTORE}" \
    \( ! -user "${QA_USER}" -o ! -group "${QA_GROUP}" \) \
    -print -quit |
    grep -q .; then

    echo "KLAIDA: dalis filestore failų nepriklauso ${QA_USER}:${QA_GROUP}." >&2
    exit 1
fi

log "Filestore katalogas ir ownership patikrinti."

###############################################################################
# 7. PALEIDŽIAMAS ODOO-QA SERVISAS
###############################################################################

log "Paleidžiamas ${QA_SERVICE} servisas..."

sudo -n systemctl start "${QA_SERVICE}"
SERVICE_STOPPED=false

###############################################################################
# 8. PATIKRINAMA SERVISO BŪSENA
###############################################################################

log "Tikrinama serviso būsena..."

sleep 3

if ! sudo -n systemctl is-active --quiet "${QA_SERVICE}"; then
    echo "KLAIDA: servisas ${QA_SERVICE} nepasileido." >&2

    sudo -n systemctl status "${QA_SERVICE}" --no-pager || true
    sudo -n journalctl -u "${QA_SERVICE}" -n 100 --no-pager || true

    exit 1
fi

log "Servisas paleistas sėkmingai."

sudo -n systemctl status "${QA_SERVICE}" --no-pager

REMOTE_SCRIPT

log "QA filestore sėkmingai atnaujintas."
log "Naudota kopijos data: ${BACKUP_DATE}"
log "Šaltinis: ${REMOTE_HOST}:${SOURCE_FILESTORE}"
log "Tikslas: ${REMOTE_HOST}:${TARGET_FILESTORE}"