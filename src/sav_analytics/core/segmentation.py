"""Сегментация k-means: сегмент — перекодировка проекта (PQ.11).

Сегмент сохраняется перекодировкой режима `segments` и дальше работает
везде, где работают перекодировки: баннер, фильтр, таблицы, выгрузка SAV.

- Переменные стандартизуются (среднее 0, разброс 1), иначе доход в рублях
  задавил бы оценку по шкале 1–5.
- k-means++ с фиксированным зерном и десятью запусками: одна и та же
  сегментация на одних данных даёт одни и те же сегменты.
- Число сегментов подбирается по среднему силуэту, но выбирает аналитик.
- В определении хранятся центры и параметры стандартизации, а не метки
  респондентов: респондент относится к ближайшему центру при чтении
  массива. Поэтому новая волна раскладывается по тем же сегментам, а не
  пересчитывает их заново.
- Респондент с пропуском в любой переменной сегмента не получает — это
  пропуск, как у любой перекодировки.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .formulas import read_project_frame
from .not_applicable import applicable_series

SEED = 20260923
RUNS = 10
MAX_ITERATIONS = 300
SILHOUETTE_SAMPLE = 2000
SEGMENT_TYPES = {"numeric", "scale"}
MIN_PER_SEGMENT = 10


class SegmentationError(ValueError):
    pass


def segment_questions(codes: list[str], project: dict[str, Any]) -> list[dict[str, Any]]:
    questions = {item["code"]: item for item in project["configuration"]["questions"]}
    found = []
    for code in codes:
        question = questions.get(code)
        if question is None:
            raise SegmentationError(f"Вопрос {code} не найден.")
        if question["question_type"] not in SEGMENT_TYPES or len(
            question.get("source_variables") or []
        ) != 1:
            raise SegmentationError(
                f"{code}: сегментация строится по числовым вопросам и шкалам."
            )
        found.append(question)
    return found


def segment_columns(recoding: dict[str, Any], project: dict[str, Any]) -> list[str]:
    return [
        question["source_variables"][0]
        for question in segment_questions(recoding["variables"], project)
    ]


def _matrix(frame: pd.DataFrame, questions: list[dict[str, Any]]) -> pd.DataFrame:
    """Значения переменных сегмента: без кодов «не применимо» и исключённых ответов."""
    columns = {}
    for question in questions:
        series = pd.to_numeric(
            applicable_series(frame[question["source_variables"][0]], question), errors="coerce"
        )
        special = pd.to_numeric(pd.Series(question.get("special_values") or []), errors="coerce")
        columns[question["code"]] = series.mask(series.isin(special.dropna())).astype(float)
    return pd.DataFrame(columns, index=frame.index)


def _squared_distances(points: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """Квадраты расстояний n×k без промежуточного массива n×k×d."""
    squared = (points**2).sum(axis=1)[:, None] + (centres**2).sum(axis=1)[None, :]
    return np.maximum(squared - 2 * points @ centres.T, 0.0)


def _kmeans(points: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Лучший из RUNS запусков k-means++ по сумме квадратов расстояний."""
    generator = np.random.default_rng(SEED)
    best: tuple[np.ndarray, np.ndarray, float] | None = None
    for _ in range(RUNS):
        centres = _plus_plus(points, k, generator)
        labels = np.zeros(len(points), dtype=int)
        for _ in range(MAX_ITERATIONS):
            labels = _squared_distances(points, centres).argmin(axis=1)
            moved = np.array(
                [
                    points[labels == index].mean(axis=0)
                    if np.any(labels == index)
                    else centres[index]
                    for index in range(k)
                ]
            )
            if np.allclose(moved, centres):
                centres = moved
                break
            centres = moved
        inertia = float(((points - centres[labels]) ** 2).sum())
        if best is None or inertia < best[2] - 1e-12:
            best = (centres, labels, inertia)
    assert best is not None
    return best


def _plus_plus(points: np.ndarray, k: int, generator: np.random.Generator) -> np.ndarray:
    centres = [points[generator.integers(len(points))]]
    for _ in range(1, k):
        distances = _squared_distances(points, np.array(centres)).min(axis=1)
        total = distances.sum()
        if total == 0:
            centres.append(points[generator.integers(len(points))])
            continue
        centres.append(points[generator.choice(len(points), p=distances / total)])
    return np.array(centres)


def silhouette(points: np.ndarray, labels: np.ndarray) -> float:
    """Средний силуэт: насколько точка ближе к своему сегменту, чем к соседнему."""
    distances = np.sqrt(_squared_distances(points, points))
    values = []
    for index in range(len(points)):
        own = labels == labels[index]
        if own.sum() <= 1:
            values.append(0.0)
            continue
        inner = distances[index, own].sum() / (own.sum() - 1)
        outer = min(
            distances[index, labels == other].mean()
            for other in np.unique(labels)
            if other != labels[index]
        )
        values.append((outer - inner) / max(inner, outer) if max(inner, outer) else 0.0)
    return float(np.mean(values))


