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

import re
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
    _open_view(page, "reports")
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

    _open_view(page, "reports")

    # Фильтр: именованное правило по одному ответу. Создаётся из строки
    # «База отчёта» — отдельного раздела под правила больше нет.
    page.click('[data-block="filter"] [data-new="filter"]')
    expect(page.locator("#filter-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#filter-name", "Только женщины")
    condition = page.locator("#filter-condition-list .filter-condition").first
    condition.locator("select.filter-source").select_option("question:SEX")
    # Ответ выбирается галочкой по подписи, код SPSS вводить не нужно,
    # а правило тут же читается обычным текстом.
    expect(condition.locator("select.filter-operation")).to_have_value("in", timeout=UI_TIMEOUT)
    condition.locator(".filter-option", has_text="Женщина").locator("input").check()
    expect(page.locator("#filter-preview")).to_contain_text("Ваш пол: Женщина", timeout=UI_TIMEOUT)
    expect(page.locator("#filter-preview .filter-result strong")).to_have_text(
        "120", timeout=UI_TIMEOUT
    )
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

    # Раздел стоит двумя колонками: слева свойства книги, справа статистика.
    expect(page.locator("#entity-list .split > .col")).to_have_count(2, timeout=UI_TIMEOUT)

    # Состав — первая строка левой колонки. Он итог структуры, а не
    # настройка книги, поэтому действие у него одно: переход в «Данные».
    expect(page.locator('[data-block="content"]')).to_contain_text(
        "Состав", timeout=UI_TIMEOUT
    )
    expect(page.locator('[data-block="content"] [data-goto="data"]')).to_be_visible()

    # Подготовка и скачивание обоих артефактов.
    workbook = _download_artifact(page, "#download-report", tmp_path / "topline.xlsx")
    audit = _download_artifact(page, "#download-statistics", tmp_path / "statistics.txt")

    # Сборка попала в историю запусков и собрана по текущим настройкам.
    expect(page.locator("#report-runs .run").first).to_contain_text(
        "текущие настройки", timeout=UI_TIMEOUT
    )

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


def test_data_rows_show_value_labels_pagination_and_saved_filter(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    # Сохранённые базы из отчёта доступны и для просмотра строк.
    _open_view(page, "reports")
    page.click('[data-block="filter"] [data-new="filter"]')
    page.fill("#filter-name", "Только женщины")
    condition = page.locator("#filter-condition-list .filter-condition").first
    condition.locator("select.filter-source").select_option("question:SEX")
    condition.locator(".filter-option", has_text="Женщина").locator("input").check()
    page.click("#save-filter")
    expect(page.locator("#toast-container")).to_contain_text(
        "Правило сохранено", timeout=UI_TIMEOUT
    )
    page.click("#close-filter-editor")

    _open_view(page, "data")
    rows_mode = page.locator('[data-structure-mode="rows"]')
    expect(rows_mode).to_be_hidden()
    rows_mode.evaluate("button => button.click()")
    expect(page.locator("#row-view-controls")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#row-page")).to_have_text("1–50 из 240", timeout=UI_TIMEOUT)
    expect(page.locator("#table-body .data-row").first).to_contain_text("Женщина")
    raw_sex = page.locator("#table-body .data-row").first.locator(".data-cell").nth(1)
    expect(raw_sex.locator("small")).to_have_text("2")

    page.click("#row-next")
    expect(page.locator("#row-page")).to_have_text("51–100 из 240", timeout=UI_TIMEOUT)
    page.select_option("#row-filter", label="Только женщины")
    expect(page.locator("#row-page")).to_have_text("1–50 из 120", timeout=UI_TIMEOUT)
    expect(page.locator("#table-body .data-row").first.locator(".row-number")).to_have_text("1")
    expect(page.locator("#table-body .data-row").nth(1).locator(".row-number")).to_have_text("3")


def test_screens_switch_and_the_project_bar_actions_stay_reachable(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Оболочка экранов: то, что ломалось при каждой перестановке шапки."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    # Конструктор стал разделом «Таблицы» рабочей области, и у раздела есть
    # адрес: перезагрузка возвращает тот же проект в тот же раздел.
    _open_view(page, "tables")
    expect(page.locator("#section-tables")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page).to_have_url(re.compile(r"#/projects/[0-9a-f-]{36}/tables$"))
    page.reload()
    expect(page.locator("#section-tables")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#project-name")).to_have_text("Браузерный сценарий")
    # Конструктор получает переменные проекта, а не грузит их сам.
    expect(page.locator("#bld-list .bld-var")).not_to_have_count(0, timeout=UI_TIMEOUT)
    # Полки стали полосой параметров: список переменных открывается из неё.
    page.click('.bld-param[data-zone="rows"]')
    expect(page.locator("#bld-picker")).to_be_visible(timeout=UI_TIMEOUT)
    page.keyboard.press("Escape")
    expect(page.locator("#bld-picker")).to_be_hidden(timeout=UI_TIMEOUT)
    # Поповер раскрывается вниз от пилюли и целиком помещается в окно:
    # прежняя версия цеплялась за верх пилюли и уезжала за нижний край,
    # когда раздел не помещался в высоту.
    page.click('.bld-param[data-zone="rows"]')
    picker = page.locator("#bld-picker").bounding_box()
    viewport = page.viewport_size
    assert picker["y"] >= 0 and picker["y"] + picker["height"] <= viewport["height"] + 1, picker
    page.keyboard.press("Escape")

    # «Анализ» — свой раздел с карточками связи.
    _open_view(page, "analysis")
    expect(page.locator("#section-analysis")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#section-soon")).to_be_hidden()
    expect(page.locator("#section-tables")).to_be_hidden()
    # «Открытые ответы» — кодификатор; заглушек у разделов больше нет.
    _open_view(page, "text")
    expect(page.locator("#section-text")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#section-soon")).to_be_hidden()
    expect(page.locator("#section-analysis")).to_be_hidden()

    page.click("#screen-nav button[data-screen='home']")
    expect(page.locator("#screen-home")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page).to_have_url(re.compile(r"#/home$"))

    # «Новый проект» из другого экрана возвращает на ручной режим, а не молчит.
    page.click("#new-project")
    expect(page.locator("#screen-manual")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page).to_have_url(re.compile(r"#/$"))


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


def test_cell_weight_is_built_from_combinations(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Вес по ячейкам: цель задаётся каждому сочетанию пол × марка (PQ.9)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    page.click('[data-picker="weight"]')
    page.click('#picker [data-new="weight"]')
    expect(page.locator("#weight-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.select_option("#weight-method", "cells")
    expect(page.locator("#weight-cells")).to_be_visible()
    page.click("#add-weight-dimension")
    page.locator(".weight-dimension-source").nth(1).select_option("BRAND")
    cells = page.locator("#weight-cell-list .weight-cell")
    expect(cells).to_have_count(4)
    expect(page.locator("#weight-cells-sum")).to_have_text("100%")
    page.fill("#weight-name", "Пол × марка")
    page.click("#save-weight")

    preview = page.locator("#weight-preview")
    expect(preview).to_contain_text("Ячейки", timeout=UI_TIMEOUT)
    expect(preview).to_contain_text("Мужчина × Первая")
    expect(page.locator("#weight-editor-kicker")).to_have_text("Взвешивание по ячейкам")


def test_raking_dimension_can_be_a_saved_recoding(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Цель веса задаётся группам перекодировки возраста, а не годам (P1.3)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    project_id = page.url.split("#/projects/")[1].split("/")[0]
    created = page.request.post(
        f"{live_server}/api/projects/{project_id}/recodings",
        data={
            "code": "AGE_GROUP",
            "name": "Возрастная группа",
            "source_variable": "AGE",
            "categories": [
                {"label": "До 40", "lower": 0, "upper": 39},
                {"label": "40+", "lower": 40, "upper": 120},
            ],
        },
    )
    assert created.ok
    recoding_id = created.json()["configuration"]["recodings"][0]["id"]
    page.reload()
    expect(page.locator("#workspace")).to_be_visible(timeout=UI_TIMEOUT)
    _open_view(page, "reports")

    page.click('[data-picker="weight"]')
    page.click('#picker [data-new="weight"]')
    expect(page.locator("#weight-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.locator(".weight-dimension-source").first.select_option(f"recoding:{recoding_id}")
    targets = page.locator(".weight-dimension").first.locator(".weight-target .lbl")
    expect(targets).to_have_text(["До 40", "40+"])
    page.fill("#weight-name", "По возрастной группе")
    page.click("#save-weight")
    expect(page.locator("#weight-preview")).to_contain_text("Возрастная группа", timeout=UI_TIMEOUT)


def test_weight_targets_round_trip_through_the_excel_template(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Шаблон целей скачивается по редактору и загружается заполненным (§10)."""
    import xlsxwriter

    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")
    page.click('[data-picker="weight"]')
    page.click('#picker [data-new="weight"]')
    expect(page.locator("#weight-editor")).to_be_visible(timeout=UI_TIMEOUT)

    with page.expect_download() as download:
        page.click("#weight-template")
    template = tmp_path / "weight_targets.xlsx"
    download.value.save_as(template)
    assert template.stat().st_size > 1000

    filled = tmp_path / "filled.xlsx"
    workbook = xlsxwriter.Workbook(str(filled))
    sheet = workbook.add_worksheet()
    sheet.write_row(0, 0, ["Ключ", "Переменная", "Категория", "Код", "Цель, %"])
    sheet.write_row(1, 0, ["SEX", "Ваш пол", "Мужчина", "1", 48])
    sheet.write_row(2, 0, ["SEX", "Ваш пол", "Женщина", "2", 52])
    workbook.close()
    page.set_input_files("#weight-targets-file", str(filled))

    expect(page.locator("#weight-targets-status")).to_have_text(
        "Цели загружены: 2 из 2", timeout=UI_TIMEOUT
    )
    inputs = page.locator(".weight-dimension").first.locator(".weight-target input")
    expect(inputs.nth(0)).to_have_value("48")
    expect(inputs.nth(1)).to_have_value("52")


def test_heuristic_scale_is_counted_for_review_until_confirmed(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Предупреждение распознавания и «0 проверить» не стоят на экране вместе.

    SCORE — шкала без подписей значений, тип узнан по диапазону. До P1.0 у неё
    было предупреждение «требует проверки», статус «Готов» и счётчик ноль.
    """
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    review_count = page.locator("#summary [data-status-filter='review'] b")
    score_row = page.locator("#table-body tr[data-code='SCORE']")
    expect(review_count).to_have_text("1", timeout=UI_TIMEOUT)
    expect(score_row.locator(".status")).to_have_text("Проверить")
    expect(score_row.locator(".q-sub.warning")).to_contain_text("требует проверки")

    score_row.locator(".question-cell").click()
    expect(page.locator("#question-review")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#save-question")).to_have_text("Подтвердить и сохранить")
    page.click("#save-question")

    expect(review_count).to_have_text("0", timeout=UI_TIMEOUT)
    expect(score_row.locator(".status")).to_have_text("Готов")
    expect(score_row.locator(".q-sub.warning")).to_have_count(0)
    expect(page.locator("#question-review")).to_be_hidden()
    expect(page.locator("#save-question")).to_have_text("Сохранить")


def test_excluded_answers_are_offered_only_where_they_change_numbers(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """У одиночного выбора «Исключить ответы» ничего не меняла — панели нет (GAP-003).

    У шкалы панель остаётся, и подпись называет фактический эффект: среднее и
    Top/Bottom, а не распределение.
    """
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    expect(page.locator("#question-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#special-answers")).to_be_hidden()

    page.select_option("#question-type", "scale")
    expect(page.locator("#special-answers")).to_be_visible()
    expect(page.locator("#special-answers .grp-cap small")).to_contain_text(
        "Не входят в среднее и Top/Bottom"
    )


def test_marking_a_labelled_category_not_applicable_needs_confirmation(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Подписанный ответ не уходит в «не применимо» одним щелчком (GAP-004)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    expect(page.locator("#not-applicable")).to_be_visible(timeout=UI_TIMEOUT)
    assessment = page.locator("#not-applicable-assessment")
    expect(assessment).to_contain_text("Валидная база", timeout=UI_TIMEOUT)

    page.check("#not-applicable-list [data-not-applicable='1']")
    expect(assessment).to_contain_text("→", timeout=UI_TIMEOUT)
    expect(assessment).to_contain_text("подписанная категория")

    page.click("#save-question")
    expect(page.locator("#editor-error")).to_contain_text("Подтвердите", timeout=UI_TIMEOUT)

    page.check("#not-applicable-confirm")
    page.click("#save-question")
    expect(page.locator("#toast-container")).to_contain_text(
        "Настройки вопроса сохранены", timeout=UI_TIMEOUT
    )
    expect(page.locator("#editor-error")).to_be_hidden()


def test_category_groups_are_built_by_moving_answers(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Группы перекодировки собираются из ответов с частотами — мышью и без неё."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    page.click("#question-recodings [data-new-recoding='BRAND']")
    expect(page.locator("#recode-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#recode-mode")).to_have_value("categories")
    page.fill("#recode-code", "BRANDGRP")
    page.fill("#recode-name", "Марки группами")

    pool = page.locator("#category-pool")
    expect(pool.locator(".value-chip")).to_have_count(2, timeout=UI_TIMEOUT)
    groups = page.locator("#category-group-list .category-group")

    # Без мыши: ответ выбирается щелчком и переносится кнопкой.
    pool.locator(".value-chip", has_text="Первая").click()
    groups.nth(0).locator(".zone-move").click()
    expect(groups.nth(0).locator(".category-zone-count")).to_contain_text("160")

    # Мышью: ответ перетаскивается в другую группу.
    pool.locator(".value-chip", has_text="Вторая").drag_to(groups.nth(1).locator(".value-chips"))
    expect(groups.nth(1).locator(".value-chip")).to_contain_text("Вторая", timeout=UI_TIMEOUT)
    expect(groups.nth(1).locator(".category-zone-count")).to_contain_text("80")
    expect(pool).to_contain_text("все ответы разложены")

    page.click("#save-recoding")
    expect(page.locator("#toast-container")).to_contain_text(
        "Перекодировка сохранена", timeout=UI_TIMEOUT
    )


def test_output_profile_is_read_from_the_chosen_metrics(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Набор вывода выбирается одним щелчком и узнаётся по отметкам."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    profile = page.locator('[data-stat="profile"]')
    expect(profile.filter(has_text="Стандарт")).to_have_attribute(
        "aria-checked", "true", timeout=UI_TIMEOUT
    )

    profile.filter(has_text="Аудит").click()
    expect(page.locator('[data-stat="pvalues"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )
    expect(page.locator('[data-stat="percent-decimals"][data-value="1"]')).to_have_attribute(
        "aria-checked", "true"
    )

    # Сняли одну отметку — набор перестал совпадать с образцом.
    page.click('[data-stat="scale:bottom2"]')
    expect(page.locator("#stat-panel")).to_contain_text("свой набор", timeout=UI_TIMEOUT)
    expect(page.locator('[data-stat="scale:bottom2"]')).to_have_attribute("aria-pressed", "false")


def test_tables_section_shows_the_numbers_of_the_workbook(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Раздел «Таблицы» считает ядром, а не рисует демонстрацию (PQ.3)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    _open_view(page, "tables")
    expect(page.locator(".tabs button[data-view='tables']")).not_to_contain_text("демо")
    grid = page.locator("#bld-grid-wrap")
    expect(grid).to_contain_text("Выберите вопросы", timeout=UI_TIMEOUT)

    page.click('.bld-param[data-zone="rows"]')
    page.click('#bld-list .bld-var[data-code="BRAND"]')
    page.keyboard.press("Escape")
    page.click('.bld-param[data-zone="cols"]')
    page.click('#bld-list .bld-var[data-code="SEX"]')
    page.keyboard.press("Escape")

    table = grid.locator("table.bld-grid")
    expect(table).to_contain_text("Мужчина", timeout=UI_TIMEOUT)
    expect(table.locator("thead tr.bld-base").first.locator("th.bld-basecell")).to_have_text(
        ["240", "120", "120"]
    )
    # 160 из 240 — 66,7%; книга выводит доли целыми, и экран показывает так же.
    first = table.locator("tbody tr", has_text="Первая").first.locator("td.bld-val")
    expect(first.first).to_have_text("67")
    expect(page.locator("#bld-tests")).to_have_text("не считаются")

    # Индекс к Total — та же ячейка, поделённая на Total: 100 у самого Total.
    page.select_option("#bld-measure", "index")
    expect(first.first).to_have_text("100")
    page.select_option("#bld-measure", "value")

    # Вложенный разрез: пол × марка даёт полное пересечение категорий.
    page.click('.bld-param[data-zone="cols"]')
    page.click('#bld-list .bld-var[data-code="BRAND"]')
    page.keyboard.press("Escape")
    nest = page.locator("#bld-nest")
    expect(nest).to_be_visible()
    nest.click()
    expect(table.locator("thead tr.bld-base").first.locator("th.bld-basecell")).to_have_count(
        5, timeout=UI_TIMEOUT
    )
    expect(table).to_contain_text("Ваш пол × Какой маркой пользуетесь")

    # Мост в отчёт: разрез сохраняется баннером и появляется в выборе колонок.
    page.click("#bld-save-cut")
    expect(page.locator("#bld-stage-note")).to_contain_text("баннером", timeout=UI_TIMEOUT)
    page.click('.bld-param[data-zone="cols"]')
    expect(page.locator("#bld-list")).to_contain_text("Баннеры отчёта", timeout=UI_TIMEOUT)
    page.keyboard.press("Escape")
    # Повторное нажатие не копит одинаковые баннеры, а называет сохранённый.
    page.click("#bld-save-cut")
    expect(page.locator("#bld-stage-note")).to_contain_text("уже сохранён", timeout=UI_TIMEOUT)
    banners = page.evaluate("window.SavApp.banners().length")
    assert banners == 1

    # Та же раскладка выгружается книгой Excel.
    page.click("#bld-export")
    with page.expect_download() as download:
        page.click('[data-export-scope="table"]')
    exported = tmp_path / "table.xlsx"
    download.value.save_as(exported)
    assert exported.stat().st_size > 5000

    # Все вопросы отчёта с тем же разрезом — книга шире таблицы.
    page.click("#bld-export")
    with page.expect_download() as download:
        page.click('[data-export-scope="report"]')
    whole = tmp_path / "report_by_cut.xlsx"
    download.value.save_as(whole)
    assert whole.stat().st_size > exported.stat().st_size


def test_net_group_set_on_a_question_reaches_the_table(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """NET-группа задаётся в карточке вопроса и становится строкой таблицы."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    expect(page.locator("#question-nets")).to_be_visible(timeout=UI_TIMEOUT)
    page.click("#add-net")
    net = page.locator("#net-list .net-row").first
    net.locator(".net-label").fill("Только первая")
    net.locator("[data-net-value='1']").check()
    page.click("#save-question")
    expect(page.locator("#toast-container")).to_contain_text(
        "Настройки вопроса сохранены", timeout=UI_TIMEOUT
    )

    _open_view(page, "tables")
    page.click('.bld-param[data-zone="rows"]')
    page.click('#bld-list .bld-var[data-code="BRAND"]')
    page.keyboard.press("Escape")
    row = page.locator("#bld-grid-wrap table.bld-grid tbody tr", has_text="NET: Только первая")
    expect(row).to_have_count(1, timeout=UI_TIMEOUT)
    expect(row.locator("td.bld-val").first).to_have_text("67")


def test_top_bottom_size_renames_the_scale_toggles(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    page.click('[data-stat="scale-box"][data-value="3"]')
    expect(page.locator('[data-stat="scale:top2"]')).to_have_text("Top-3", timeout=UI_TIMEOUT)
    expect(page.locator('[data-stat="scale:bottom2"]')).to_have_text("Bottom-3")

    page.click('[data-stat="counts"]')
    expect(page.locator('[data-stat="counts"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )
    page.click('[data-stat="row-percents"]')
    expect(page.locator('[data-stat="row-percents"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )
    page.click('[data-stat="charts"]')
    expect(page.locator('[data-stat="charts"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )
    page.click('[data-stat="overall"]')
    expect(page.locator('[data-stat="overall"]')).to_have_attribute(
        "aria-pressed", "true", timeout=UI_TIMEOUT
    )
    page.click('[data-stat="secondary"][data-value="0.9"]')
    expect(page.locator('[data-stat="secondary"][data-value="0.9"]')).to_have_attribute(
        "aria-checked", "true", timeout=UI_TIMEOUT
    )


def test_question_can_keep_its_own_output_set(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='SCORE'] .question-cell")
    expect(page.locator("#question-output")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#question-output-list")).to_be_hidden()
    page.check("#question-output-own")
    page.uncheck("[data-output-metric='distribution']")
    page.click("#save-question")
    expect(page.locator("#toast-container")).to_contain_text(
        "Настройки вопроса сохранены", timeout=UI_TIMEOUT
    )
    expect(page.locator("#question-output-own")).to_be_checked()
    expect(page.locator("[data-output-metric='distribution']")).not_to_be_checked()


def test_logic_variable_is_built_from_rules(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Логическая переменная собирается из правил тем же редактором, что фильтр."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.select_option("#logic-variables", "new")
    expect(page.locator("#recode-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#recode-mode")).to_have_value("conditions")
    expect(page.locator("#recode-source-field")).to_be_hidden()
    page.fill("#recode-code", "SEXSEG")
    page.fill("#recode-name", "Пол по правилу")

    categories = page.locator("#condition-category-list .condition-category")
    expect(categories).to_have_count(2)
    for index, (label, answer) in enumerate([("Мужчины", "Мужчина"), ("Женщины", "Женщина")]):
        category = categories.nth(index)
        category.locator(".condition-category-label").fill(label)
        category.locator("select.filter-source").select_option("question:SEX")
        category.locator(".filter-option", has_text=answer).locator("input").check()

    page.click("#save-recoding")
    expect(page.locator("#toast-container")).to_contain_text(
        "Перекодировка сохранена", timeout=UI_TIMEOUT
    )
    expect(page.locator("#recode-preview")).to_contain_text("120", timeout=UI_TIMEOUT)
    expect(page.locator("#logic-variables option", has_text="SEXSEG")).to_have_count(1)


def test_project_library_renames_copies_trashes_and_restores(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    page.click("#new-project")
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)

    cards = page.locator("#project-list .project-card")
    expect(cards).to_have_count(1, timeout=UI_TIMEOUT)
    page.click("[data-project-rename]")
    page.fill(".project-rename-form input", "Трекер")
    page.click(".project-rename-form button[type='submit']")
    expect(cards.first).to_contain_text("Трекер", timeout=UI_TIMEOUT)

    page.click("[data-project-copy]")
    expect(cards).to_have_count(2, timeout=UI_TIMEOUT)
    page.fill("#project-search", "копия")
    expect(cards).to_have_count(1)

    page.click("[data-project-trash]")
    expect(page.locator("#project-trash-toggle")).to_have_text("Корзина · 1", timeout=UI_TIMEOUT)
    page.fill("#project-search", "")
    expect(cards).to_have_count(1)

    page.click("#project-trash-toggle")
    expect(cards).to_have_count(1)
    page.click("[data-project-restore]")
    expect(page.locator("#project-trash-toggle")).to_have_text("Корзина", timeout=UI_TIMEOUT)
    page.click("#project-trash-toggle")
    expect(cards).to_have_count(2, timeout=UI_TIMEOUT)


def test_unsaved_question_edits_are_not_lost_silently(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Правка в редакторе не теряется молча: закрытие и переход спрашивают (P2)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    expect(page.locator("#question-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#question-label", "Подпись, которую не сохранили")

    # Отказ оставляет редактор открытым и ввод на месте.
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.click("#close-editor")
    expect(page.locator("#question-editor")).to_be_visible()
    expect(page.locator("#question-label")).to_have_value("Подпись, которую не сохранили")

    # Переход к другому вопросу спрашивает так же.
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.click("#table-body tr[data-code='SEX'] .question-cell")
    expect(page.locator("#question-label")).to_have_value("Подпись, которую не сохранили")

    # Согласие закрывает без сохранения.
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#close-editor")
    expect(page.locator("#question-editor")).to_be_hidden(timeout=UI_TIMEOUT)
    expect(page.locator("#table-body tr[data-code='BRAND']")).not_to_contain_text(
        "Подпись, которую не сохранили"
    )

    # Открытый без правок редактор закрывается без вопроса.
    page.click("#table-body tr[data-code='SEX'] .question-cell")
    page.click("#close-editor")
    expect(page.locator("#question-editor")).to_be_hidden(timeout=UI_TIMEOUT)


def test_several_questions_are_excluded_at_once(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.check("#table-body tr[data-code='BRAND'] .select-question")
    page.check("#table-body tr[data-code='SEX'] .select-question")
    expect(page.locator("#bulk-bar")).to_be_visible()
    expect(page.locator("#bulk-count")).to_have_text("Выбрано: 2")
    # Флажок не открывает карточку вопроса.
    expect(page.locator("#question-editor")).to_be_hidden()

    page.click('#bulk-bar [data-bulk="exclude"]')
    for code in ("BRAND", "SEX"):
        expect(page.locator(f"#table-body tr[data-code='{code}'] .status")).to_have_text(
            "Исключён", timeout=UI_TIMEOUT
        )

    page.select_option("#bulk-type", "open_text")
    for code in ("BRAND", "SEX"):
        expect(
            page.locator(f"#table-body tr[data-code='{code}'] .type-icon")
        ).to_have_attribute("aria-label", "Открытый текст", timeout=UI_TIMEOUT)
    page.click('#bulk-bar [data-bulk="undo"]')
    expect(
        page.locator("#table-body tr[data-code='SEX'] .type-icon")
    ).not_to_have_attribute("aria-label", "Открытый текст", timeout=UI_TIMEOUT)
    expect(page.locator("#bulk-undo")).to_be_hidden()

    page.check("#select-all-questions")
    expect(page.locator("#bulk-count")).to_have_text("Выбрано: 5")
    page.click('#bulk-bar [data-bulk="clear"]')
    expect(page.locator("#bulk-bar")).to_be_hidden()


def test_separate_questions_are_grouped_and_split_back(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Ручная сборка группы (PQ.9): слоты без общего префикса — в один вопрос."""
    source = tmp_path / "slots.sav"
    brands = {1: "Альфа", 2: "Бета", 3: "Гамма"}
    pyreadstat.write_sav(
        pd.DataFrame({"FIRST": [1, 2, 1, 3, 2, 1], "SECOND": [2, 3, 3, 1, 1, 2]}),
        source,
        column_labels={"FIRST": "Марки: первая", "SECOND": "Марки: вторая"},
        variable_value_labels={"FIRST": brands, "SECOND": brands},
        variable_measure={"FIRST": "nominal", "SECOND": "nominal"},
    )
    _open_project(page, live_server, source)

    page.check("#table-body tr[data-code='FIRST'] .select-question")
    page.check("#table-body tr[data-code='SECOND'] .select-question")
    page.select_option("#bulk-group", "multiple_choice_categorical")
    expect(page.locator("#toast-container")).to_contain_text("собраны в", timeout=UI_TIMEOUT)
    expect(page.locator("#table-body tr[data-code='FIRST']")).to_have_count(0)

    group = page.locator("#table-body tr", has_text="Марки").first
    group.locator(".question-cell").click()
    expect(page.locator("#question-members")).to_contain_text(
        "Состав блока · 2", timeout=UI_TIMEOUT
    )

    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#question-members [data-ungroup]")
    expect(page.locator("#table-body tr[data-code='FIRST']")).to_have_count(1, timeout=UI_TIMEOUT)
    expect(page.locator("#table-body tr[data-code='SECOND']")).to_have_count(1)


@pytest.mark.parametrize("encoding", ["rank_per_item", "item_per_rank"])
def test_ranking_group_preview_and_saved_settings(
    page: Page, live_server: str, tmp_path: Path, encoding: str
) -> None:
    from tests.test_ranking import SOURCES, write_ranking

    source = tmp_path / "ranks.sav"
    write_ranking(source, encoding)
    _open_project(page, live_server, source)
    for code in SOURCES:
        page.check(f"#table-body tr[data-code='{code}'] .select-question")
    page.select_option("#bulk-group", f"ranking:{encoding}")
    expect(page.locator("#toast-container")).to_contain_text("собраны в", timeout=UI_TIMEOUT)
    page.locator("#table-body tr[data-code='ALPHA_grp'] .question-cell").click()
    expect(page.locator("#ranking-encoding")).to_have_value(encoding)
    expect(page.locator("#preview-content")).to_contain_text("Альфа", timeout=UI_TIMEOUT)

    page.click("#close-editor")
    _open_view(page, "tables")
    page.click('.bld-param[data-zone="rows"]')
    page.click('#bld-list .bld-var[data-code="ALPHA_grp"]')
    page.keyboard.press("Escape")
    table = page.locator("#bld-grid-wrap table.bld-grid")
    expect(table).to_contain_text("Средний ранг", timeout=UI_TIMEOUT)
    first_rank = table.locator("tbody tr", has_text="Место 1").first
    expect(first_rank.locator("td.bld-val").first).to_have_text("40")
    page.screenshot(path=str(tmp_path / "ranking-table.png"), full_page=True)

    _open_view(page, "data")
    page.locator("#table-body tr[data-code='ALPHA_grp'] .question-cell").click()
    expect(page.locator("#preview-content .base-line")).to_contain_text("Валидная база 4")
    page.locator("#preview-content details").first.click()
    expect(page.locator("#preview-content details").first).to_contain_text("Место 1")
    page.fill("#question-label", "Строгое ранжирование")
    page.click("#save-question")
    expect(page.locator("#table-body tr[data-code='ALPHA_grp']")).to_contain_text(
        "Строгое ранжирование", timeout=UI_TIMEOUT
    )
    page.reload()
    expect(page.locator("#table-body tr[data-code='ALPHA_grp']")).to_be_visible(timeout=UI_TIMEOUT)
    page.locator("#table-body tr[data-code='ALPHA_grp'] .question-cell").click()
    expect(page.locator("#ranking-encoding")).to_have_value(encoding)
    expect(page.locator("#preview-content")).to_contain_text("Альфа", timeout=UI_TIMEOUT)


def test_csv_upload_opens_a_project(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.csv"
    source.write_bytes("Пол;Оценка\nМужчина;4\nЖенщина;5\nЖенщина;3\n".encode("utf-8-sig"))
    page.goto(live_server)
    expect(page.locator("#start")).to_be_visible(timeout=UI_TIMEOUT)
    page.set_input_files("#file", str(source))
    page.click("#submit")
    expect(page.locator("#workspace")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#table-body")).to_contain_text("Пол", timeout=UI_TIMEOUT)


def test_formula_is_checked_saved_and_opened_as_a_question(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#new-formula")
    expect(page.locator("#formula-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#formula-name", "DOUBLE")
    page.fill("#formula-label", "Двойная оценка")
    page.fill("#formula-expression", "NOPE * 2")
    page.click("#check-formula")
    expect(page.locator("#formula-preview")).to_contain_text("NOPE", timeout=UI_TIMEOUT)

    page.fill("#formula-expression", "")
    page.locator("#formula-variables button").first.click()
    page.locator("#formula-expression").press("End")
    page.locator("#formula-expression").type(" * 2")
    page.click("#check-formula")
    expect(page.locator("#formula-preview")).to_contain_text("Посчитано", timeout=UI_TIMEOUT)

    page.click("#save-formula")
    expect(page.locator("#question-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#question-formula")).to_contain_text("* 2")
    expect(page.locator("#table-body tr[data-code='DOUBLE']")).to_be_visible()

    page.click("#question-formula [data-open-formula]")
    expect(page.locator("#formula-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#formula-name")).to_have_js_property("readOnly", True)


def test_banner_category_can_be_hidden_and_renamed(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    page.click('[data-block="banner"] [data-new="banner"]')
    expect(page.locator("#banner-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#banner-name", "Пол")
    page.locator("#banner-block-list select").first.select_option("question:SEX")
    page.locator("#banner-block-list .banner-categories summary").first.click()
    rows = page.locator("#banner-block-list .banner-category")
    expect(rows).to_have_count(2, timeout=UI_TIMEOUT)
    rows.nth(0).locator(".banner-category-shown").uncheck()
    rows.nth(1).locator(".banner-category-label").fill("Женщины")
    page.click("#save-banner")
    expect(page.locator("#banner-preview")).to_contain_text("Женщины", timeout=UI_TIMEOUT)
    expect(page.locator("#banner-preview")).not_to_contain_text("Мужчина")
    expect(page.locator("#banner-preview-count")).to_have_text("2 колонок")



def test_banner_categories_are_merged_into_one_column(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Категории с одной группой выводятся одной колонкой — без перекодировки (PQ.5)."""
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    page.click('[data-block="banner"] [data-new="banner"]')
    expect(page.locator("#banner-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#banner-name", "Марка")
    page.locator("#banner-block-list select").first.select_option("question:BRAND")
    page.locator("#banner-block-list .banner-categories summary").first.click()
    rows = page.locator("#banner-block-list .banner-category")
    expect(rows).to_have_count(2, timeout=UI_TIMEOUT)
    rows.nth(0).locator(".banner-category-group").fill("Любая марка")
    rows.nth(1).locator(".banner-category-group").fill("Любая марка")
    page.click("#save-banner")
    expect(page.locator("#banner-preview")).to_contain_text("Любая марка", timeout=UI_TIMEOUT)
    expect(page.locator("#banner-preview")).not_to_contain_text("Первая")
    expect(page.locator("#banner-preview-count")).to_have_text("2 колонок")


def test_derived_sav_downloads_from_the_export_menu(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    target = _download_artifact(page, "#download-derived-sav", tmp_path / "derived.sav")

    _, meta = pyreadstat.read_sav(target, metadataonly=True)
    assert meta.column_names == ["ID", "SEX", "AGE", "BRAND", "SCORE"]


def test_ranges_are_suggested_from_the_data(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click("#table-body tr[data-code='AGE'] .question-cell")
    page.locator("#question-recodings [data-new-recoding]").click()
    expect(page.locator("#recode-editor")).to_be_visible(timeout=UI_TIMEOUT)
    page.select_option("#range-method", "quantiles")
    page.fill("#range-groups", "3")
    page.click("#suggest-ranges")
    expect(page.locator("#range-list .range-row")).to_have_count(3, timeout=UI_TIMEOUT)
    expect(page.locator("#range-suggest-note")).to_contain_text("Респондентов в группах")



def test_question_settings_are_copied_from_the_bulk_bar(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.check("#table-body tr[data-code='SEX'] .select-question")
    page.check("#table-body tr[data-code='BRAND'] .select-question")
    expect(page.locator("#bulk-copy option[value='SEX']")).to_have_count(1, timeout=UI_TIMEOUT)
    page.once("dialog", lambda dialog: dialog.accept())
    page.select_option("#bulk-copy", "SEX")
    expect(page.locator("#toast-container")).to_contain_text(
        "Настройки SEX перенесены", timeout=UI_TIMEOUT
    )


def test_selected_questions_move_as_a_block_and_the_move_can_be_undone(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    rows = page.locator("#table-body tr[data-code]")
    expect(rows).to_have_count(5, timeout=UI_TIMEOUT)
    original = [rows.nth(index).get_attribute("data-code") for index in range(5)]

    page.check("#table-body tr[data-code='BRAND'] .select-question")
    page.check("#table-body tr[data-code='SCORE'] .select-question")
    page.click('#bulk-bar [data-bulk="move-up"]')
    moved = [code for code in original if code not in {"BRAND", "SCORE"}]
    expected = original.copy()
    for code in ("BRAND", "SCORE"):
        index = expected.index(code)
        if index and expected[index - 1] not in {"BRAND", "SCORE"}:
            expected[index - 1], expected[index] = expected[index], expected[index - 1]
    expect(rows.nth(0)).to_have_attribute("data-code", expected[0], timeout=UI_TIMEOUT)
    expect(page.locator("#table-body tr[data-code]")).to_have_count(5)
    for index, code in enumerate(expected):
        expect(rows.nth(index)).to_have_attribute("data-code", code, timeout=UI_TIMEOUT)
    assert moved

    page.click('#bulk-bar [data-bulk="undo"]')
    for index, code in enumerate(original):
        expect(rows.nth(index)).to_have_attribute("data-code", code, timeout=UI_TIMEOUT)

    # «Собрать вместе» ставит выбранные подряд с места первого из них.
    assert page.evaluate(
        "shiftedQuestionOrder(['A', 'B', 'C', 'D', 'E'], new Set(['B', 'D']), 'gather')"
    ) == ["A", "B", "D", "C", "E"]
    assert page.evaluate(
        "shiftedQuestionOrder(['A', 'B', 'C'], new Set(['A']), 'move-up')"
    ) == ["A", "B", "C"]


def test_analysis_card_shows_the_test_chosen_by_types(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    page.click(".tabs button[data-view='analysis']")
    expect(page.locator("#section-analysis")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#analysis-cards")).to_contain_text("Карточек пока нет")
    page.select_option("#analysis-a", "question:SEX")
    page.select_option("#analysis-b", "question:BRAND")
    page.click("#add-analysis-card")
    card = page.locator("#analysis-cards .analysis-card").first
    expect(card).to_contain_text("Хи-квадрат Пирсона", timeout=UI_TIMEOUT)
    expect(card).to_contain_text("p с поправкой BH")

    card.locator("[data-delete-card]").click()
    expect(page.locator("#analysis-cards")).to_contain_text("Карточек пока нет", timeout=UI_TIMEOUT)

    # Карточка переменной: что в переменной есть до поиска связей.
    page.select_option("#variable-source", "question:AGE")
    page.click("#describe-variable")
    expect(page.locator("#variable-body")).to_contain_text("Медиана", timeout=UI_TIMEOUT)
    page.select_option("#variable-source", "question:SEX")
    page.click("#describe-variable")
    expect(page.locator("#variable-body")).to_contain_text("Мужчина", timeout=UI_TIMEOUT)


def test_open_answers_are_coded_by_query_and_by_hand(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "open.sav"
    # Номер в конце делает ответы разными: иначе столбец похож на закрытый вопрос.
    answers = [
        f"{text} (анкета {index})"
        for index, text in enumerate(
            [
                "Очень доволен доставкой",
                "Доставку привезли быстро",
                "Дорого, но качественно",
                "Цены высокие",
                "Ничего не понравилось",
            ]
            * 8
        )
    ]
    pyreadstat.write_sav(
        pd.DataFrame({"ID": list(range(1, len(answers) + 1)), "WHY": answers}),
        source,
        column_labels={"ID": "Номер", "WHY": "Почему вы так оценили?"},
    )
    _open_project(page, live_server, source)
    _open_view(page, "text")
    expect(page.locator("#section-text")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#text-question option[value='WHY']")).to_have_count(1, timeout=UI_TIMEOUT)

    page.click("#create-codeframe")
    expect(page.locator("#coding-body")).to_be_visible(timeout=UI_TIMEOUT)
    page.click("#add-theme")
    row = page.locator("#theme-list .theme-row").last
    row.locator(".theme-name").fill("Доставка")
    row.locator(".theme-queries").fill("доставка")
    page.click("#save-themes")
    expect(page.locator("#coding-stats")).to_contain_text("без темы 24", timeout=UI_TIMEOUT)
    expect(page.locator("#theme-list .theme-count").first).to_have_text("16", timeout=UI_TIMEOUT)

    page.select_option("#answer-filter", "uncoded")
    expect(page.locator("#answer-count")).to_contain_text("из 24", timeout=UI_TIMEOUT)
    first = page.locator("#answer-list .answer-row").first
    first.locator(".answer-add").select_option(label="Доставка")
    expect(page.locator("#coding-stats")).to_contain_text("без темы 23", timeout=UI_TIMEOUT)
    expect(page.locator("#theme-list .theme-count").first).to_contain_text("вручную 1")

    # Кодификатор другой волны: новые темы добавляются, совпадающие пропускаются.
    codeframe = tmp_path / "codeframe.json"
    codeframe.write_text(
        '{"format": "sav-analytics/codeframe", "version": 1, "label": "x", "themes": ['
        '{"id": "a", "name": "Доставка", "parent_id": null, "queries": ["доставка"]},'
        '{"id": "b", "name": "Цена", "parent_id": null, "queries": ["цен*"]}]}',
        encoding="utf-8",
    )
    page.set_input_files("#import-codeframe", str(codeframe))
    expect(page.locator("#theme-list .theme-row")).to_have_count(2, timeout=UI_TIMEOUT)
    page.click("#save-themes")
    expect(page.locator("#coding-stats")).to_contain_text("без темы 15", timeout=UI_TIMEOUT)



def test_header_preview_shows_columns_and_bases_before_building(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "reports")

    # Без баннера превью честно говорит, что в книге будет только Total.
    page.click("#header-preview summary")
    expect(page.locator("#header-preview-body")).to_contain_text(
        "только колонка Total", timeout=UI_TIMEOUT
    )

    page.click('[data-block="banner"] [data-new="banner"]')
    page.fill("#banner-name", "Пол")
    page.locator("#banner-block-list select").first.select_option("question:SEX")
    page.click("#save-banner")
    expect(page.locator("#banner-preview")).to_contain_text("Мужчина", timeout=UI_TIMEOUT)
    page.click("#close-banner-editor")

    page.click("#header-preview summary")
    page.click("#header-preview summary")
    expect(page.locator("#header-preview-body")).to_contain_text("Мужчина", timeout=UI_TIMEOUT)
    expect(page.locator(".header-preview-row").first).to_contain_text("Total")


def test_table_row_draws_a_chart_of_the_same_numbers(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "tables")

    page.click('.bld-param[data-zone="rows"]')
    page.click('#bld-list .bld-var[data-code="BRAND"]')
    page.keyboard.press("Escape")
    expect(page.locator("#bld-grid-wrap table.bld-grid")).to_be_visible(timeout=UI_TIMEOUT)

    page.locator("#bld-grid-wrap .bld-rowchart").first.click()
    chart = page.locator("#bld-chart")
    expect(chart).to_be_visible(timeout=UI_TIMEOUT)
    expect(chart.locator("svg rect")).to_have_count(1)
    expect(chart.locator("svg text").nth(1)).to_have_text(
        page.locator("#bld-grid-wrap tbody .bld-val").first.inner_text()
    )
    page.click("#bld-chart-close")
    expect(chart).to_be_hidden()


def test_net_group_is_built_on_the_table_screen_without_saving(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)
    _open_view(page, "tables")

    page.click('.bld-param[data-zone="rows"]')
    page.click('#bld-list .bld-var[data-code="BRAND"]')
    page.keyboard.press("Escape")
    expect(page.locator("#bld-grid-wrap table.bld-grid")).to_be_visible(timeout=UI_TIMEOUT)

    # Доли, Top/Bottom и NET живут в меню «Вид»: полоса параметров держится
    # в одну строку, а эти три настройки меняют вид уже посчитанного.
    page.click("#bld-view")
    expect(page.locator("#bld-net-row")).to_be_visible(timeout=UI_TIMEOUT)
    page.click("#bld-net")
    expect(page.locator("#bld-net-add")).to_be_visible(timeout=UI_TIMEOUT)
    page.fill("#bld-net-label", "Любая марка")
    page.locator(".bld-net-values input").first.check()
    page.locator(".bld-net-values input").nth(1).check()
    page.click("#bld-net-add")
    expect(page.locator("#bld-grid-wrap")).to_contain_text("NET: Любая марка", timeout=UI_TIMEOUT)
    page.click("#bld-view")
    expect(page.locator("#bld-net")).to_have_text("NET · 1")
    page.keyboard.press("Escape")

    # Вопрос в структуре не изменился: группа живёт только на экране.
    _open_view(page, "data")
    page.click("#table-body tr[data-code='BRAND'] .question-cell")
    expect(page.locator("#question-editor")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#net-list")).not_to_contain_text("Любая марка")


def test_new_wave_shows_the_diff_and_replaces_data(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    source = tmp_path / "survey.sav"
    _write_survey(source)
    _open_project(page, live_server, source)

    second = tmp_path / "wave2.sav"
    size = 120
    pyreadstat.write_sav(
        pd.DataFrame(
            {
                "ID": range(1, size + 1),
                "SEX": [1 if index % 2 else 2 for index in range(size)],
                "AGE": [18 + (index * 5) % 50 for index in range(size)],
                "BRAND": [1 if index % 3 else 2 for index in range(size)],
                "SCORE": [index % 11 for index in range(size)],
                "CITY": [1 for _ in range(size)],
            }
        ),
        second,
        column_labels={
            "ID": "Номер интервью",
            "SEX": "Ваш пол",
            "AGE": "Возраст, полных лет",
            "BRAND": "Какой маркой пользуетесь",
            "SCORE": "Готовность рекомендовать",
            "CITY": "Город",
        },
        variable_value_labels={
            "SEX": {1: "Мужчина", 2: "Женщина"},
            "BRAND": {1: "Первая", 2: "Вторая"},
        },
    )

    page.click("#export-toggle")
    page.set_input_files("#wave-file", str(second))
    expect(page.locator("#wave-sheet")).to_be_visible(timeout=UI_TIMEOUT)
    expect(page.locator("#wave-body")).to_contain_text("CITY", timeout=UI_TIMEOUT)
    expect(page.locator("#wave-body")).to_contain_text("станет 120")

    page.click("#wave-apply")
    expect(page.locator("#toast-container")).to_contain_text(
        "Данные проекта заменены", timeout=UI_TIMEOUT
    )
    expect(page.locator("#table-body tr[data-code='CITY']")).to_be_visible(timeout=UI_TIMEOUT)
