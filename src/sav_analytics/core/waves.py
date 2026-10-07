"""Волны отдельными источниками (PQ.19, решение 034).

У каждой волны свой файл `waves/<id>.sav` и своё сопоставление переменных
с первой волной: «переменная проекта ← переменная файла волны». Расчётный
`source.sav` собирается из волн: строки складываются, переменные
переименовываются по сопоставлению, добавляется переменная волны с ролью
«Волна». Поэтому таблицы, баннер, фильтры, вес внутри волн, сравнение с
прошлой волной и кодирование открытых ответов по тексту работают на
сравнении волн без отдельного кода.

Переменной, которой нет в волне, в её строках нет значений: в колонке этой
волны база 0, и тест с ней не считается — «не задавался».

Переменная волны у проекта одна — вопрос с ролью «Волна». Волны бывают
двух видов и не спорят друг с другом: уже размеченные в данных (значения
этой переменной в исходном файле) и подгруженные файлами. Файл волны без
своей переменной волны получает новое значение той же переменной (`code`
у записи волны), а не вторую переменную. Поэтому выбранная волна
(`configuration.wave_view`) — всегда значение одной переменной, откуда бы
волна ни взялась.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pyreadstat

from .questionnaire import value_key

WAVE_DIR = "waves"


class WaveError(ValueError):
    pass


@dataclass
class WaveFile:
    frame: pd.DataFrame
    labels: dict[str, str]
    value_labels: dict[str, dict[Any, str]]
    measures: dict[str, str]
    missing: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def read_wave(path: Path) -> WaveFile:
    frame, meta = pyreadstat.read_sav(
        path, apply_value_formats=False, user_missing=True, dates_as_pandas_datetime=False
    )
    return WaveFile(
        frame=frame,
        labels=dict(zip(meta.column_names, meta.column_labels or [], strict=False)),
        value_labels=dict(meta.variable_value_labels or {}),
        measures=dict(meta.variable_measure or {}),
        missing=dict(meta.missing_ranges or {}),
    )


def _normalized(text: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", str(text or "").lower().replace("ё", "е")).strip()


def base_variables(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Переменные первой волны, которые сопоставляются: без производных и
    без переменной волны, если её завели файлы волн. Размеченная в данных
    переменная волны сопоставляется как обычная: у файла волны может быть
    своя."""
    meta = project.get("waves_meta") or {}
    wave_variable = None if meta.get("in_data") else meta.get("variable")
    return [
        item for item in project["inspection"]["variables"]
        if not item.get("formula_id") and not item.get("codeframe_id")
        and item["name"] != wave_variable
    ]


def wave_variable_of(project: dict[str, Any] | None) -> str | None:
    """Переменная волны проекта: вопрос с ролью «Волна»."""
    for question in ((project or {}).get("configuration") or {}).get("questions", []):
        if question.get("role") == "wave" and len(question.get("source_variables") or []) == 1:
            return str(question["source_variables"][0])
    return None


