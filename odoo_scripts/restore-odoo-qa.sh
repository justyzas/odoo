#!/usr/bin/env bash

set -Eeuo pipefail

# Production DB pavadinimas. Naudojamas tik informaciniams komentarams.
SOURCE_DATABASE_NAME="odoo_armetlina"

# QA DB, kuri bus ištrinta ir atkurta iš production dump.
QA_DATABASE_NAME="odoo_armetlina_qa"

# PostgreSQL rolė, kurią testinis Odoo naudoja jungdamasis prie QA DB.
#
# Tai yra PostgreSQL rolė, o ne Linux vartotojas.
QA_DATABASE_OWNER="odoo_qa"

# Testinės Odoo aplinkos adresas.
QA_BASE_URL="https://odoo-qa.vibelink.lt"

# Dump katalogas.
BACKUP_ROOT="/odoo-dumps"

# Be argumento naudojama šiandienos data:
#
#   ./restore-odoo-qa.sh
#
# Galima nurodyti konkrečią datą:
#
#   ./restore-odoo-qa.sh 2026-07-16
BACKUP_DATE="${1:-$(date +%Y-%m-%d)}"

BACKUP_DIR="${BACKUP_ROOT}/${BACKUP_DATE}"
DUMP_FILE="${BACKUP_DIR}/dump.sql"

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
# PRADINIAI PATIKRINIMAI
###############################################################################

# Skriptą reikia paleisti kaip root, nes PostgreSQL komandos vykdomos
# persijungiant į Linux vartotoją postgres.
if [[ "${EUID}" -ne 0 ]]; then
    fail "Šį skriptą reikia paleisti kaip root."
fi

# Patikriname, ar production serveryje egzistuoja Linux vartotojas postgres.
if ! id postgres >/dev/null 2>&1; then
    fail "Šiame serveryje nėra Linux vartotojo 'postgres'."
fi

# Patikriname, ar įdiegtos visos reikalingos PostgreSQL komandos.
for command_name in psql dropdb createdb; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        fail "Nerasta komanda: ${command_name}"
    fi
done

# Patikriname, ar dump failas egzistuoja ir nėra tuščias.
if [[ ! -s "${DUMP_FILE}" ]]; then
    fail "Dump failas nerastas arba tuščias: ${DUMP_FILE}"
fi

# Patikriname, ar egzistuoja PostgreSQL rolė, kuri bus QA DB savininkė.
QA_ROLE_EXISTS="$(
    runuser -u postgres -- psql \
        --dbname=postgres \
        --tuples-only \
        --no-align \
        --command="
            SELECT EXISTS (
                SELECT 1
                FROM pg_roles
                WHERE rolname = '${QA_DATABASE_OWNER}'
            );
        "
)"

if [[ "${QA_ROLE_EXISTS}" != "t" ]]; then
    fail "PostgreSQL rolė '${QA_DATABASE_OWNER}' neegzistuoja."
fi

log "Bus atkurta QA DB iš: ${DUMP_FILE}"
log "Production DB šaltinis: ${SOURCE_DATABASE_NAME}"
log "Tikslinė QA DB: ${QA_DATABASE_NAME}"
log "QA PostgreSQL rolė: ${QA_DATABASE_OWNER}"

###############################################################################
# 1. NUTRAUKIAME AKTYVIAS JUNGTIS PRIE QA DB
###############################################################################

# PostgreSQL neleidžia ištrinti duomenų bazės, jei prie jos yra prisijungusių
# klientų.
#
# Prisijungęs gali būti:
#   - testinės aplinkos Odoo procesas;
#   - atidaryta psql sesija;
#   - administravimo programa;
#   - kitas procesas.
#
# Visos jungtys prie QA DB nutraukiamos priverstinai.
#
# Svarbu: testinės Odoo servisas turėtų būti sustabdytas, kad po jungties
# nutraukimo iš karto neprisijungtų dar kartą.
log "Nutraukiamos aktyvios jungtys prie ${QA_DATABASE_NAME}..."

runuser -u postgres -- psql \
    --dbname=postgres \
    --set=ON_ERROR_STOP=1 \
    --command="
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = '${QA_DATABASE_NAME}'
          AND pid <> pg_backend_pid();
    "

###############################################################################
# 2. IŠTRINAME SENĄ QA DB
###############################################################################

# Trinama tik QA duomenų bazė.
#
# Production DB odoo_armetlina neliečiama.
log "Trinama sena QA duomenų bazė ${QA_DATABASE_NAME}..."

runuser -u postgres -- dropdb \
    --if-exists \
    "${QA_DATABASE_NAME}"

###############################################################################
# 3. SUKURIAME NAUJĄ TUŠČIĄ QA DB
###############################################################################

# QA DB sukuriama iš švaraus template0 šablono.
#
# DB savininke nustatoma PostgreSQL rolė odoo_qa. Šią rolę testinis Odoo
# naudoja jungdamasis prie PostgreSQL per VPN.
log "Kuriama nauja QA duomenų bazė ${QA_DATABASE_NAME}..."

