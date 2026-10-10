# Резервное копирование (P6): pg_dump той же версии, что сервер, и gpg.
FROM postgres:16-alpine
RUN apk add --no-cache gnupg tar
COPY scripts/backup.sh scripts/restore.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/backup.sh /usr/local/bin/restore.sh
ENTRYPOINT ["/usr/local/bin/backup.sh"]
