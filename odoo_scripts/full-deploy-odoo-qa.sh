#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="/odoo-dumps"

EXPORT_SCRIPT="${SCRIPT_DIR}/export-odoo-to-test.sh"
RESTORE_SCRIPT="${SCRIPT_DIR}/restore-odoo-qa.sh"
FILESTORE_SCRIPT="${SCRIPT_DIR}/deploy-odoo-qa-filestore.sh"

REMOTE_HOST="odoo-qa-test"
QA_SERVICE="odoo-qa"

# Visi proceso žingsniai naudos tą pačią datą.
BACKUP_DATE="$(date +%Y-%m-%d)"

log() {
    echo
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

fail() {
    echo "KLAIDA: $*" >&2
    exit 1
}

error_handler() {
    local exit_code=$?
    local failed_line="${BASH_LINENO[0]}"

    echo >&2
    echo "============================================================" >&2
    echo "KLAIDA: pilnas QA diegimas nepavyko." >&2
    echo "Skriptas sustojo ties eilute: ${failed_line}" >&2
    echo "Exit code: ${exit_code}" >&2
    echo "============================================================" >&2

    echo "Dabartinė ${QA_SERVICE} būsena testiniame serveryje:" >&2

    ssh "${REMOTE_HOST}" \
        "sudo -n systemctl status '${QA_SERVICE}' --no-pager" >&2 || true

    exit "${exit_code}"
}

trap error_handler ERR

###############################################################################
# PRADINIAI PATIKRINIMAI
###############################################################################

if [[ "${EUID}" -ne 0 ]]; then
    fail "Šį skriptą reikia paleisti kaip root production serveryje."
fi

for script_file in \
    "${EXPORT_SCRIPT}" \
    "${RESTORE_SCRIPT}" \
    "${FILESTORE_SCRIPT}"; do

    if [[ ! -f "${script_file}" ]]; then
        fail "Skriptas nerastas: ${script_file}"
    fi

    if [[ ! -x "${script_file}" ]]; then
        fail "Skriptas neturi vykdymo teisių: ${script_file}"
    fi

    if ! bash -n "${script_file}"; then
        fail "Skripto sintaksė netvarkinga: ${script_file}"
    fi
done

log "Tikrinamas SSH ryšys su testiniu serveriu ${REMOTE_HOST}..."

ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    "${REMOTE_HOST}" \
    "true"

log "Pradedamas pilnas Odoo QA atnaujinimas."
log "Naudojama kopijos data: ${BACKUP_DATE}"

###############################################################################
# 1. EXPORTUOJAME PRODUCTION DB IR FILESTORE
###############################################################################

log "1/5 – eksportuojama production DB ir filestore į testinį serverį..."

"${EXPORT_SCRIPT}"

log "Production DB ir filestore eksportuoti sėkmingai."

###############################################################################
# 2. SUSTABDOMAS TESTINĖS ODOO SERVISAS
###############################################################################

log "2/5 – stabdomas ${QA_SERVICE} servisas testiniame serveryje..."

ssh "${REMOTE_HOST}" "
    set -Eeuo pipefail

    sudo -n systemctl stop '${QA_SERVICE}'

    if sudo -n systemctl is-active --quiet '${QA_SERVICE}'; then
        echo 'KLAIDA: ${QA_SERVICE} serviso sustabdyti nepavyko.' >&2
        exit 1
    fi

    echo '${QA_SERVICE} servisas sustabdytas.'
"

###############################################################################
# 3. ATKURIAMA IR NEUTRALIZUOJAMA QA DB
###############################################################################

log "3/5 – atkuriama ir neutralizuojama QA duomenų bazė..."

"${RESTORE_SCRIPT}" "${BACKUP_DATE}"

log "QA duomenų bazė atkurta ir neutralizuota sėkmingai."

###############################################################################
# 4. ATNAUJINAMAS QA FILESTORE
###############################################################################

log "4/5 – diegiamas QA filestore..."

"${FILESTORE_SCRIPT}" "${BACKUP_DATE}"

log "QA filestore įdiegtas sėkmingai."

###############################################################################
# 5. GALUTINIS ODOO-QA SERVISO RESTART
###############################################################################

log "5/5 – atliekamas galutinis ${QA_SERVICE} serviso restart..."

ssh "${REMOTE_HOST}" "
    set -Eeuo pipefail

    sudo -n systemctl restart '${QA_SERVICE}'

    sleep 3

    if ! sudo -n systemctl is-active --quiet '${QA_SERVICE}'; then
        echo 'KLAIDA: ${QA_SERVICE} servisas po restart nepasileido.' >&2

        sudo -n systemctl status '${QA_SERVICE}' --no-pager || true
        sudo -n journalctl -u '${QA_SERVICE}' -n 100 --no-pager || true

        exit 1
    fi

    sudo -n systemctl status '${QA_SERVICE}' --no-pager
"

log "============================================================"
log "Pilnas Odoo QA diegimas baigtas sėkmingai."
log "Kopijos data: ${BACKUP_DATE}"
log "QA servisas: ${QA_SERVICE}"
log "Testinis serveris: ${REMOTE_HOST}"
log "============================================================"