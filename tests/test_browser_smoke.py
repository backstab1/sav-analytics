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

    # Непостроенный раздел говорит, что в нём будет, и не показывает чисел.
    _open_view(page, "analysis")
    expect(page.locator("#section-soon")).to_contain_text("Раздел строится", timeout=UI_TIMEOUT)
    expect(page.locator("#section-tables")).to_be_hidden()

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

