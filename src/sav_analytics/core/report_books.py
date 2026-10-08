"""Несколько книг отчёта в проекте (PQ.7).

У книги свои баннер, общий фильтр и настройки (статистика, вес, профиль
вывода). Состав вопросов, перекодировки, баннеры, фильтры и веса — общие
для проекта, книга на них только ссылается.

Хранение. Активная книга живёт там же, где жила единственная книга до
появления списка: `report_banner_id`, `report_filter_id` и
`report_settings` конфигурации. Остальные книги держат свои значения в
`configuration.reports` под ключами `banner_id`, `filter_id`, `settings`;
у записи активной книги этих ключей нет. Переключение книги меняет значения
местами. Так у каждой настройки одно место, а весь код, который строит
книгу, читает те же три поля, что и раньше (решение 040 хранилища заметок).
Неактивную книгу собирают через `configuration_for_book`: копия
конфигурации, в которой она поставлена на место активной.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from uuid import uuid4

from .report_settings import DEFAULT_REPORT_SETTINGS

DEFAULT_BOOK_NAME = "Отчёт"
_NUMBERED = re.compile(r"^Отчёт (\d+)$")

# Поле конфигурации активной книги → ключ записи неактивной.
BOOK_FIELDS = (
    ("report_banner_id", "banner_id"),
    ("report_filter_id", "filter_id"),
    ("report_settings", "settings"),
)


class ReportBookError(ValueError):
    pass


def ensure_books(configuration: dict[str, Any]) -> None:
    """Проект без списка книг получает одну — ту, что в нём уже настроена."""
    books = configuration.get("reports")
    if not books:
        identifier = str(uuid4())
        configuration["reports"] = [{"id": identifier, "name": DEFAULT_BOOK_NAME}]
        configuration["active_report_id"] = identifier
        return
    if not any(book["id"] == configuration.get("active_report_id") for book in books):
        # Активной могла быть удалённая в обход API книга: первая из
        # оставшихся становится активной со своими сохранёнными значениями.
        _load(configuration, books[0])
        configuration["active_report_id"] = books[0]["id"]


def books(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    """Книги проекта с их значениями — у активной они берутся из конфигурации."""
    active = configuration.get("active_report_id")
    result = []
    for book in configuration.get("reports", []):
        if book["id"] == active:
            values = {key: configuration.get(field) for field, key in BOOK_FIELDS}
        else:
            values = {key: book.get(key) for _, key in BOOK_FIELDS}
        result.append(
            {"id": book["id"], "name": book["name"], **values, "active": book["id"] == active}
        )
    return result


def find_book(configuration: dict[str, Any], book_id: str) -> dict[str, Any]:
    for book in configuration.get("reports", []):
        if book["id"] == str(book_id):
            return book
    raise ReportBookError("Книга отчёта не найдена.")


def configuration_for_book(configuration: dict[str, Any], book_id: str | None) -> dict[str, Any]:
    """Конфигурация, в которой книга `book_id` стоит на месте активной.

    Для активной книги (и `None`) возвращается сама конфигурация, без копии.
    """
    if book_id is None or str(book_id) == configuration.get("active_report_id"):
        return configuration
    result = copy.deepcopy(configuration)
    activate(result, str(book_id))
    return result


def activate(configuration: dict[str, Any], book_id: str) -> None:
    """Сделать книгу активной: значения прежней уходят в её запись."""
    target = find_book(configuration, book_id)
    active_id = configuration.get("active_report_id")
    if target["id"] == active_id:
        return
    current = next(
        (book for book in configuration.get("reports", []) if book["id"] == active_id), None
    )
    if current is not None:
        for field, key in BOOK_FIELDS:
            current[key] = copy.deepcopy(configuration.get(field))
    _load(configuration, target)
    configuration["active_report_id"] = target["id"]


def create_book(
    configuration: dict[str, Any], name: str | None, *, source_id: str | None = None
) -> dict[str, Any]:
    """Новая книга сразу за исходной (копия) или в конце (пустая); она становится активной."""
    reports = configuration["reports"]
    if source_id is not None:
        find_book(configuration, source_id)
        source = next(item for item in books(configuration) if item["id"] == str(source_id))
        values = {key: copy.deepcopy(source[key]) for _, key in BOOK_FIELDS}
        position = (
            next(index for index, item in enumerate(reports) if item["id"] == source["id"]) + 1
        )
        name = name or _copy_name(source["name"], reports)
    else:
        values = _empty_values()
        position = len(reports)
        name = name or _next_default_name(reports)
    book = {"id": str(uuid4()), "name": _clean_name(name), **values}
    reports.insert(position, book)
    activate(configuration, book["id"])
    return book


def rename_book(configuration: dict[str, Any], book_id: str, name: str) -> None:
    find_book(configuration, book_id)["name"] = _clean_name(name)


def clear_book(configuration: dict[str, Any], book_id: str) -> None:
    """Баннер и фильтр снимаются, настройки возвращаются к значениям по умолчанию."""
    book = find_book(configuration, book_id)
    values = _empty_values()
    if book["id"] == configuration.get("active_report_id"):
        _load(configuration, values)
    else:
        book.update(values)


def delete_book(configuration: dict[str, Any], book_id: str) -> None:
    reports = configuration["reports"]
    book = find_book(configuration, book_id)
    if len(reports) == 1:
        raise ReportBookError("Единственную книгу удалить нельзя — её можно очистить.")
    index = reports.index(book)
    if book["id"] == configuration.get("active_report_id"):
        neighbour = reports[index + 1] if index + 1 < len(reports) else reports[index - 1]
        activate(configuration, neighbour["id"])
    reports.remove(book)


def book_settings(configuration: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Настройки всех книг с их названиями — для проверок «где выбран вес»."""
    return [(book["name"], book["settings"] or {}) for book in books(configuration)]


def inactive_books(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    active = configuration.get("active_report_id")
    return [book for book in configuration.get("reports", []) if book["id"] != active]


def _load(configuration: dict[str, Any], book: dict[str, Any]) -> None:
    for field, key in BOOK_FIELDS:
        value = book.pop(key, None) if key in book else None
        if field == "report_settings":
            value = {**DEFAULT_REPORT_SETTINGS, **(value or {})}
        configuration[field] = value


def _empty_values() -> dict[str, Any]:
    return {
        "banner_id": None,
        "filter_id": None,
        "settings": copy.deepcopy(DEFAULT_REPORT_SETTINGS),
    }


def _clean_name(name: str) -> str:
    cleaned = " ".join(str(name).split())[:120]
    if not cleaned:
        raise ReportBookError("Название книги не может быть пустым.")
    return cleaned


def _next_default_name(reports: list[dict[str, Any]]) -> str:
    taken = {
        int(match.group(1)) for book in reports if (match := _NUMBERED.match(book.get("name", "")))
    }
    number = 2
    while number in taken:
        number += 1
    return f"Отчёт {number}"


def _copy_name(name: str, reports: list[dict[str, Any]]) -> str:
    names = {book.get("name") for book in reports}
    candidate = f"{name} (копия)"
    number = 2
    while candidate in names:
        candidate = f"{name} (копия {number})"
        number += 1
    return candidate[:120]