runuser -u postgres -- createdb \
    --owner="${QA_DATABASE_OWNER}" \
    --encoding="UTF8" \
    --template="template0" \
    "${QA_DATABASE_NAME}"

###############################################################################
# 4. IMPORTUOJAME PRODUCTION DUMP
###############################################################################

# Production serveryje gali nebūti Linux vartotojo odoo_qa. Tai nėra problema,
# nes odoo_qa yra PostgreSQL rolė, o ne būtinai Linux vartotojas.
#
# psql procesą paleidžiame kaip Linux vartotoją postgres.
#
# Pirmosios PostgreSQL sesijos komandos metu vykdome:
#
#   SET ROLE odoo_qa;
#
# Po to visos dump esančios CREATE TABLE, CREATE SEQUENCE ir kitos komandos
# vykdomos PostgreSQL rolės odoo_qa vardu.
#
# Kadangi production dump sukurtas su:
#
#   --no-owner
#   --no-acl
#
# dump neturėtų bandyti atkurti production savininkų ar production teisių.
log "Importuojamas dump į ${QA_DATABASE_NAME}..."

{
    printf 'SET ROLE %s;\n' "${QA_DATABASE_OWNER}"
    cat "${DUMP_FILE}"
} | runuser -u postgres -- psql \
    --dbname="${QA_DATABASE_NAME}" \
    --set=ON_ERROR_STOP=1

###############################################################################
# 5. NEUTRALIZUOJAME PRODUCTION DB KOPIJĄ
###############################################################################

# Atkūrus production dump, QA DB vis dar turi production nustatymus.
#
# Joje gali būti:
#   - production web.base.url;
#   - aktyvios suplanuotos cron užduotys;
#   - aktyvūs SMTP serveriai;
#   - production eilėje laukiantys laiškai;
#   - aktyvūs incoming mail serveriai;
#   - Google Calendar, Outlook ar custom integracijų nustatymai.
#
# Toliau atliekame pagrindinį neutralizavimą prieš paleidžiant testinį Odoo.
log "Neutralizuojama QA duomenų bazė..."

runuser -u postgres -- psql \
    --dbname="${QA_DATABASE_NAME}" \
    --set=ON_ERROR_STOP=1 <<SQL
BEGIN;

-------------------------------------------------------------------------------
-- 5.1. PAKEIČIAME PAGRINDINĮ ODOO URL
-------------------------------------------------------------------------------

-- Production DB kopijoje web.base.url gali būti:
--
--   https://odoo.armetlina.lt
--
-- QA aplinkoje jis turi būti:
--
--   ${QA_BASE_URL}
--
-- Pirmiausia atnaujiname esamą parametrą, jei jis jau egzistuoja.
UPDATE ir_config_parameter
SET value = '${QA_BASE_URL}'
WHERE key = 'web.base.url';

-- Jei parametro nėra, sukuriame jį.
INSERT INTO ir_config_parameter (key, value)
SELECT 'web.base.url', '${QA_BASE_URL}'
WHERE NOT EXISTS (
    SELECT 1
    FROM ir_config_parameter
    WHERE key = 'web.base.url'
);

-------------------------------------------------------------------------------
-- 5.2. UŽŠALDOME WEB.BASE.URL
-------------------------------------------------------------------------------

-- Odoo tam tikrais atvejais gali automatiškai pakeisti web.base.url pagal
-- adresą, kuriuo prisijungė administratorius.
--
-- web.base.url.freeze=True neleidžia Odoo automatiškai perrašyti QA URL.
UPDATE ir_config_parameter
SET value = 'True'
WHERE key = 'web.base.url.freeze';

INSERT INTO ir_config_parameter (key, value)
SELECT 'web.base.url.freeze', 'True'
WHERE NOT EXISTS (
    SELECT 1
    FROM ir_config_parameter
    WHERE key = 'web.base.url.freeze'
);

-------------------------------------------------------------------------------
-- 5.3. IŠJUNGIAME VISAS SUPLANUOTAS CRON UŽDUOTIS
-------------------------------------------------------------------------------

-- Production DB gali turėti aktyvias suplanuotas užduotis, kurios:
--
--   - siunčia laiškus;
--   - sinchronizuoja Google ar Outlook kalendorius;
--   - vykdo bankų sinchronizaciją;
--   - paleidžia custom integracijas;
--   - generuoja dokumentus;
--   - vykdo prenumeratų ar sąskaitų veiksmus;
--   - kviečia išorinius API ar webhookus.
--
-- Išjungiame visas cron užduotis. Vėliau QA aplinkoje bus galima rankiniu
-- būdu įjungti tik tas užduotis, kurios tikrai reikalingos testavimui.
UPDATE ir_cron
SET active = FALSE
WHERE active = TRUE;

-------------------------------------------------------------------------------
-- 5.4. IŠJUNGIAME VISUS SIUNČIAMO PAŠTO SERVERIUS
-------------------------------------------------------------------------------

