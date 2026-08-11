from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pandas as pd
import pyreadstat
import pytest

from sav_analytics.core.not_applicable import suggest_not_applicable_codes
from sav_analytics.core.report import build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _write_skip_fixture(path: Path) -> None:
    """Заглушка «не применимо», как её пишет Росстат: код 0 без подписи.

    Занятость спрашивают только у 60 из 100; остальным в обе переменные блока
    записан 0, которого нет среди подписанных кодов 11 и 41.
    """
    frame = pd.DataFrame(
        {
            "STATUS": [1] * 60 + [2] * 40,
            "ST_TRUD": [11] * 20 + [41] * 40 + [0] * 40,
            "TIP_DG": [1] * 20 + [2] * 40 + [0] * 40,
            "SCORE": [7] * 50 + [9] * 50,
        }
    )
    pyreadstat.write_sav(
        frame,
        path,
        column_labels={
            "STATUS": "Экономическая активность",
            "ST_TRUD": "Статус в сфере труда",
            "TIP_DG": "Вид оформления",
            "SCORE": "Оценка",
        },
        variable_value_labels={
            "STATUS": {1: "Занятые", 2: "Не занятые"},
            "ST_TRUD": {11: "Работодатели", 41: "Наемные работники"},
            "TIP_DG": {1: "Договор", 2: "Устно"},
            "SCORE": {value: str(value) for value in range(11)},
        },
        variable_measure={
            "STATUS": "nominal", "ST_TRUD": "nominal",
            "TIP_DG": "nominal", "SCORE": "scale",
        },
    )


def _project(source: Path, **marks: list) -> dict:
    inspection = inspect_sav(source).to_dict()
    questions = []
    for question in inspection["questions"]:
        item = dict(question)
        if question["code"] in marks:
            item["not_applicable_values"] = marks[question["code"]]
        questions.append(item)
    return {
        "name": "Пропуск по ветке",
        "inspection": inspection,
        "configuration": {
            "questions": questions,
            "recodings": [], "filters": [], "banners": [], "report_filter_id": None,
        },
    }


def _rows(content: bytes, sheet_index: int) -> list[tuple[str, str]]:
    with ZipFile(BytesIO(content)) as archive:
        shared = [
            "".join(node.itertext())
            for node in ElementTree.fromstring(
                archive.read("xl/sharedStrings.xml")
            ).findall("m:si", NS)
        ]
        sheet = ElementTree.fromstring(archive.read(f"xl/worksheets/sheet{sheet_index}.xml"))
    result = []
    for row in sheet.findall("m:sheetData/m:row", NS):
        cells = {cell.attrib["r"][0]: cell for cell in row.findall("m:c", NS)}
        if "A" not in cells:
            continue
        raw = cells["A"].findtext("m:v", default="", namespaces=NS)
        label = shared[int(raw)] if cells["A"].get("t") == "s" and raw else raw
        value = cells["B"].findtext("m:v", default="", namespaces=NS) if "B" in cells else ""
        result.append((label, value))
    return result


def test_unmarked_code_stays_a_category(tmp_path: Path) -> None:
    """Без пометки заглушка остаётся строкой отчёта — это исходное поведение."""
    source = tmp_path / "skip.sav"
    _write_skip_fixture(source)

    rows = _rows(build_topline_xlsx(source, _project(source)), 1)

    assert any(label == "0.0" for label, _ in rows)


def test_marked_code_leaves_the_distribution_but_stays_in_the_total_base(
    tmp_path: Path,
) -> None:
    source = tmp_path / "skip.sav"
    _write_skip_fixture(source)
    # Помечается весь блок: заглушка стоит у одних и тех же респондентов в обеих
    # переменных, и в отчёте не должно остаться ни одной строки «0.0».
    project = _project(source, ST_TRUD=[0], TIP_DG=[0])

    rows = _rows(build_topline_xlsx(source, project), 1)
    wanted = {"Работодатели", "Наемные работники"}
    shares = {label: value for label, value in rows if label in wanted}

    assert not any(label == "0.0" for label, _ in rows)
    # 20 и 40 из полной базы 100: помеченные респонденты остались в знаменателе,
    # поэтому доли дают 60%, а не 100%.
    assert float(shares["Работодатели"]) == pytest.approx(20)
    assert float(shares["Наемные работники"]) == pytest.approx(40)


