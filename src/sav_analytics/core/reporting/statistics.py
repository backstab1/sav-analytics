from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, TextIO

import numpy as np
import pandas as pd

from ..statistics import (
    StatisticalTestResult,
    balance_z_test,
    proportion_z_test,
    subgroup_vs_rest_z_test,
    subgroup_vs_total_z_test,
    weighted_proportion_z_test,
    weighted_welch_t_test,
    welch_t_test,
)
from .models import StatisticalAuditEntry


@dataclass(frozen=True)
class _UnweightedProportionContext:
    selected: np.ndarray
    eligible: np.ndarray
    column_masks: tuple[np.ndarray, ...]
    effective_masks: tuple[np.ndarray, ...]
    bases: tuple[int, ...]
    successes: tuple[int, ...]


@dataclass(frozen=True)
class _UnweightedMeanContext:
    values: np.ndarray
    valid: np.ndarray
    base: np.ndarray
    column_masks: tuple[np.ndarray, ...]
    samples: tuple[np.ndarray, ...]


# Шапка аудита. Второе примечание отвечает на вопрос, который иначе задаёт
# каждый, кто читает файл внимательно: у границы принятия решения тест и
# интервал считают разброс по-разному и потому расходятся.
_AUDIT_NOTES = (
    "Примечание: статистическая значимость рассчитана в модели независимых "
    "наблюдений и сама по себе не подтверждает репрезентативность выборки.",
    "Примечание о доверительных интервалах для долей: проверка гипотезы использует "
    "объединённую (pooled) оценку дисперсии, а интервал строится по раздельным "
    "дисперсиям групп. Это стандартное сочетание, но у границы принятия решения оно "
    "выглядит противоречиво: тест может дать «незначимо», тогда как интервал не "
    "накрывает ноль. Решение о значимости всегда принимается по тесту.",
)


def _excel_column_name(index: int) -> str:
    result = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result

def _unweighted_proportion_context(
    outcome: pd.Series,
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
) -> _UnweightedProportionContext:
    selected = outcome.fillna(False).to_numpy(dtype=bool, copy=False)
    eligible = eligible_mask.to_numpy(dtype=bool, copy=False)
    column_masks = tuple(
        column["mask"].to_numpy(dtype=bool, copy=False) for column in columns
    )
    effective_masks = tuple(mask & eligible for mask in column_masks)
    bases = tuple(int(np.count_nonzero(mask)) for mask in effective_masks)
    successes = tuple(
        int(np.count_nonzero(selected & mask)) for mask in effective_masks
    )
    return _UnweightedProportionContext(
        selected,
        eligible,
        column_masks,
        effective_masks,
        bases,
        successes,
    )

def _comparison_scheme_line(banner: dict[str, Any]) -> str:
    """Строка шапки аудита: с кем сравнивались подгруппы."""
    if (banner.get("compare_target") or "rest") == "total":
        return (
            "Схема сравнения: подгруппа против Total. Total включает саму подгруппу, "
            "поэтому выборки пересекаются, различия систематически занижены, а вывод "
            "сдвинут в сторону «незначимо». Режим включён вручную ради совпадения с "
            "клиентскими макросами; статистически корректная схема — против остальных."
        )
    return (
        "Схема сравнения: подгруппа против остальных респондентов блока "
        "(Rest = Total − Subgroup). Total остаётся описательной колонкой и в тестах "
        "не участвует."
    )


def _compares_with_total(column: dict[str, Any]) -> bool:
    """Клиентская схема «подгруппа против Total» вместо непересекающегося Rest."""
    return column.get("compare_target") == "total"


def _reference_mask(
    total_mask: Any,
    column_mask: Any,
    eligible_mask: Any,
    column: dict[str, Any],
) -> Any:
    """Маска группы, с которой сравнивают.

    Для Rest это дополнение подгруппы внутри Total, для Total — весь Total
    целиком, включая саму подгруппу. Второй вариант пересекается с подгруппой
    и потому занижает различие; он существует ради совпадения с клиентскими
    макросами и всюду помечается в аудите.
    """
    if _compares_with_total(column):
        return total_mask & eligible_mask
    return total_mask & ~column_mask & eligible_mask


