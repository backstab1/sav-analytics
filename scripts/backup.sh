#!/bin/sh
# Ежедневная резервная копия (P6, docs/operations.md).
#
# Одна копия — один файл sav-backup-<UTC-время>.tar.gpg: внутри дамп
# PostgreSQL (pg_dump -Fc) и архив тома данных (исходники SAV, волны,
# книги отчётов). Шифрование — gpg AES256 по парольной фразе из файла.
# Копия считается успешной только после проверки: архив расшифровывается,
# дамп читается pg_restore --list, архив тома — tar -t. Итог пишется
# строкой в backups/backup.log. Копии старше BACKUP_RETENTION_DAYS удаляются.
#
# Переменные: PGHOST PGUSER PGDATABASE PGPASSWORD — база;
# BACKUP_PASSPHRASE_FILE — файл с парольной фразой; DATA_DIR (/data);
# BACKUP_DIR (/backups); BACKUP_RETENTION_DAYS (14).
#
# Копия на этом же сервере — ещё не резервная копия: каталог BACKUP_DIR
# должен уезжать за пределы сервера (rclone, rsync на другой хост, S3) —
# см. docs/operations.md, «Резервное копирование».
set -eu

DATA_DIR=${DATA_DIR:-/data}
BACKUP_DIR=${BACKUP_DIR:-/backups}
RETENTION=${BACKUP_RETENTION_DAYS:-14}
PASSPHRASE_FILE=${BACKUP_PASSPHRASE_FILE:?нужен BACKUP_PASSPHRASE_FILE}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="sav-backup-$STAMP"
WORK=$(mktemp -d)
LOG="$BACKUP_DIR/backup.log"
trap 'rm -rf "$WORK"' EXIT

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG"; }
fail() { log "FAILED $NAME: $*"; exit 1; }

[ -s "$PASSPHRASE_FILE" ] || fail "файл парольной фразы пуст или не найден"
mkdir -p "$BACKUP_DIR" "$WORK/$NAME"

pg_dump --format=custom --no-owner --file="$WORK/$NAME/database.dump" \
    || fail "pg_dump"
tar -C "$DATA_DIR" -cf "$WORK/$NAME/data.tar" . || fail "архив тома данных"
(cd "$WORK/$NAME" && sha256sum database.dump data.tar > SHA256SUMS)

tar -C "$WORK" -cf - "$NAME" \
    | gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
          --symmetric --cipher-algo AES256 --output "$WORK/$NAME.tar.gpg" \
    || fail "шифрование"

# Проверка: то, что лежит в копии, восстанавливается.
mkdir "$WORK/check"
gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
    --decrypt "$WORK/$NAME.tar.gpg" | tar -C "$WORK/check" -xf - \
    || fail "копия не расшифровывается"
(cd "$WORK/check/$NAME" && sha256sum -c SHA256SUMS >/dev/null) || fail "суммы не сходятся"
pg_restore --list "$WORK/check/$NAME/database.dump" >/dev/null || fail "дамп не читается"
tar -tf "$WORK/check/$NAME/data.tar" >/dev/null || fail "архив тома не читается"

mv "$WORK/$NAME.tar.gpg" "$BACKUP_DIR/$NAME.tar.gpg"
SIZE=$(du -h "$BACKUP_DIR/$NAME.tar.gpg" | cut -f1)
log "OK $NAME.tar.gpg $SIZE"

find "$BACKUP_DIR" -name 'sav-backup-*.tar.gpg' -mtime "+$RETENTION" -print -delete \
    | while read -r old; do log "REMOVED $(basename "$old") (старше $RETENTION дн.)"; done