def test_marked_question_reaches_the_filter_sheet_with_a_valid_base(
    tmp_path: Path,
) -> None:
    source = tmp_path / "skip.sav"
    _write_skip_fixture(source)

    without = _rows(build_topline_xlsx(source, _project(source)), 2)
    assert not any("ST_TRUD" in label for label, _ in without)

    rows = _rows(build_topline_xlsx(source, _project(source, ST_TRUD=[0])), 2)
    labels = {label: value for label, value in rows}

    assert any("ST_TRUD" in label for label in labels)
    assert float(labels["Валидная база, N"]) == 60
    assert float(labels["Работодатели"]) == pytest.approx(100 * 20 / 60)
    assert float(labels["Наемные работники"]) == pytest.approx(100 * 40 / 60)


def test_suggestions_group_questions_sharing_the_same_respondents(
    tmp_path: Path,
) -> None:
    source = tmp_path / "skip.sav"
    _write_skip_fixture(source)

    groups = suggest_not_applicable_codes(source, _project(source))

    assert len(groups) == 1
    group = groups[0]
    assert group.respondents == 40
    assert group.share == pytest.approx(0.4)
    assert {item.question_code for item in group.candidates} == {"ST_TRUD", "TIP_DG"}
    assert all(item.value == 0 for item in group.candidates)
    assert all(not item.already_marked for item in group.candidates)


def test_suggestions_mark_what_is_already_confirmed(tmp_path: Path) -> None:
    source = tmp_path / "skip.sav"
    _write_skip_fixture(source)

    groups = suggest_not_applicable_codes(source, _project(source, ST_TRUD=[0]))

    marked = {item.question_code: item.already_marked for item in groups[0].candidates}
    assert marked == {"ST_TRUD": True, "TIP_DG": False}


def test_labelled_zero_is_never_suggested(tmp_path: Path) -> None:
    """Ноль со своей подписью — осмысленный ответ, а не заглушка."""
    source = tmp_path / "labelled_zero.sav"
    frame = pd.DataFrame({"NPS": [0] * 30 + [7] * 30 + [10] * 40})
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"NPS": "Рекомендация"},
        variable_value_labels={"NPS": {value: str(value) for value in range(11)}},
        variable_measure={"NPS": "scale"},
    )

    assert suggest_not_applicable_codes(source, _project(source)) == []


def test_variable_without_labels_is_never_suggested(tmp_path: Path) -> None:
    """Ноль часов и ноль-заглушка по данным неразличимы — правило молчит."""
    source = tmp_path / "hours.sav"
    frame = pd.DataFrame({"HOURS": [0] * 30 + [40] * 70})
    pyreadstat.write_sav(
        frame, source, column_labels={"HOURS": "Часов в неделю"},
        variable_measure={"HOURS": "scale"},
    )

    assert suggest_not_applicable_codes(source, _project(source)) == []


def test_unlabelled_code_inside_the_labelled_range_is_not_suggested(
    tmp_path: Path,
) -> None:
    """Код между подписанными — скорее потерянная подпись, чем заглушка."""
    source = tmp_path / "gap.sav"
    frame = pd.DataFrame({"Q": [1] * 40 + [3] * 20 + [5] * 40})
    pyreadstat.write_sav(
        frame, source, column_labels={"Q": "Оценка"},
        variable_value_labels={"Q": {1: "Низкая", 5: "Высокая"}},
        variable_measure={"Q": "nominal"},
    )

    assert suggest_not_applicable_codes(source, _project(source)) == []
