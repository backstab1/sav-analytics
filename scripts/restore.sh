#!/bin/sh
# Восстановление из резервной копии (P6, docs/operations.md).
#
#   restore.sh <файл .tar.gpg> [--into-empty]
#
# Без флага — пробное восстановление: дамп разворачивается во временную
# базу sav_restore_check, данные — во временный каталог, проверяются
# суммы, число проектов и что у каждого проекта есть исходник; затем
# временное удаляется. Так выполняется ежемесячная проверка RTO: время
# восстановления печатается в конце.
#
# С --into-empty — настоящее восстановление в пустую базу PGDATABASE и
# пустой каталог DATA_DIR (сначала остановите api и worker). Непустую
# базу или каталог скрипт не трогает.
set -eu

ARCHIVE=${1:?укажите файл копии}
MODE=${2:-check}
DATA_DIR=${DATA_DIR:-/data}
PASSPHRASE_FILE=${BACKUP_PASSPHRASE_FILE:?нужен BACKUP_PASSPHRASE_FILE}
STARTED=$(date +%s)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
    --decrypt "$ARCHIVE" | tar -C "$WORK" -xf -
CONTENT=$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n1)
(cd "$CONTENT" && sha256sum -c SHA256SUMS)

if [ "$MODE" = "--into-empty" ]; then
    TABLES=$(psql -tAc "select count(*) from information_schema.tables where table_schema='public'")
    [ "$TABLES" = "0" ] || { echo "База $PGDATABASE не пуста — восстановление отменено." >&2; exit 1; }
    [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ] || { echo "Каталог $DATA_DIR не пуст." >&2; exit 1; }
    pg_restore --no-owner --exit-on-error --dbname="$PGDATABASE" "$CONTENT/database.dump"
    tar -C "$DATA_DIR" -xf "$CONTENT/data.tar"
    TARGET_DB=$PGDATABASE
    TARGET_DATA=$DATA_DIR
else
    psql -qc "drop database if exists sav_restore_check" postgres
    psql -qc "create database sav_restore_check" postgres
    pg_restore --no-owner --exit-on-error --dbname=sav_restore_check "$CONTENT/database.dump"
    mkdir "$WORK/data"
    tar -C "$WORK/data" -xf "$CONTENT/data.tar"
    TARGET_DB=sav_restore_check
    TARGET_DATA=$WORK/data
fi

PROJECTS=$(psql -tAc "select count(*) from projects" "$TARGET_DB" | tr -d '\r')
MISSING=0
for id in $(psql -tAc "select id from projects" "$TARGET_DB" | tr -d '\r'); do
    [ -f "$TARGET_DATA/projects/$id/source.sav" ] || { echo "нет source.sav у $id" >&2; MISSING=$((MISSING + 1)); }
done
[ "$MODE" = "--into-empty" ] || psql -qc "drop database sav_restore_check" postgres

echo "Проектов: $PROJECTS, без исходника: $MISSING, время: $(( $(date +%s) - STARTED )) с"
[ "$MISSING" -eq 0 ]
