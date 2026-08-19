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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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


def _open_weight_sheet(page: Page) -> None:
    """Лист веса открывается из плитки «Вес» раздела «Отчёт».

    Баннеры, базы и веса перестали быть разделами: это свойства книги,
    и живут плитками раздела «Отчёт». Статистика листа не требует —
    её варианты видны прямо в панели под плитками.
    """
    _open_view(page, "report")
    page.click('[data-block="weight"] [data-open-sheet="report-settings"]')


def _panel_text(page: Page, selector: str) -> str:
    """Текст диагностического блока, если он вообще появился на странице."""
    locator = page.locator(selector)
    if not locator.count():
        return "—"
    return " ".join(locator.first.inner_text().split()) or "—"


def _download_artifact(page: Page, link: str, target: Path) -> Path:
    """Скачать подготовленный артефакт, объяснив причину, если он не поехал.

    Скачивание асинхронное: preflight, запуск сборки, опрос задачи и только
    потом сам файл. На любом отказе приложение кладёт причину в #report-status
    и оставляет её на экране, но download при этом просто не наступает, и
    expect_download отдаёт голый TimeoutError без единого намёка на то, что
    случилось. Этот тест изредка падает именно так, поэтому причину дочитываем
    со страницы — иначе следующее падение снова будет нечитаемым.
    """
    page.click("#export-toggle")
    try:
        with page.expect_download(timeout=REPORT_TIMEOUT) as download:
            page.click(link)
    except PlaywrightTimeoutError as error:
        raise AssertionError(
            f"{link} не отдал файл за {REPORT_TIMEOUT // 1000} с.\n"
            f"Статус отчёта: {_panel_text(page, '#report-status')}\n"
            f"Проверка конфигурации: {_panel_text(page, '#report-findings')}\n"
            f"Прогресс: {_panel_text(page, '#report-progress')}"
        ) from error
    download.value.save_as(target)
    return target


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

    # Перекодировка: числовой возраст в группы. Заводится из карточки самого
    # вопроса — своего раздела у перекодировок больше нет, а источник
    # подставляется тем вопросом, из которого пришли.
    page.click("#table-body tr:has-text('AGE') .question-cell")
    expect(page.locator("#question-recodings")).to_be_visible(timeout=UI_TIMEOUT)
    page.click("#question-recodings [data-new-recoding='AGE']")
    expect(page.locator("#recode-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#recode-source")).to_have_value("AGE")
    page.fill("#recode-code", "AGEGRP")
    page.fill("#recode-name", "Возрастные группы")
    page.click("#save-recoding")
    # Сохранение асинхронное и само переоткрывает редактор, поэтому закрываем
    # его только после подтверждения — иначе гонка с перерисовкой.
    expect(page.locator("#toast-container")).to_contain_text(
        "Перекодировка сохранена", timeout=UI_TIMEOUT
    )
    # Закрытие редактора возвращает в тот же вопрос, и группировка уже там.
    page.click("#close-recode-editor")
    expect(page.locator("#question-recodings")).to_contain_text(
        "Возрастные группы", timeout=UI_TIMEOUT
    )
    expect(page.locator("#table-body")).to_contain_text("1 группировка", timeout=UI_TIMEOUT)
    page.click("#close-editor")

    _open_view(page, "report")

    # Фильтр: именованное правило по одному ответу. Создаётся из строки
    # «База отчёта» — отдельного раздела под правила больше нет.
    page.click('[data-block="filter"] [data-new="filter"]')
    expect(page.locator("#filter-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#filter-name", "Только женщины")
    condition = page.locator("#filter-condition-list .filter-condition").first
    condition.locator("select.filter-source").select_option("question:SEX")
    # Операция по умолчанию — «равно», значение вводится кодом ответа.
    expect(condition.locator("select.filter-operation")).to_have_value("eq", timeout=UI_TIMEOUT)
    condition.locator("input.filter-value").fill("2")
    page.click("#save-filter")
    # Сохранённое правило попадает в поповер выбора базы — бывший экран целиком.
    page.click('[data-picker="filter"]')
    expect(page.locator("#picker")).to_contain_text("Только женщины", timeout=UI_TIMEOUT)
    page.keyboard.press("Escape")

    # Баннер теперь описывает только колонки отчёта и создаётся из строки
    # «Колонки» того же раздела.
    page.click('[data-block="banner"] [data-new="banner"]')
    expect(page.locator("#banner-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#banner-name", "Пол")
    page.locator("#banner-block-list select").first.select_option("question:SEX")
    page.click("#save-banner")
    expect(page.locator("#banner-preview")).to_contain_text("Мужчина", timeout=UI_TIMEOUT)
    # Первый баннер становится баннером отчёта, и строка «Колонки» это показывает.
    page.click("#close-banner-editor")
    expect(page.locator('[data-block="banner"]')).to_contain_text("Пол", timeout=UI_TIMEOUT)

    # Статистические параметры видны прямо в разделе и применяются сразу:
    # схема сравнения — один сегментированный переключатель на два поля.
    expect(page.locator("#stat-panel")).to_be_visible(timeout=UI_TIMEOUT)
    page.click('#stat-panel [data-stat="scheme"][data-value="rest"]')
    expect(page.locator('#stat-panel [data-stat="scheme"][data-value="rest"]')).to_have_attribute(
        "aria-checked", "true", timeout=UI_TIMEOUT
    )
    page.click('#stat-panel [data-stat="pairwise"]')
    expect(page.locator('#stat-panel [data-stat="pairwise"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )

    # Состав книги стоит отдельной полосой над плитками свойств.
    expect(page.locator('[data-block="content"]')).to_contain_text(
        "Состав книги", timeout=UI_TIMEOUT
    )

    # Подготовка и скачивание обоих артефактов.
    workbook = _download_artifact(page, "#download-report", tmp_path / "topline.xlsx")
    audit = _download_artifact(page, "#download-statistics", tmp_path / "statistics.txt")

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


def test_identifier_cannot_be_chosen_as_a_report_weight(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Обязательный сценарий P1.0: `ID` не становится весом через интерфейс.

    Проверяется вся цепочка защиты, а не одно звено: в списке весов `ID` нет,
    после явного объявления весом он появляется, но разбор распределения
    отвергает его и не даёт сохранить настройки.
    """
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    _open_weight_sheet(page)
    expect(page.locator("#report-weight")).to_be_visible(timeout=UI_TIMEOUT)

    # Ни одна переменная массива весом не объявлена, поэтому выбирать нечего.
    expect(page.locator("#report-weight option")).to_have_count(1, timeout=UI_TIMEOUT)
    expect(page.locator("#report-weight-help")).to_contain_text("Ни одна переменная")

    # Явное действие: аналитик настаивает, что `ID` — вес.
    page.locator("#report-weight-declare summary").click()
    page.select_option("#report-weight-candidate", "ID")
    page.click("#report-weight-declare-button")

    # Роль получена, но отчёт на таком весе не собрать: решает распределение.
    expect(page.locator("#report-weight-diagnostics")).to_contain_text(
        "не похож на поправочный вес", timeout=UI_TIMEOUT
    )
    expect(page.locator("#save-report-settings")).to_be_disabled()

    # Проверка не запрещает взвешивание вообще: переменная с правдоподобным
    # распределением, объявленная весом, проходит. Осмысленность веса остаётся
    # на аналитике — машина отвечает за то, что число не сломано.
    # После объявления веса интерфейс закрывает блок (declareSelectedWeight),
    # поэтому для второй попытки его надо раскрыть заново.
    page.locator("#report-weight-declare summary").click()
    page.select_option("#report-weight-candidate", "AGE")
    page.click("#report-weight-declare-button")
    expect(page.locator("#report-weight-diagnostics")).to_contain_text(
        "Вес пригоден", timeout=UI_TIMEOUT
    )
    expect(page.locator("#save-report-settings")).to_be_enabled()
