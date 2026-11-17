\#!/usr/bin/env bash

set -Eeuo pipefail

DATABASE_NAME="odoo_armetlina"
QA_DATABASE_NAME="odoo_armetlina_qa"
BACKUP_ROOT="/odoo-dumps"
REMOTE_HOST="odoo-qa-test"
REMOTE_ROOT="/odoo_transfer"

BACKUP_DATE="$(date +%Y-%m-%d)"
BACKUP_DIR="${BACKUP_ROOT}/${BACKUP_DATE}"
DUMP_FILE="${BACKUP_DIR}/dump.sql"
ODOO_BACKUP_DIR="${BACKUP_DIR}/odoo"
FILESTORE_DIR="${ODOO_BACKUP_DIR}/filestore"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error_handler() {
    local exit_code=$?
    echo "KLAIDA: skriptas sustojo ties ${BASH_SOURCE[0]}:${BASH_LINENO[0]}." >&2
    exit "${exit_code}"
}

trap error_handler ERR

if [[ "${EUID}" -ne 0 ]]; then
    echo "Šį skriptą reikia paleisti kaip root." >&2
    exit 1
fi

if ! id postgres >/dev/null 2>&1; then
    echo "KLAIDA: šiame serveryje nėra postgres vartotojo." >&2
    echo "Tikėtina, kad skriptas paleistas ne production serveryje." >&2
    exit 1
fi

if [[ -e "${BACKUP_DIR}" ]]; then
    echo "Šios dienos backup katalogas jau egzistuoja: ${BACKUP_DIR}" >&2
    echo "Pašalink jį arba pakeisk katalogo pavadinimą prieš paleisdamas dar kartą." >&2
    exit 1
fi

log "Kuriamas backup katalogas: ${BACKUP_DIR}"
mkdir -p "${BACKUP_DIR}"

log "Daromas PostgreSQL duomenų bazės ${DATABASE_NAME} dump..."
runuser -u postgres -- pg_dump \
    --format=plain \
    --no-owner \
    --no-acl \
    "${DATABASE_NAME}" > "${DUMP_FILE}"

if [[ ! -s "${DUMP_FILE}" ]]; then
    echo "KLAIDA: DB dump failas tuščias: ${DUMP_FILE}" >&2
    exit 1
fi

log "Kopijuojamas /var/lib/odoo katalogas..."
cp -a /var/lib/odoo "${ODOO_BACKUP_DIR}"

log "Šalinami addons ir sessions katalogai iš kopijos..."
rm -rf \
    "${ODOO_BACKUP_DIR}/addons" \
    "${ODOO_BACKUP_DIR}/sessions"

if [[ ! -d "${FILESTORE_DIR}" ]]; then
    echo "KLAIDA: filestore katalogas nerastas: ${FILESTORE_DIR}" >&2
    exit 1
fi

if [[ ! -d "${FILESTORE_DIR}/${DATABASE_NAME}" ]]; then
    echo "KLAIDA: pagrindinės DB filestore nerastas:" >&2
    echo "${FILESTORE_DIR}/${DATABASE_NAME}" >&2
    exit 1
fi

log "Paliekamas tik ${DATABASE_NAME} filestore katalogas..."

find "${FILESTORE_DIR}" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    ! -name "${DATABASE_NAME}" \
    -print \
    -exec rm -rf -- {} +

log "Pervadinamas '${DATABASE_NAME}' filestore į '${QA_DATABASE_NAME}'..."
mv "${FILESTORE_DIR}/${DATABASE_NAME}" "${FILESTORE_DIR}/${QA_DATABASE_NAME}"

log "Likę filestore katalogai:"
find "${FILESTORE_DIR}" \
    -mindepth 1 \
    -maxdepth 1 \
    -type d \
    -printf '%f\n'

log "Patikrinamas SSH ryšys su ${REMOTE_HOST}..."
ssh -o BatchMode=yes "${REMOTE_HOST}" "true"

log "Kuriamas nuotolinis katalogas ${REMOTE_ROOT}..."
ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_ROOT}'"

if ssh "${REMOTE_HOST}" "test -e '${REMOTE_ROOT}/${BACKUP_DATE}'"; then
    echo "KLAIDA: nuotolinis katalogas jau egzistuoja:" >&2
    echo "${REMOTE_HOST}:${REMOTE_ROOT}/${BACKUP_DATE}" >&2
    exit 1
fi

log "Perkeliamas backup į ${REMOTE_HOST}:${REMOTE_ROOT}/${BACKUP_DATE}..."
scp -r \
    "${BACKUP_DIR}" \
    "${REMOTE_HOST}:${REMOTE_ROOT}/"

log "Tikrinamas nuotolinis backup..."
ssh "${REMOTE_HOST}" \
    "test -s '${REMOTE_ROOT}/${BACKUP_DATE}/dump.sql' &&
     test -d '${REMOTE_ROOT}/${BACKUP_DATE}/odoo/filestore/${QA_DATABASE_NAME}' &&
     test ! -e '${REMOTE_ROOT}/${BACKUP_DATE}/odoo/filestore/${DATABASE_NAME}' &&
     test ! -e '${REMOTE_ROOT}/${BACKUP_DATE}/odoo/addons' &&
     test ! -e '${REMOTE_ROOT}/${BACKUP_DATE}/odoo/sessions'"

log "Perkėlimas baigtas sėkmingai."
log "Lokali kopija: ${BACKUP_DIR}"
log "Testinė kopija: ${REMOTE_HOST}:${REMOTE_ROOT}/${BACKUP_DATE}