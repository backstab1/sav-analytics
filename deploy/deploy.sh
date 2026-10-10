#!/bin/sh
# Развёртывание образа на сервере с автоматическим откатом (P6).
#
#   ./deploy/deploy.sh ghcr.io/backstab1/sav-analytics:<sha>
#
# 1. Тянет образ и запоминает текущий тег в .deployed-previous.
# 2. Миграции базы — отдельным шагом (`migrate`); они только добавляют,
#    поэтому прежний образ работает поверх новой схемы и откат возможен
#    без отката базы (docs/operations.md, «Миграции и откат»).
# 3. Перезапускает api и worker и ждёт /api/ready через прокси до 120 с.
# 4. Не дождался — возвращает прежний тег и выходит с ошибкой.
set -eu

IMAGE=${1:?укажите образ с тегом}
cd "$(dirname "$0")/.."
CURRENT=$(cat .deployed-image 2>/dev/null || true)

ready() {
    docker compose exec -T api python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4)" \
        >/dev/null 2>&1
}

switch_to() {
    SAV_IMAGE=$1 docker compose up -d --no-build --remove-orphans migrate api worker proxy
}

echo "Разворачиваем $IMAGE (сейчас: ${CURRENT:-нет})"
docker pull "$IMAGE"
SAV_IMAGE=$IMAGE docker compose run --rm --no-deps migrate
switch_to "$IMAGE"

waited=0
until ready; do
    waited=$((waited + 5))
    if [ "$waited" -ge 120 ]; then
        echo "Новая версия не готова за 120 с — откат на ${CURRENT:-прежнюю}" >&2
        if [ -n "$CURRENT" ]; then
            switch_to "$CURRENT"
        fi
        exit 1
    fi
    sleep 5
done

if [ -n "$CURRENT" ]; then
    printf '%s\n' "$CURRENT" > .deployed-previous
fi
printf '%s\n' "$IMAGE" > .deployed-image
echo "Готово: $IMAGE"
