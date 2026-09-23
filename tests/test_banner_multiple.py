"""Multiple-response в колонках баннера (PQ.5)."""

from pathlib import Path

import pandas as pd
import pyreadstat

from sav_analytics.core.banner import BannerError, calculate_banner_preview
from sav_analytics.core.report import build_statistics_txt, build_topline_xlsx
from sav_analytics.core.sav_reader import inspect_sav
from tests.test_report import _cell_value, _row_labels


def _project(source: Path, **settings: object) -> dict:
    frame = pd.DataFrame(
        {
            # 40 выбрали только A, 40 — только B, 40 — оба варианта, 30 — ничего.
            "BUY_1": [1] * 40 + [0] * 40 + [1] * 40 + [0] * 30,
            "BUY_2": [0] * 40 + [1] * 40 + [1] * 40 + [0] * 30,
            "OUTCOME": ([1] * 30 + [2] * 10) + ([1] * 10 + [2] * 30) + ([1] * 20 + [2] * 20)
            + [1] * 15 + [2] * 15,
        }
    )
    pyreadstat.write_sav(
        frame,
        source,
        column_labels={"BUY_1": "Марка A", "BUY_2": "Марка B", "OUTCOME": "Результат"},
        variable_value_labels={"OUTCOME": {1: "Да", 2: "Нет"}},
        variable_measure={"BUY_1": "nominal", "BUY_2": "nominal", "OUTCOME": "nominal"},
    )
    inspection = inspect_sav(source).to_dict()
    outcome = next(item for item in inspection["questions"] if item["code"] == "OUTCOME")
    buy = {
        "code": "BUY",
        "label": "Какие марки покупали",
        "question_type": "multiple_choice_dichotomy",
        "role": "question",
        "source_variables": ["BUY_1", "BUY_2"],
        "valid_count": 150,
        "missing_count": 0,
        "included_in_report": False,
        "multiple_response": {"encoding": "dichotomy", "counted_value": 1},
    }
    return {
        "name": "Multiple в баннере",
        "inspection": inspection,
        "configuration": {
            "questions": [outcome, buy],
            "recodings": [],
            "filters": [],
            "report_filter_id": None,
            "banners": [
                {
                    "name": "Марки",
                    "blocks": [{"label": "Марки", "sources": [{"kind": "question", "ref": "BUY"}]}],
                }
            ],
            "report_settings": {
                "confidence_level": 0.95,
                "bonferroni": False,
                "minimum_base": 30,
                "compare_to_total": True,
                "compare_pairwise": True,
                "show_p_values": True,
                **settings,
            },
        },
    }


def test_multiple_response_columns_are_the_ones_who_chose_each_answer(tmp_path: Path) -> None:
    source = tmp_path / "multiple.sav"
    project = _project(source)

    preview = calculate_banner_preview(
        source, project["configuration"]["banners"][0], project
    )

    assert [column["base"] for column in preview["columns"]] == [150, 80, 80]
    assert preview["overlaps"] == [{"block_index": 0, "block": "Марки", "respondents": 40}]

    content = build_topline_xlsx(source, project)
    assert "Да" in _row_labels(content)
    # «Да» у выбравших A: 30 + 20 из 80.
    assert _cell_value(content, "Да", "C") == 50 / 80 * 100


def test_overlapping_columns_are_compared_only_with_the_rest(tmp_path: Path) -> None:
    source = tmp_path / "multiple.sav"
    audit = build_statistics_txt(source, _project(source))

    assert "Колонки блока пересекаются" in audit
    assert "Rest(" in audit or "остальн" in audit.lower()


def test_multiple_without_counted_value_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "multiple.sav"
    project = _project(source)
    project["configuration"]["questions"][1]["multiple_response"] = {
        "encoding": "dichotomy",
        "counted_value": None,
    }

    try:
        calculate_banner_preview(source, project["configuration"]["banners"][0], project)
    except BannerError as exc:
        assert "код выбранного ответа" in str(exc)
    else:
        raise AssertionError("баннер без кода выбранного ответа должен отклоняться")


def test_skip_reasons_mode_notes_only_the_tests_that_were_not_run(tmp_path: Path) -> None:
    """«Причины пропуска в примечании» — без полного протокола (§9.1)."""
    from tests.test_overall_tests import _cell_comment

    source = tmp_path / "multiple.sav"
    quiet = build_topline_xlsx(source, _project(source, show_p_values=False))
    reasons = build_topline_xlsx(
        source, _project(source, show_p_values=False, note_skip_reasons=True)
    )

    assert "пересекаются" not in (_cell_comment(quiet, "Да", "C") or "")
    note = _cell_comment(reasons, "Да", "C") or ""
    assert "пересекаются" in note
    # Выполненный тест с остатком в режим причин не попадает.
    assert "p-value=" not in note
