"""Сквозной сценарий в настоящем браузере.

Юнит- и API-тесты не видят связку интерфейса с сервером: они дергают эндпоинты
напрямую и остаются зелёными, даже когда кнопка не находит свой обработчик,
редактор не открывается или ссылка на готовый артефакт никуда не ведёт. Этот
файл проходит путь целиком — загрузка SAV, правка структуры, перекодировка,
фильтр, баннер, подготовка отчёта и скачивание обоих файлов — и проверяет то,
что реально скачалось.

Тесты помечены `browser` и по умолчанию не собираются: нужны бинарники
Chromium (`playwright install chromium`).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pandas as pd
import pyreadstat
import pytest
import uvicorn
from playwright.sync_api import Page, expect

from sav_analytics.api import app, get_repository
from sav_analytics.repository import ProjectRepository

pytestmark = pytest.mark.browser

# Интерфейс отвечает на действия асинхронно, поэтому ожидания выражены через
# expect(...) с явным таймаутом, а не через sleep.
UI_TIMEOUT = 15_000
REPORT_TIMEOUT = 90_000


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _write_survey(path: Path) -> None:
    """Массив, на котором осмысленны и баннер, и перекодировка, и фильтр."""
    size = 240
    frame = pd.DataFrame(
        {
            "ID": range(1, size + 1),
            "SEX": [1 if index % 2 else 2 for index in range(size)],
            "AGE": [18 + (index * 7) % 45 for index in range(size)],
            "BRAND": [1 if index % 3 else 2 for index in range(size)],
            "SCORE": [(index % 11) for index in range(size)],
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "ID": "Номер интервью",
            "SEX": "Ваш пол",
            "AGE": "Возраст, полных лет",
            "BRAND": "Какой маркой пользуетесь",
            "SCORE": "Готовность рекомендовать",
        },
        variable_value_labels={
            "SEX": {1: "Мужчина", 2: "Женщина"},
            "BRAND": {1: "Первая", 2: "Вторая"},
        },
        variable_measure={
            "ID": "nominal",
            "SEX": "nominal",
            "AGE": "scale",
            "BRAND": "nominal",
            "SCORE": "scale",
        },
    )


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    """Настоящий HTTP-сервер поверх того же приложения и временного хранилища."""
    repository = ProjectRepository(tmp_path / "projects", max_upload_bytes=10_000_000)
    app.dependency_overrides[get_repository] = lambda: repository
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("Сервер не поднялся за 20 секунд.")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        app.dependency_overrides.pop(get_repository, None)


def _open_project(page: Page, base_url: str, source: Path) -> None:
    page.goto(base_url)
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("input[name='name']", "Браузерный сценарий")
    page.set_input_files("#file", str(source))
    page.click("#submit")
    expect(page.locator("#workspace")).to_be_visible(timeout=UI_TIMEOUT)


def _open_view(page: Page, view: str) -> None:
    page.click(f".tabs button[data-view='{view}']")


def test_full_analyst_workflow_from_upload_to_downloaded_files(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    # Структура прочитана и показана.
    expect(page.locator("#table-body tr")).not_to_have_count(0, timeout=UI_TIMEOUT)
    expect(page.locator("#project-name")).to_have_text("Браузерный сценарий")

    # Правка вопроса доходит до сервера: после сохранения имя видно в таблице.
    page.click("#table-body tr:has-text('BRAND') .question-cell")
    expect(page.locator("#question-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#question-label", "Основная марка")
    page.click("#save-question")
    expect(page.locator("#table-body")).to_contain_text("Основная марка", timeout=UI_TIMEOUT)

    # Перекодировка: числовой возраст в группы.
    _open_view(page, "recodings")
    page.click("#new-recoding")
    expect(page.locator("#recode-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#recode-code", "AGEGRP")
    page.fill("#recode-name", "Возрастные группы")
    page.select_option("#recode-source", "AGE")
    page.click("#save-recoding")
    expect(page.locator("#entity-list")).to_contain_text("Возрастные группы", timeout=UI_TIMEOUT)

    # Фильтр: именованное правило по одному ответу.
    _open_view(page, "filters")
    page.click("#new-filter")
    expect(page.locator("#filter-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#filter-name", "Только женщины")
    condition = page.locator("#filter-condition-list .filter-condition").first
    condition.locator("select.filter-source").select_option("question:SEX")
    # Операция по умолчанию — «равно», значение вводится кодом ответа.
    expect(condition.locator("select.filter-operation")).to_have_value("eq", timeout=UI_TIMEOUT)
    condition.locator("input.filter-value").fill("2")
    page.click("#save-filter")
    expect(page.locator("#entity-list")).to_contain_text("Только женщины", timeout=UI_TIMEOUT)

    # Баннер теперь описывает только колонки отчёта.
    _open_view(page, "banners")
    page.click("#new-banner")
    expect(page.locator("#banner-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#banner-name", "Пол")
    page.locator("#banner-block-list select").first.select_option("question:SEX")
    page.click("#save-banner")
    expect(page.locator("#banner-preview")).to_contain_text("Мужчина", timeout=UI_TIMEOUT)

    # Статистические параметры живут отдельно и применяются ко всему отчёту.
    _open_view(page, "report-settings")
    expect(page.locator("#report-settings-form")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#report-compare-target")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#report-compare-target")).to_be_disabled()
    page.locator("#report-compare-subgroups").check()
    expect(page.locator("#report-compare-target")).to_be_enabled()
    page.click("#save-report-settings")
    expect(page.locator("#toast-container")).to_contain_text(
        "Настройки отчёта сохранены", timeout=UI_TIMEOUT
    )

    # Подготовка и скачивание обоих артефактов.
    page.click("#export-toggle")
    with page.expect_download(timeout=REPORT_TIMEOUT) as download:
        page.click("#download-report")
    workbook = tmp_path / "topline.xlsx"
    download.value.save_as(workbook)

    page.click("#export-toggle")
    with page.expect_download(timeout=REPORT_TIMEOUT) as download:
        page.click("#download-statistics")
    audit = tmp_path / "statistics.txt"
    download.value.save_as(audit)

    # Скачалось именно то, что должно: настоящая книга и настоящий аудит.
    with ZipFile(workbook) as archive:
        names = set(archive.namelist())
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/sharedStrings.xml" in names
        strings = archive.read("xl/sharedStrings.xml").decode("utf-8")
        assert "Основная марка" in strings

    text = audit.read_text(encoding="utf-8")
    assert "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА" in text
    assert "Браузерный сценарий" in text
    assert "Subgroup/Rest" in text
    assert "подгруппа против остальных респондентов блока" in text


def test_screens_switch_and_the_project_bar_actions_stay_reachable(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Оболочка экранов: то, что ломалось при каждой перестановке шапки."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#screen-nav button[data-screen='builder']")
    expect(page.locator("#screen-builder")).to_be_visible(timeout=UI_TIMEOUT)
    # Конструктор получает переменные проекта, а не грузит их сам.
    expect(page.locator("#bld-list .bld-var")).not_to_have_count(0, timeout=UI_TIMEOUT)

    page.click("#screen-nav button[data-screen='home']")
    expect(page.locator("#screen-home")).to_be_visible(timeout=UI_TIMEOUT)

    # «Новый проект» из другого экрана возвращает на ручной режим, а не молчит.
    page.click("#new-project")
    expect(page.locator("#screen-manual")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)


def test_upload_rejects_a_file_that_is_not_sav(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Отказ должен доходить до пользователя, а не оставаться в консоли."""
    broken = tmp_path / f"{uuid4().hex}.sav"
    broken.write_bytes(BytesIO(b"not a real sav").getvalue())

    page.goto(live_server)
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("input[name='name']", "Битый файл")
    page.set_input_files("#file", str(broken))
    page.click("#submit")

    expect(page.locator("#form-error")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#workspace")).to_be_hidden()