-- Production DB kopijoje lieka SMTP serverių nustatymai, prisijungimai ir
-- galimi slaptažodžiai.
--
-- Išjungus ir_mail_server įrašus, QA sistema negalės naudoti production SMTP
-- serverių įprastam Odoo laiškų siuntimui.
UPDATE ir_mail_server
SET active = FALSE
WHERE active = TRUE;

-------------------------------------------------------------------------------
-- 5.5. ATŠAUKIAME PRODUCTION EILĖJE LAUKIANČIUS LAIŠKUS
-------------------------------------------------------------------------------

-- Production DB dump gali turėti jau sugeneruotų, bet dar neišsiųstų laiškų.
--
-- Būsena outgoing reiškia, kad laiškas laukia siuntimo.
-- Būsena exception reiškia, kad ankstesnis siuntimas nepavyko ir Odoo gali
-- bandyti jį siųsti dar kartą.
--
-- Abu variantus pažymime kaip cancel, kad QA aplinka jų neišsiųstų.
UPDATE mail_mail
SET state = 'cancel'
WHERE state IN ('outgoing', 'exception');

COMMIT;
SQL

###############################################################################
# 6. IŠJUNGIAME INCOMING MAIL SERVERIUS, JEI MODULIS ĮDIEGTAS
###############################################################################

# Incoming mail serverių lentelė fetchmail_server egzistuoja tik tada, kai
# atitinkamas Odoo modulis yra įdiegtas.
#
# Pirmiausia patikriname lentelės egzistavimą, kad skriptas nesustotų ten,
# kur incoming mail funkcionalumas nenaudojamas.
FETCHMAIL_TABLE_EXISTS="$(
    runuser -u postgres -- psql \
        --dbname="${QA_DATABASE_NAME}" \
        --tuples-only \
        --no-align \
        --set=ON_ERROR_STOP=1 \
        --command="
            SELECT to_regclass('public.fetchmail_server') IS NOT NULL;
        "
)"

if [[ "${FETCHMAIL_TABLE_EXISTS}" == "t" ]]; then
    log "Išjungiami gaunamo pašto serveriai..."

    runuser -u postgres -- psql \
        --dbname="${QA_DATABASE_NAME}" \
        --set=ON_ERROR_STOP=1 \
        --command="
            UPDATE fetchmail_server
            SET active = FALSE
            WHERE active = TRUE;
        "
else
    log "fetchmail_server lentelės nėra – incoming mail žingsnis praleidžiamas."
fi

###############################################################################
# 7. PATIKRINAME OBJEKTŲ SAVININKUS
###############################################################################

# Patikriname, kam priklauso atkurtos public schemos lentelės.
#
# Tikėtinas savininkas yra odoo_qa.
log "Tikrinami QA DB objektų savininkai..."

runuser -u postgres -- psql \
    --dbname="${QA_DATABASE_NAME}" \
    --set=ON_ERROR_STOP=1 \
    --command="
        SELECT
            tableowner,
            count(*) AS table_count
        FROM pg_tables
        WHERE schemaname = 'public'
        GROUP BY tableowner
        ORDER BY tableowner;
    "

###############################################################################
# 8. PATIKRINAME GALUTINĘ NEUTRALIZAVIMO BŪSENĄ
###############################################################################

log "Tikrinama galutinė QA duomenų bazės būsena..."

runuser -u postgres -- psql \
    --dbname="${QA_DATABASE_NAME}" \
    --set=ON_ERROR_STOP=1 \
    --command="
        SELECT
            key,
            value
        FROM ir_config_parameter
        WHERE key IN (
            'web.base.url',
            'web.base.url.freeze'
        )
        ORDER BY key;

        SELECT
            count(*) AS active_cron_jobs
        FROM ir_cron
        WHERE active = TRUE;

        SELECT
            count(*) AS active_outgoing_mail_servers
        FROM ir_mail_server
        WHERE active = TRUE;

        SELECT
            count(*) AS pending_outgoing_emails
        FROM mail_mail
        WHERE state IN ('outgoing', 'exception');
    "

###############################################################################
# 9. PATIKRINAME QA DB SAVININKĄ
###############################################################################

log "Tikrinamas QA DB savininkas..."

runuser -u postgres -- psql \
    --dbname=postgres \
    --set=ON_ERROR_STOP=1 \
    --command="
        SELECT
            datname,
            pg_get_userbyid(datdba) AS database_owner
        FROM pg_database
        WHERE datname = '${QA_DATABASE_NAME}';
    "

log "QA duomenų bazė sėkmingai atkurta ir neutralizuota."
log "Šaltinio dump: ${DUMP_FILE}"
log "QA DB: ${QA_DATABASE_NAME}"
log "QA DB rolė: ${QA_DATABASE_OWNER}"
log "QA URL: ${QA_BASE_URL}"
log "Prieš paleidžiant testinį Odoo papildomai patikrink custom integracijas ir OAuth sinchronizacijas."