def _standardised(values: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    means = values.mean().to_numpy(dtype=float)
    scales = values.std(ddof=0).to_numpy(dtype=float)
    if np.any(scales == 0):
        constant = [name for name, scale in zip(values.columns, scales, strict=True) if scale == 0]
        raise SegmentationError(f"Без разброса значений: {', '.join(constant)}.")
    return (values.to_numpy(dtype=float) - means) / scales, means, scales


def _complete(path: str | Path, project: dict[str, Any], codes: list[str]) -> tuple[
    list[dict[str, Any]], pd.DataFrame
]:
    questions = segment_questions(codes, project)
    frame = read_project_frame(
        path, project, [question["source_variables"][0] for question in questions]
    )
    values = _matrix(frame, questions).dropna()
    return questions, values


def suggest_segments(
    path: str | Path, project: dict[str, Any], codes: list[str], k_min: int = 2, k_max: int = 6
) -> dict[str, Any]:
    """Варианты числа сегментов с силуэтом, размерами и профилями центров."""
    questions, values = _complete(path, project, codes)
    if len(values) < MIN_PER_SEGMENT * k_min:
        raise SegmentationError("Слишком мало респондентов без пропусков в выбранных переменных.")
    points, means, scales = _standardised(values)
    sample = points
    if len(points) > SILHOUETTE_SAMPLE:
        sample_index = np.random.default_rng(SEED).choice(
            len(points), SILHOUETTE_SAMPLE, replace=False
        )
        sample = points[sample_index]
    options = []
    for k in range(k_min, k_max + 1):
        if len(values) < MIN_PER_SEGMENT * k:
            break
        centres, labels, inertia = _kmeans(points, k)
        sample_labels = (
            labels
            if sample is points
            else _squared_distances(sample, centres).argmin(axis=1)
        )
        options.append(
            {
                "k": k,
                "silhouette": silhouette(sample, sample_labels)
                if len(np.unique(sample_labels)) > 1
                else 0.0,
                "inertia": inertia,
                "sizes": sorted((int((labels == index).sum()) for index in range(k)), reverse=True),
            }
        )
    best = max(options, key=lambda item: item["silhouette"])["k"] if options else None
    return {
        "base": len(values),
        "variables": [question["code"] for question in questions],
        "options": options,
        "best": best,
    }


def build_segment_definition(
    path: str | Path, project: dict[str, Any], definition: dict[str, Any]
) -> dict[str, Any]:
    """Посчитать сегменты и положить в определение центры и стандартизацию.

    Сегменты нумеруются по убыванию размера; профиль — средние переменных
    в исходных единицах, чтобы сегмент можно было назвать.
    """
    k = int(definition["k"])
    questions, values = _complete(path, project, definition["variables"])
    if len(values) < MIN_PER_SEGMENT * k:
        raise SegmentationError(
            f"На {k} сегментов нужно не меньше {MIN_PER_SEGMENT * k} респондентов "
            f"без пропусков, а их {len(values)}."
        )
    points, means, scales = _standardised(values)
    centres, labels, _ = _kmeans(points, k)
    order = sorted(range(k), key=lambda index: -int((labels == index).sum()))
    centres = centres[order]
    profiles = []
    for position, index in enumerate(order, start=1):
        members = values[labels == index]
        profiles.append(
            {
                "position": position,
                "size": int(len(members)),
                "means": {code: float(members[code].mean()) for code in members.columns},
            }
        )
    existing = definition.get("categories") or []
    categories = [
        {
            "label": (existing[position]["label"] if position < len(existing) else None)
            or f"Сегмент {position + 1}"
        }
        for position in range(k)
    ]
    return {
        **definition,
        "categories": categories,
        "model": {
            "means": means.tolist(),
            "scales": scales.tolist(),
            "centres": centres.tolist(),
            "base": int(len(values)),
            "profiles": profiles,
        },
    }


def segment_series(
    recoding: dict[str, Any], project: dict[str, Any], frame: pd.DataFrame
) -> pd.Series:
    """Подпись сегмента каждого респондента — по ближайшему сохранённому центру."""
    questions = segment_questions(recoding["variables"], project)
    model = recoding.get("model") or {}
    if not model.get("centres"):
        raise SegmentationError(f"У сегментации {recoding['code']} не посчитаны центры.")
    values = _matrix(frame, questions)
    complete = values.notna().all(axis=1)
    points = (values[complete].to_numpy(dtype=float) - np.array(model["means"])) / np.array(
        model["scales"]
    )
    centres = np.array(model["centres"])
    nearest = _squared_distances(points, centres).argmin(axis=1)
    labels = [category["label"] for category in recoding["categories"]]
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    result.loc[complete] = [labels[index] for index in nearest]
    return result