def wave_values(project: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Волны проекта — подписанные значения переменной волны по порядку кода."""
    variable = wave_variable_of(project)
    if variable is None:
        return []
    record = next(
        (item for item in project["inspection"]["variables"] if item["name"] == variable),  # type: ignore[index]
        None,
    )
    labels = (record or {}).get("value_labels") or []
    values = sorted(labels, key=lambda item: (_numeric(item["value"]), str(item["value"])))
    return [{"value": item["value"], "label": item["label"]} for item in values]


def _numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


WAVE_MODES = ("wave", "all", "compare")


def wave_view(project: dict[str, Any] | None) -> dict[str, Any]:
    """Какая волна выбрана для работы.

    `mode`: `wave` — одна волна (по умолчанию последняя), `all` — все
    вместе, `compare` — сравнение волн колонками. Без двух волн выбора нет:
    `mode` = `all`. Сохранённая волна, которой больше нет, заменяется
    последней.
    """
    values = wave_values(project)
    variable = wave_variable_of(project)
    if len(values) < 2 or variable is None:
        return {"mode": "all", "variable": variable, "value": None, "label": None}
    stored = (((project or {}).get("configuration") or {}).get("wave_view")) or {}
    mode = stored.get("mode") if stored.get("mode") in WAVE_MODES else "wave"
    if mode != "wave":
        return {"mode": mode, "variable": variable, "value": None, "label": None}
    chosen = next(
        (item for item in values if value_key(item["value"]) == value_key(stored.get("value"))),
        values[-1],
    )
    return {"mode": "wave", "variable": variable, "value": chosen["value"],
            "label": chosen["label"]}


def compare_wave_block(project: dict[str, Any] | None) -> dict[str, Any] | None:
    """Блок разреза «Волна» для режима сравнения волн, или None.

    В режиме сравнения волна — колонка в таблицах, книге и ИИ отчёте без
    ручной настройки баннера.
    """
    if wave_view(project)["mode"] != "compare":
        return None
    variable = wave_variable_of(project)
    question = next(
        (
            item for item in (project or {})["configuration"]["questions"]
            if item.get("role") == "wave" and item.get("source_variables") == [variable]
        ),
        None,
    )
    if question is None:
        return None
    return {"label": "Волна", "sources": [{"kind": "question", "ref": question["code"]}]}


def with_wave_block(banner: dict[str, Any], block: dict[str, Any] | None) -> dict[str, Any]:
    """Баннер с блоком волны первым, если его в баннере ещё нет."""
    if block is None:
        return banner
    reference = block["sources"][0]["ref"]
    blocks = list(banner.get("blocks") or [])
    if any(
        source.get("kind") == "question" and source.get("ref") == reference
        for item in blocks for source in item.get("sources") or []
    ):
        return banner
    return {**banner, "name": banner.get("name") or "Волны", "blocks": [block, *blocks]}


def comparison_wave(
    project: dict[str, Any] | None, settings: dict[str, Any]
) -> dict[str, Any] | None:
    """Волна, с которой сравнивается выбранная одна волна, или None.

    «С предыдущей» — волна перед выбранной по порядку кода; «с контрольной» —
    контрольная, если это не сама выбранная волна.
    """
    view = wave_view(project)
    mode = settings.get("wave_comparison", "none")
    if view["mode"] != "wave" or mode == "none":
        return None
    values = wave_values(project)
    position = next(
        index for index, item in enumerate(values)
        if value_key(item["value"]) == value_key(view["value"])
    )
    if mode == "previous":
        return values[position - 1] if position > 0 else None
    control = settings.get("wave_control_value")
    target = next(
        (item for item in values if value_key(item["value"]) == value_key(control)), None
    )
    if target is None or value_key(target["value"]) == value_key(view["value"]):
        return None
    return target


def active_wave_filter(project: dict[str, Any] | None) -> tuple[str, Any] | None:
    """Переменная и значение, по которым сужается массив, или None."""
    view = wave_view(project)
    if view["mode"] != "wave":
        return None
    return view["variable"], view["value"]


def propose_mapping(
    project: dict[str, Any], wave: WaveFile
) -> list[dict[str, Any]]:
    """Сопоставление: сначала точное по имени, затем по подписи.

    Возвращает строку на каждую переменную проекта: какая переменная волны
    ей соответствует и как найдена. Несопоставленное потом предлагает ИИ, а
    решает человек.
    """
    names = list(wave.frame.columns)
    by_label: dict[str, list[str]] = {}
    for name in names:
        by_label.setdefault(_normalized(wave.labels.get(name)), []).append(name)
    used: set[str] = set()
    rows = []
    base_names = {item["name"] for item in base_variables(project)}
    for variable in base_variables(project):
        target = variable["name"]
        source = None
        how = None
        if target in names:
            source, how = target, "code"
        else:
            candidates = [
                name for name in by_label.get(_normalized(variable.get("label")), [])
                if name not in used and name not in base_names
            ]
            if variable.get("label") and len(candidates) == 1:
                source, how = candidates[0], "label"
        if source:
            used.add(source)
        rows.append(
            {
                "target": target,
                "label": variable.get("label") or "",
                "source": source,
                "how": how,
            }
        )
    return rows


def unmatched_wave_variables(mapping: list[dict[str, Any]], wave: WaveFile) -> list[str]:
    used = {row["source"] for row in mapping if row.get("source")}
    return [name for name in wave.frame.columns if name not in used]


def convergence(
    project: dict[str, Any], wave: WaveFile, mapping: list[dict[str, Any]]
) -> dict[str, Any]:
    """Сходимость волны с проектом: структура анкеты и цели веса.

    Блокирует только то, что сломает общий массив: разный тип хранения
    (число и текст) у сопоставленной пары. Остальное — предупреждения.
    """
    variables = {item["name"]: item for item in project["inspection"]["variables"]}
    blocking: list[str] = []
    changed: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for row in mapping:
        variable = variables.get(row["target"]) or {}
        source = row.get("source")
        if not source:
            missing.append({"name": row["target"], "label": row["label"]})
            continue
        if source not in wave.frame.columns:
            blocking.append(f"Переменной {source} нет в файле волны.")
            continue
        base_string = variable.get("storage_type") == "string"
        wave_string = wave.frame[source].dtype == object
        if base_string != wave_string:
            blocking.append(
                f"{row['target']} ← {source}: в одной волне число, в другой текст."
            )
            continue
        notes = []
        if _normalized(variable.get("label")) != _normalized(wave.labels.get(source)):
            notes.append("подпись вопроса изменилась")
        base_codes = {
            value_key(item["value"]): item["label"] for item in variable.get("value_labels") or []
        }
        wave_codes = {
            value_key(code): label for code, label in (wave.value_labels.get(source) or {}).items()
        }
        added = sorted(set(wave_codes) - set(base_codes))
        removed = sorted(set(base_codes) - set(wave_codes)) if wave_codes else []
        relabeled = [
            code for code in set(base_codes) & set(wave_codes)
            if _normalized(base_codes[code]) != _normalized(wave_codes[code])
        ]
        if added:
            notes.append("новые коды: " + ", ".join(added[:8]))
        if removed:
            notes.append("нет кодов: " + ", ".join(removed[:8]))
        if relabeled:
            notes.append("другие подписи у кодов: " + ", ".join(sorted(relabeled)[:8]))
        if notes:
            changed.append({"name": row["target"], "source": source, "notes": notes})
    weights = _weight_convergence(project, wave, mapping)
    return {
        "blocking": blocking,
        "changed": changed,
        "missing": missing,
        "weights": weights,
        "rows": len(wave.frame),
    }


def _weight_convergence(
    project: dict[str, Any], wave: WaveFile, mapping: list[dict[str, Any]]
) -> list[str]:
    """Цели рассчитанного веса: каждая цель должна встретиться в волне, иначе
    вес внутри волны не сойдётся. Измерения по перекодировкам не проверяются:
    их категории считаются при чтении массива."""
    sources = {row["target"]: row.get("source") for row in mapping}
    warnings = []
    for weight in project["configuration"].get("calculated_weights", []):
        name = weight.get("name", "")
        for dimension in weight.get("dimensions") or []:
            if dimension.get("recoding_id"):
                continue
            variable = dimension.get("variable")
            source = sources.get(variable)
            if source is None:
                warnings.append(f"Вес «{name}»: переменной {variable} нет в волне.")
                continue
            present = {value_key(value) for value in wave.frame[source].dropna().unique()}
            for target in dimension.get("targets") or []:
                if not any(value_key(value) in present for value in target.get("values") or []):
                    warnings.append(
                        f"Вес «{name}»: у {variable} в волне нет категории «{target.get('label')}»"
                        " — цель на неё не сойдётся."
                    )
    return warnings


def stack_waves(
    waves: list[dict[str, Any]],
    project_dir: Path,
    wave_variable: str,
    target: Path,
) -> int:
    """Собрать общий массив из волн и записать в `target`. Возвращает число строк.

    Схема — переменные первой волны в её порядке, затем новые переменные
    следующих волн. Подписи переменных и кодов — первой волны; коды, которых
    в ней не было, добавляются с подписями своей волны.
    """
    if not waves:
        raise WaveError("Нет ни одной волны.")
    frames = []
    labels: dict[str, str] = {}
    value_labels: dict[str, dict[Any, str]] = {}
    measures: dict[str, str] = {}
    missing: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    file_labels: dict[float, str] = {}
    for index, wave in enumerate(waves, start=1):
        data = read_wave(project_dir / WAVE_DIR / f"{wave['id']}.sav")
        columns = list(data.frame.columns)
        if index == 1:
            renames = {name: name for name in columns}
        else:
            # Переменная проекта ← переменная файла волны; новые переменные
            # волны идут под своими именами (их уникальность проверена при
            # добавлении волны).
            renames = {
                source: target_name
                for target_name, source in (wave.get("mapping") or {}).items()
                if source and source in columns
            }
            for name in wave.get("added") or []:
                if name in columns and name not in renames:
                    renames[name] = name
        frame = data.frame[list(renames)].rename(columns=renames)
        # Волна своей переменной не несёт — строки получают её код. Если
        # несёт (размечена в данных или сопоставлена), её значения целы,
        # а пустые получают код файла.
        code = wave.get("code", float(index))
        if wave_variable in frame.columns:
            if code is not None:
                frame[wave_variable] = frame[wave_variable].fillna(float(code))
        else:
            frame[wave_variable] = float(code if code is not None else index)
            file_labels[float(code if code is not None else index)] = wave["label"]
        for source, name in renames.items():
            if name not in order:
                order.append(name)
            labels.setdefault(name, data.labels.get(source) or "")
            measures.setdefault(name, data.measures.get(source) or "unknown")
            if source in data.missing and name not in missing:
                missing[name] = data.missing[source]
            known = value_labels.setdefault(name, {})
            existing = {value_key(code) for code in known}
            for code, label in (data.value_labels.get(source) or {}).items():
                if value_key(code) not in existing:
                    known[code] = label
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    columns = [name for name in order if name in combined.columns and name != wave_variable]
    columns += [wave_variable]
    combined = combined[columns]
    for name in columns:
        if combined[name].dtype == object:
            combined[name] = combined[name].where(combined[name].notna(), "")
    labels.setdefault(wave_variable, "Волна")
    if not labels[wave_variable]:
        labels[wave_variable] = "Волна"
    known = value_labels.setdefault(wave_variable, {})
    for code, label in file_labels.items():
        same = next((key for key in known if value_key(key) == value_key(code)), None)
        if same is not None:
            del known[same]
        known[code] = label
    measures[wave_variable] = "nominal"
    temporary = target.with_suffix(".stacking.sav")
    pyreadstat.write_sav(
        combined,
        temporary,
        column_labels=[labels.get(name, "") for name in columns],
        variable_value_labels={
            name: codes for name, codes in value_labels.items() if codes and name in columns
        },
        variable_measure={
            name: measure for name, measure in measures.items()
            if name in columns and measure in {"nominal", "ordinal", "scale"}
        },
        missing_ranges={name: ranges for name, ranges in missing.items() if name in columns},
    )
    temporary.replace(target)
    return len(combined)


def wave_variable_name(project: dict[str, Any]) -> str:
    """Имя переменной волны: размеченная в данных (роль «Волна»), иначе
    WAVE, если свободно, иначе WAVE_2, WAVE_3…"""
    current = (project.get("waves_meta") or {}).get("variable")
    if current:
        return current
    in_data = wave_variable_of(project)
    if in_data:
        return in_data
    taken = {item["name"].lower() for item in project["inspection"]["variables"]}
    name = "WAVE"
    index = 2
    while name.lower() in taken:
        name = f"WAVE_{index}"
        index += 1
    return name


def trend_project(project: dict[str, Any]) -> dict[str, Any] | None:
    """Копия проекта для листа «Тренды»: разрез — переменная волны, сравнение
    с предыдущей волной. None, если волн меньше двух."""
    if len(wave_values(project)) < 2:
        return None
    variable = wave_variable_of(project)
    if not any(item["code"] == variable for item in project["configuration"]["questions"]):
        return None
    trend = copy.deepcopy(project)
    configuration = trend["configuration"]
    banner_id = str(uuid4())
    configuration["banners"] = [
        *configuration.get("banners", []),
        {
            "id": banner_id,
            "name": "Волны",
            "blocks": [{"label": "Волна", "sources": [{"kind": "question", "ref": variable}]}],
        },
    ]
    configuration["report_banner_id"] = banner_id
    # Тренды — всегда по всем волнам, какая бы волна ни была выбрана для работы.
    configuration["wave_view"] = {"mode": "compare"}
    configuration["report_settings"] = {
        **(configuration.get("report_settings") or {}),
        "wave_comparison": "previous",
        "wave_control_value": None,
        "compare_pairwise": False,
    }
    return trend
