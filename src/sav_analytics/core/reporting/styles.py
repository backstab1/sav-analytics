from __future__ import annotations

from typing import Any

from ..statistics import StatisticalTestResult


def _result_format(
    formats: dict[str, Any],
    family: str,
    base: int,
    result: StatisticalTestResult | None,
    settings: dict[str, Any],
    wave_result: StatisticalTestResult | None = None,
) -> Any:
    key = family
    if 0 < base < settings["minimum_base"]:
        key = f"{family}_small"
    elif result is not None and result.significant and result.direction in {"higher", "lower"}:
        key = f"{family}_{result.direction}"
    if (
        wave_result is not None
        and wave_result.significant
        and wave_result.direction in {"higher", "lower"}
    ):
        return formats[f"{key}_wave_{wave_result.direction}"]
    return formats[key]

def _formats(workbook: Any) -> dict[str, Any]:
    border = {"bottom": 1, "bottom_color": "#E3E7E3"}
    formats = {
        "title": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 16,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#355B47",
                "align": "left",
                "valign": "vcenter",
            }
        ),
        "banner": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#E7ECE8",
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        ),
        "banner_letter": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#D8E3DC",
                "align": "center",
            }
        ),
        "base": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 9,
                "bold": True,
                "bg_color": "#F1F4F2",
                "align": "center",
                "num_format": "#,##0",
            }
        ),
        "base_label": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "bold": True, "bg_color": "#F1F4F2"}
        ),
        "question": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#4F7D65",
                "text_wrap": True,
                "valign": "vcenter",
            }
        ),
        "subquestion": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "bold": True, "bg_color": "#EDF3EF", **border}
        ),
        "percent": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "num_format": "0", "align": "right", **border}
        ),
        "mean": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "num_format": "0.0", "align": "right", **border}
        ),
        "contents_header": workbook.add_format(
            {
                "font_name": "Arial",
                "font_size": 10,
                "bold": True,
                "bg_color": "#E7ECE8",
                "bottom": 1,
                "bottom_color": "#AAB7AF",
            }
        ),
        "link": workbook.add_format(
            {"font_name": "Arial", "font_size": 9, "font_color": "#355B47", "underline": True}
        ),
    }
    result_fills = {
        "higher": "#D9EAD3",
        "lower": "#F4CCCC",
        "small": "#D9D9D9",
    }
    for family, num_format in (("percent", "0"), ("mean", "0.0")):
        for result, color in result_fills.items():
            formats[f"{family}_{result}"] = workbook.add_format(
                {
                    "font_name": "Arial",
                    "font_size": 9,
                    "num_format": num_format,
                    "align": "right",
                    "bg_color": color,
                    **border,
                }
            )
        for base_name, fill_color in {"": None, **result_fills}.items():
            base_key = family if not base_name else f"{family}_{base_name}"
            for direction, arrow, font_color in (
                ("higher", "↑", "#548235"),
                ("lower", "↓", "#C00000"),
            ):
                properties = {
                    "font_name": "Arial",
                    "font_size": 9,
                    "font_color": font_color,
                    "num_format": f'{num_format}" {arrow}"',
                    "align": "right",
                    **border,
                }
                if fill_color:
                    properties["bg_color"] = fill_color
                formats[f"{base_key}_wave_{direction}"] = workbook.add_format(properties)
    return formats