def _unweighted_proportion_test(
    context: _UnweightedProportionContext,
    position: int,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    reference = _reference_mask(
        context.column_masks[0], context.column_masks[position], context.eligible, column
    )
    try:
        return proportion_z_test(
            context.successes[position],
            context.bases[position],
            int(np.count_nonzero(context.selected & reference)),
            int(np.count_nonzero(reference)),
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    except ValueError:
        return None

def _unweighted_mean_context(
    series: pd.Series,
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
) -> _UnweightedMeanContext:
    numeric = pd.to_numeric(series, errors="coerce")
    values = numeric.to_numpy(dtype=float, na_value=np.nan)
    valid = np.isfinite(values)
    base = base_mask.to_numpy(dtype=bool, copy=False)
    column_masks = tuple(
        column["mask"].to_numpy(dtype=bool, copy=False) for column in columns
    )
    samples = tuple(values[mask & base & valid] for mask in column_masks)
    return _UnweightedMeanContext(values, valid, base, column_masks, samples)

def _unweighted_mean_test(
    context: _UnweightedMeanContext,
    position: int,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    subgroup = context.samples[position]
    rest_mask = (
        context.column_masks[0]
        & ~context.column_masks[position]
        & context.base
        & context.valid
    )
    rest = context.values[rest_mask]
    if not subgroup.size or not rest.size:
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    return welch_t_test(
        subgroup,
        rest,
        confidence_level=settings["confidence_level"],
        comparisons=comparisons,
        minimum_base=settings["minimum_base"],
    )

def _balance_result(
    scores: pd.Series,
    mask_a: pd.Series,
    mask_b: pd.Series,
    settings: dict[str, Any],
    columns: list[dict[str, Any]],
    column: dict[str, Any],
    method: str,
) -> StatisticalTestResult | None:
    sample_a = scores[mask_a].dropna()
    sample_b = scores[mask_b].dropna()
    if sample_a.empty or sample_b.empty:
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    return balance_z_test(
        sample_a,
        sample_b,
        weights_a=weights.loc[sample_a.index] if weights is not None else None,
        weights_b=weights.loc[sample_b.index] if weights is not None else None,
        method=method,
        confidence_level=settings["confidence_level"],
        comparisons=comparisons,
        minimum_base=settings["minimum_base"],
    )

def _pairwise_note(
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    cache: dict[tuple[int, int], StatisticalTestResult | None],
    run_pair: Callable[[int, int], StatisticalTestResult | None],
) -> list[StatisticalAuditEntry]:
    """Обойти пары колонок внутри блока баннера и собрать записи для строки.

    ``run_pair`` считает тест для пары позиций и возвращает None, если хотя бы
    одна группа пуста — тогда в аудит попадает запись с причиной вместо
    результата. Тест выполняется один раз на пару и переиспользуется из кэша
    в обратную сторону через :func:`_reverse_test_result`.

    Возвращаются записи со стороны текущей колонки — все пары блока, включая
    те, что уже ушли в аудит в обратном направлении: примечание пишется на
    конкретную ячейку и читается от неё.
    """
    if not column.get("compare_pairwise"):
        return []
    current_position = _column_position(columns, column)
    entries: list[StatisticalAuditEntry] = []
    for position, other in enumerate(columns):
        if other is column or other.get("block_index") != column.get("block_index"):
            continue
        pair = tuple(sorted((current_position, position)))
        if pair not in cache:
            cache[pair] = run_pair(*pair)
        result = cache[pair]
        if current_position > position:
            result = _reverse_test_result(result)
        entry = StatisticalAuditEntry(
            sheet=audit_context[0],
            question_code=audit_context[1],
            question_label=audit_context[2],
            row_label=row_label,
            comparison="Pairwise",
            group_a=_column_title(current_position, column),
            group_b=_column_title(position, other),
            result=result,
            reason="Пустая группа." if result is None else None,
        )
        entries.append(entry)
        if current_position < position:
            audit_entries.append(entry)
    return entries

def _pairwise_balance_entries(
    scores: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    method: str,
    cache: dict[tuple[int, int], StatisticalTestResult | None],
) -> list[StatisticalAuditEntry]:
    def run_pair(
        left_position: int, right_position: int
    ) -> StatisticalTestResult | None:
        left, right = (columns[left_position], columns[right_position])
        return _balance_result(
            scores,
            left["mask"] & eligible_mask,
            right["mask"] & eligible_mask,
            settings,
            columns,
            left,
            method,
        )

    return _pairwise_note(
        column, columns, audit_entries, audit_context, row_label, cache, run_pair
    )

def _proportion_test(
    outcome: pd.Series,
    total_mask: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    try:
        weights = settings["weights"]
        if weights is not None:
            subgroup_mask = total_mask & column["mask"] & eligible_mask
            reference_mask = _reference_mask(
                total_mask, column["mask"], eligible_mask, column
            )
            return weighted_proportion_z_test(
                outcome[subgroup_mask],
                weights[subgroup_mask],
                outcome[reference_mask],
                weights[reference_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        compare = (
            subgroup_vs_total_z_test
            if _compares_with_total(column)
            else subgroup_vs_rest_z_test
        )
        return compare(
            outcome.fillna(False),
            total_mask,
            column["mask"],
            eligible_mask=eligible_mask,
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    except ValueError:
        return None

def _mean_test(
    series: pd.Series,
    total_mask: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> StatisticalTestResult | None:
    if not column.get("compare_to_total"):
        return None
    subgroup_mask = total_mask & column["mask"] & base_mask
    reference_mask = _reference_mask(total_mask, column["mask"], base_mask, column)
    subgroup = pd.to_numeric(series[subgroup_mask], errors="coerce").dropna()
    rest = pd.to_numeric(series[reference_mask], errors="coerce").dropna()
    if subgroup.empty or rest.empty:
        return None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    if weights is not None:
        return weighted_welch_t_test(
            subgroup,
            weights.loc[subgroup.index],
            rest,
            weights.loc[rest.index],
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    return welch_t_test(
        subgroup,
        rest,
        confidence_level=settings["confidence_level"],
        comparisons=comparisons,
        minimum_base=settings["minimum_base"],
    )

def _comparison_count(column: dict[str, Any], columns: list[dict[str, Any]]) -> int:
    block_columns = [
        item
        for item in columns
        if item.get("block_index") == column.get("block_index")
    ]
    total_comparisons = len(block_columns) if column.get("compare_to_total") else 0
    pairwise_comparisons = (
        len(block_columns) * (len(block_columns) - 1) // 2
        if column.get("compare_pairwise")
        else 0
    )
    wave_columns = [item for item in block_columns if item.get("wave_value") is not None]
    peer_groups = {item.get("wave_peer_key") for item in wave_columns}
    wave_comparisons = (
        max(0, len(wave_columns) - len(peer_groups))
        if column.get("wave_comparison", "none") != "none"
        else 0
    )
    return max(1, total_comparisons + pairwise_comparisons + wave_comparisons)

def _wave_target(
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any] | None:
    mode = settings.get("wave_comparison", "none")
    if mode == "none" or column.get("wave_value") is None:
        return None
    peers = [
        item
        for item in columns
        if item.get("block_index") == column.get("block_index")
        and item.get("wave_peer_key") == column.get("wave_peer_key")
        and item.get("wave_value") is not None
    ]
    if mode == "previous":
        position = next((index for index, item in enumerate(peers) if item is column), -1)
        return peers[position - 1] if position > 0 else None
    control = settings.get("wave_control_value")
    return next(
        (
            item
            for item in peers
            if item is not column and _values_equal(item.get("wave_value"), control)
        ),
        None,
    )

def _wave_proportion_test(
    outcome: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, StatisticalTestResult | None]:
    target = _wave_target(column, columns, settings)
    if target is None:
        return None, None
    current_mask = column["mask"] & eligible_mask
    target_mask = target["mask"] & eligible_mask
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    selected = outcome.fillna(False).astype(bool)
    weights = settings["weights"]
    try:
        if weights is not None:
            result = weighted_proportion_z_test(
                selected[current_mask],
                weights[current_mask],
                selected[target_mask],
                weights[target_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        else:
            result = proportion_z_test(
                int((selected & current_mask).sum()),
                int(current_mask.sum()),
                int((selected & target_mask).sum()),
                int(target_mask.sum()),
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
    except ValueError:
        result = None
    return target, result

def _wave_mean_test(
    series: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, StatisticalTestResult | None]:
    target = _wave_target(column, columns, settings)
    if target is None:
        return None, None
    current = pd.to_numeric(series[column["mask"] & base_mask], errors="coerce").dropna()
    previous = pd.to_numeric(series[target["mask"] & base_mask], errors="coerce").dropna()
    if current.empty or previous.empty:
        return target, None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1
    weights = settings["weights"]
    if weights is not None:
        result = weighted_welch_t_test(
            current,
            weights.loc[current.index],
            previous,
            weights.loc[previous.index],
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    else:
        result = welch_t_test(
            current,
            previous,
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )
    return target, result

def _record_wave_comparison(
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    target: dict[str, Any] | None,
    result: StatisticalTestResult | None,
) -> StatisticalAuditEntry | None:
    if target is None:
        return None
    entry = StatisticalAuditEntry(
        sheet=audit_context[0],
        question_code=audit_context[1],
        question_label=audit_context[2],
        row_label=row_label,
        comparison="Wave",
        group_a=_worksheet_column_title(_column_position(columns, column), column),
        group_b=_worksheet_column_title(_column_position(columns, target), target),
        result=result,
        reason="Пустая сравниваемая волна." if result is None else None,
    )
    audit_entries.append(entry)
    return entry

def _values_equal(left: Any, right: Any) -> bool:
    try:
        return bool(left == right) or str(left) == str(right)
    except (TypeError, ValueError):
        return False

def _pairwise_proportion_entries(
    outcome: pd.Series,
    column: dict[str, Any],
    eligible_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    cache: dict[tuple[int, int], StatisticalTestResult | None],
    vectorized: _UnweightedProportionContext | None = None,
) -> list[StatisticalAuditEntry]:
    selected = outcome.fillna(False).astype(bool) if vectorized is None else None
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1

    def run_pair(
        left_position: int, right_position: int
    ) -> StatisticalTestResult | None:
        if vectorized is not None:
            if not vectorized.bases[left_position] or not vectorized.bases[right_position]:
                return None
            return proportion_z_test(
                vectorized.successes[left_position],
                vectorized.bases[left_position],
                vectorized.successes[right_position],
                vectorized.bases[right_position],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        left_mask = columns[left_position]["mask"] & eligible_mask
        right_mask = columns[right_position]["mask"] & eligible_mask
        if not left_mask.any() or not right_mask.any():
            return None
        weights = settings["weights"]
        if weights is not None:
            return weighted_proportion_z_test(
                selected[left_mask],
                weights[left_mask],
                selected[right_mask],
                weights[right_mask],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        return proportion_z_test(
            int((selected & left_mask).sum()),
            int(left_mask.sum()),
            int((selected & right_mask).sum()),
            int(right_mask.sum()),
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )

    return _pairwise_note(
        column, columns, audit_entries, audit_context, row_label, cache, run_pair
    )

def _pairwise_mean_entries(
    series: pd.Series,
    column: dict[str, Any],
    base_mask: pd.Series,
    columns: list[dict[str, Any]],
    settings: dict[str, Any],
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    cache: dict[tuple[int, int], StatisticalTestResult | None],
    vectorized: _UnweightedMeanContext | None = None,
) -> list[StatisticalAuditEntry]:
    comparisons = _comparison_count(column, columns) if settings["bonferroni"] else 1

    def run_pair(
        left_position: int, right_position: int
    ) -> StatisticalTestResult | None:
        if vectorized is not None:
            left = vectorized.samples[left_position]
            right = vectorized.samples[right_position]
            if not left.size or not right.size:
                return None
        else:
            left = pd.to_numeric(
                series[columns[left_position]["mask"] & base_mask], errors="coerce"
            ).dropna()
            right = pd.to_numeric(
                series[columns[right_position]["mask"] & base_mask], errors="coerce"
            ).dropna()
            if left.empty or right.empty:
                return None
        weights = settings["weights"]
        if weights is not None:
            return weighted_welch_t_test(
                left,
                weights.loc[left.index],
                right,
                weights.loc[right.index],
                confidence_level=settings["confidence_level"],
                comparisons=comparisons,
                minimum_base=settings["minimum_base"],
            )
        return welch_t_test(
            left,
            right,
            confidence_level=settings["confidence_level"],
            comparisons=comparisons,
            minimum_base=settings["minimum_base"],
        )

    return _pairwise_note(
        column, columns, audit_entries, audit_context, row_label, cache, run_pair
    )

def _format_pairwise_note(entries: list[StatisticalAuditEntry]) -> str | None:
    """Короткая сводка попарных сравнений: с кем ячейка расходится значимо."""
    lines = []
    for direction, caption in (("higher", "Значимо выше"), ("lower", "Значимо ниже")):
        labels = [
            entry.group_b
            for entry in entries
            if entry.result is not None
            and entry.result.significant
            and entry.result.direction == direction
        ]
        if labels:
            lines.append(f"{caption}: " + ", ".join(labels))
    return "\n".join(lines) or None


def cell_note(
    settings: dict[str, Any],
    entries: list[StatisticalAuditEntry],
    pairwise: list[StatisticalAuditEntry],
) -> str | None:
    """Примечание к ячейке: сводка попарных сравнений и, по настройке, детали.

    Детали рендерятся тем же кодом, что и `statistics.txt`, поэтому примечание
    не может разойтись с аудитом. Без включённой настройки поведение прежнее —
    только сводка, иначе примечание встанет почти на каждую посчитанную ячейку
    и утяжелит книгу.
    """
    blocks = [note for note in (_format_pairwise_note(pairwise),) if note]
    if settings["show_p_values"]:
        blocks.extend(
            "\n".join(line.strip() for line in _render_audit_entry(entry))
            for entry in (*entries, *pairwise)
        )
    return "\n\n".join(blocks) or None

def _reverse_test_result(
    result: StatisticalTestResult | None,
) -> StatisticalTestResult | None:
    """Return the same pairwise test viewed from group B instead of group A."""
    if result is None:
        return None
    direction = {"higher": "lower", "lower": "higher"}.get(
        result.direction, result.direction
    )
    interval = result.confidence_interval
    expected = result.expected_frequencies
    return replace(
        result,
        direction=direction,
        statistic=-result.statistic if result.statistic is not None else None,
        difference=-result.difference,
        confidence_interval=(-interval[1], -interval[0]) if interval is not None else None,
        expected_frequencies=(expected[2], expected[3], expected[0], expected[1])
        if expected is not None
        else None,
        group_estimates=_swap_pair(result.group_estimates),
        group_variances=_swap_pair(result.group_variances),
        group_bases=_swap_pair(result.group_bases),
        group_successes=_swap_pair(result.group_successes),
        group_weight_sums=_swap_pair(result.group_weight_sums),
        effective_bases=_swap_pair(result.effective_bases),
    )

def _swap_pair(pair: tuple[Any, Any] | None) -> tuple[Any, Any] | None:
    return (pair[1], pair[0]) if pair is not None else None

def _record_total_comparison(
    audit_entries: list[StatisticalAuditEntry],
    audit_context: tuple[str, str, str],
    row_label: str,
    column: dict[str, Any],
    columns: list[dict[str, Any]],
    result: StatisticalTestResult | None,
) -> StatisticalAuditEntry | None:
    """Записать сравнение в аудит и вернуть ту же запись для примечания.

    Примечание к ячейке и `statistics.txt` рендерятся из одного объекта, иначе
    две формулировки одного теста рано или поздно разъедутся.
    """
    if not column.get("compare_to_total"):
        return None
    position = _column_position(columns, column)
    if _compares_with_total(column):
        comparison = "Subgroup/Total (пересекающиеся выборки)"
        group_b = "Total — включая саму подгруппу"
        reason = "Пустая подгруппа или Total."
    else:
        comparison = "Subgroup/Rest"
        group_b = f"Rest({_excel_column_name(position + 1)}) — Total − {column['label']}"
        reason = "Пустая подгруппа или Rest."
    entry = StatisticalAuditEntry(
        sheet=audit_context[0],
        question_code=audit_context[1],
        question_label=audit_context[2],
        row_label=row_label,
        comparison=comparison,
        group_a=_column_title(position, column),
        group_b=group_b,
        result=result,
        reason=reason if result is None else None,
    )
    audit_entries.append(entry)
    return entry

def _column_title(position: int, column: dict[str, Any]) -> str:
    return f"{_excel_column_name(position + 1)} — {column['label']}"

def _worksheet_column_title(position: int, column: dict[str, Any]) -> str:
    return f"{_excel_column_name(position + 2)} — {column['label']}"

def _column_position(columns: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return next(index for index, column in enumerate(columns) if column is target)

class _StatisticsAuditWriter:
    def __init__(
        self,
        stream: TextIO,
        project: dict[str, Any],
        banner: dict[str, Any],
        configuration: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        self.stream = stream
        self.current_sheet: str | None = None
        self.current_question: tuple[str, str] | None = None
        self.current_row: str | None = None
        self.entry_count = 0
        report_filter = "не используется"
        report_filter_id = configuration.get("report_filter_id")
        if report_filter_id:
            selected_filter = next(
                (
                    item
                    for item in configuration.get("filters", [])
                    if item["id"] == report_filter_id
                ),
                None,
            )
            report_filter = selected_filter["name"] if selected_filter else str(report_filter_id)
        lines = [
            "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА",
            f"Проект: {project['name']}",
            f"Исходный SAV: {project.get('original_filename', 'source.sav')}",
            f"Дата расчёта: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Баннер: {banner.get('name', 'Total')}",
            f"Общий фильтр: {report_filter}",
            f"Вес: {settings['weight_label'] or 'не используется'}",
            f"Уровень доверия: {_number(settings['confidence_level'] * 100)}%",
            f"Bonferroni: {'включена' if settings['bonferroni'] else 'выключена'}",
            f"Порог малой базы: N < {settings['minimum_base']}",
            "",
            _comparison_scheme_line(banner),
            "",
            *_AUDIT_NOTES,
        ]
        self.stream.write("\n".join(lines) + "\n")

    def write_entries(self, entries: list[StatisticalAuditEntry]) -> None:
        for entry in entries:
            lines: list[str] = []
            if entry.sheet != self.current_sheet:
                lines.extend(["", f"=== {entry.sheet} ==="])
                self.current_sheet = entry.sheet
                self.current_question = None
                self.current_row = None
            question_key = (entry.question_code, entry.question_label)
            if question_key != self.current_question:
                lines.extend(["", f"[{entry.question_code}] {entry.question_label}"])
                self.current_question = question_key
                self.current_row = None
            if entry.row_label != self.current_row:
                lines.append(f"  Строка: {entry.row_label}")
                self.current_row = entry.row_label
            lines.extend(_render_audit_entry(entry))
            self.stream.write("\n".join(lines) + "\n")
            self.entry_count += 1

    def finish(self) -> None:
        if self.entry_count == 0:
            self.stream.write("\nСтатистические сравнения не включены.\n")

def _render_statistics_txt(
    project: dict[str, Any],
    banner: dict[str, Any],
    configuration: dict[str, Any],
    settings: dict[str, Any],
    entries: list[StatisticalAuditEntry],
) -> str:
    report_filter = "не используется"
    report_filter_id = configuration.get("report_filter_id")
    if report_filter_id:
        selected_filter = next(
            (
                item
                for item in configuration.get("filters", [])
                if item["id"] == report_filter_id
            ),
            None,
        )
        report_filter = selected_filter["name"] if selected_filter else str(report_filter_id)
    lines = [
        "СТАТИСТИЧЕСКИЙ АУДИТ ТОПЛАЙНА",
        f"Проект: {project['name']}",
        f"Исходный SAV: {project.get('original_filename', 'source.sav')}",
        f"Дата расчёта: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Баннер: {banner.get('name', 'Total')}",
        f"Общий фильтр: {report_filter}",
        f"Вес: {settings['weight_label'] or 'не используется'}",
        f"Уровень доверия: {_number(settings['confidence_level'] * 100)}%",
        f"Bonferroni: {'включена' if settings['bonferroni'] else 'выключена'}",
        f"Порог малой базы: N < {settings['minimum_base']}",
        "",
        _comparison_scheme_line(banner),
        "",
        *_AUDIT_NOTES,
    ]
    if not entries:
        lines.extend(["", "Статистические сравнения не включены."])
        return "\n".join(lines) + "\n"

    current_sheet = None
    current_question = None
    current_row = None
    for entry in entries:
        if entry.sheet != current_sheet:
            lines.extend(["", f"=== {entry.sheet} ==="])
            current_sheet = entry.sheet
            current_question = None
            current_row = None
        question_key = (entry.question_code, entry.question_label)
        if question_key != current_question:
            lines.extend(["", f"[{entry.question_code}] {entry.question_label}"])
            current_question = question_key
            current_row = None
        if entry.row_label != current_row:
            lines.append(f"  Строка: {entry.row_label}")
            current_row = entry.row_label
        lines.extend(_render_audit_entry(entry))
    return "\n".join(lines) + "\n"

def _render_audit_entry(entry: StatisticalAuditEntry) -> list[str]:
    lines = [
        f"    {entry.comparison}: {entry.group_a} vs {entry.group_b}",
    ]
    result = entry.result
    if result is None:
        lines.append(f"      Статус: пропущен. Причина: {entry.reason or 'Тест неприменим.'}")
        return lines
    lines.append(f"      Метод: {result.method}")
    if result.group_bases is not None:
        lines.append(f"      Базы: N1={result.group_bases[0]}; N2={result.group_bases[1]}")
    if result.group_successes is not None:
        lines.append(
            "      Числители: "
            f"n1={_number(result.group_successes[0])}; "
            f"n2={_number(result.group_successes[1])}"
        )
    if result.group_weight_sums is not None:
        lines.append(
            f"      Суммы весов: sum_w1={_number(result.group_weight_sums[0])}; "
            f"sum_w2={_number(result.group_weight_sums[1])}"
        )
    if result.effective_bases is not None:
        lines.append(
            f"      Эффективные базы: n_eff1={_number(result.effective_bases[0])}; "
            f"n_eff2={_number(result.effective_bases[1])}"
        )
    if result.group_estimates is not None:
        if result.method == "z-test":
            estimate_label = "Доли"
        elif "z-test" in result.method:
            estimate_label = "Балансы"
        else:
            estimate_label = "Средние"
        estimates = result.group_estimates
        lines.append(
            f"      {estimate_label}: group1={_number(estimates[0])}; "
            f"group2={_number(estimates[1])}"
        )
    if result.group_variances is not None:
        variances = result.group_variances
        lines.append(
            f"      Дисперсии: var1={_number(variances[0])}; var2={_number(variances[1])}"
        )
        variance_bases = result.effective_bases or result.group_bases
        if variance_bases is not None:
            standard_errors = (
                math.sqrt(variances[0] / variance_bases[0]),
                math.sqrt(variances[1] / variance_bases[1]),
            )
            lines.append(
                f"      Стандартные ошибки: se1={_number(standard_errors[0])}; "
                f"se2={_number(standard_errors[1])}"
            )
    if result.expected_frequencies is not None:
        lines.append(
            "      Ожидаемые частоты 2×2: "
            + "; ".join(_number(value) for value in result.expected_frequencies)
        )
    difference = result.difference * 100 if "z-test" in result.method else result.difference
    difference_unit = " п.п." if "z-test" in result.method else ""
    lines.append(f"      Разница: {_number(difference)}{difference_unit}")
    if result.confidence_interval is not None:
        interval = result.confidence_interval
        if "z-test" in result.method:
            interval = (interval[0] * 100, interval[1] * 100)
        lines.append(
            f"      Доверительный интервал: [{_number(interval[0])}; "
            f"{_number(interval[1])}]"
        )
    if not result.performed:
        lines.append(f"      Статус: пропущен. Причина: {result.reason}")
        lines.append(f"      Скорректированный alpha: {_number(result.alpha)}")
        return lines
    statistic_name = "z" if "z-test" in result.method else "t"
    lines.append(f"      {statistic_name}={_number(result.statistic)}")
    if result.degrees_of_freedom is not None:
        lines.append(f"      df={_number(result.degrees_of_freedom)}")
    lines.append(f"      p-value={_p_value(result.p_value)}")
    lines.append(f"      Скорректированный alpha: {_number(result.alpha)}")
    decision = "значимо" if result.significant else "незначимо"
    lines.append(f"      Решение: {decision}; направление={result.direction}")
    if result.approximate:
        lines.append("      Характер теста: приближённый")
    return lines

def _number(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6f}"

def _p_value(value: float | None) -> str:
    if value is None:
        return "—"
    return "<0.000001" if value < 0.000001 else _number(value)

