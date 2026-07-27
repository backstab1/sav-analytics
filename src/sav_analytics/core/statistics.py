from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

import numpy as np
from scipy.stats import t as student_t

Direction = Literal["higher", "lower", "none"]


@dataclass(frozen=True)
class StatisticalTestResult:
    method: str
    performed: bool
    significant: bool | None
    direction: Direction | None
    alpha: float
    statistic: float | None
    p_value: float | None
    difference: float
    confidence_interval: tuple[float, float] | None
    reason: str | None = None
    degrees_of_freedom: float | None = None
    expected_frequencies: tuple[float, float, float, float] | None = None
    group_estimates: tuple[float, float] | None = None
    group_variances: tuple[float, float] | None = None
    group_bases: tuple[int, int] | None = None
    group_successes: tuple[int, int] | None = None
    approximate: bool = False


def proportion_z_test(
    successes_a: int,
    base_a: int,
    successes_b: int,
    base_b: int,
    *,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    """Compare two independent, unweighted proportions with a pooled two-sided z-test."""
    _validate_binomial_sample(successes_a, base_a, "A")
    _validate_binomial_sample(successes_b, base_b, "B")
    alpha = _adjusted_alpha(confidence_level, comparisons)
    difference = successes_a / base_a - successes_b / base_b
    estimates = (successes_a / base_a, successes_b / base_b)

    if base_a < minimum_base or base_b < minimum_base:
        return _skipped_result(
            "z-test",
            alpha,
            difference,
            estimates,
            "Невзвешенная база одной из групп ниже установленного порога.",
            group_bases=(base_a, base_b),
            group_successes=(successes_a, successes_b),
        )

    pooled = (successes_a + successes_b) / (base_a + base_b)
    expected = (
        base_a * pooled,
        base_a * (1 - pooled),
        base_b * pooled,
        base_b * (1 - pooled),
    )
    if any(value < 5 for value in expected):
        return _skipped_result(
            "z-test",
            alpha,
            difference,
            estimates,
            "Хотя бы одна ожидаемая частота таблицы 2×2 меньше 5.",
            expected_frequencies=expected,
            group_bases=(base_a, base_b),
            group_successes=(successes_a, successes_b),
        )

    pooled_standard_error = math.sqrt(
        pooled * (1 - pooled) * (1 / base_a + 1 / base_b)
    )
    if pooled_standard_error == 0:
        return _skipped_result(
            "z-test",
            alpha,
            difference,
            estimates,
            "Нулевая дисперсия не позволяет выполнить z-test.",
            expected_frequencies=expected,
            group_bases=(base_a, base_b),
            group_successes=(successes_a, successes_b),
        )

    statistic = difference / pooled_standard_error
    p_value = math.erfc(abs(statistic) / math.sqrt(2))
    interval_standard_error = math.sqrt(
        estimates[0] * (1 - estimates[0]) / base_a
        + estimates[1] * (1 - estimates[1]) / base_b
    )
    critical = NormalDist().inv_cdf(1 - alpha / 2)
    interval = (
        difference - critical * interval_standard_error,
        difference + critical * interval_standard_error,
    )
    significant = p_value < alpha
    return StatisticalTestResult(
        method="z-test",
        performed=True,
        significant=significant,
        direction=_direction(difference, significant),
        alpha=alpha,
        statistic=statistic,
        p_value=p_value,
        difference=difference,
        confidence_interval=interval,
        expected_frequencies=expected,
        group_estimates=estimates,
        group_bases=(base_a, base_b),
        group_successes=(successes_a, successes_b),
    )


def subgroup_vs_rest_z_test(
    outcome: Iterable[bool],
    total_mask: Iterable[bool],
    subgroup_mask: Iterable[bool],
    *,
    eligible_mask: Iterable[bool] | None = None,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    """Compare a subgroup with its non-overlapping Rest inside Total."""
    selected = np.asarray(list(outcome), dtype=bool)
    total = np.asarray(list(total_mask), dtype=bool)
    subgroup = np.asarray(list(subgroup_mask), dtype=bool)
    if not len(selected) or len(total) != len(selected) or len(subgroup) != len(selected):
        raise ValueError("Outcome, Total и Subgroup должны иметь одинаковую ненулевую длину.")
    if np.any(subgroup & ~total):
        raise ValueError("Subgroup должен полностью входить в Total.")
    if eligible_mask is None:
        eligible = np.ones(len(selected), dtype=bool)
    else:
        eligible = np.asarray(list(eligible_mask), dtype=bool)
        if len(eligible) != len(selected):
            raise ValueError("Маска валидной базы должна иметь ту же длину, что и данные.")

    subgroup_base_mask = total & subgroup & eligible
    rest_base_mask = total & ~subgroup & eligible
    return proportion_z_test(
        int((selected & subgroup_base_mask).sum()),
        int(subgroup_base_mask.sum()),
        int((selected & rest_base_mask).sum()),
        int(rest_base_mask.sum()),
        confidence_level=confidence_level,
        comparisons=comparisons,
        minimum_base=minimum_base,
    )


def welch_t_test(
    values_a: Iterable[float],
    values_b: Iterable[float],
    *,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    """Compare means of two independent, unweighted samples with Welch's t-test."""
    sample_a = _finite_sample(values_a)
    sample_b = _finite_sample(values_b)
    alpha = _adjusted_alpha(confidence_level, comparisons)
    if not len(sample_a) or not len(sample_b):
        raise ValueError("Обе группы должны содержать хотя бы одно числовое значение.")

    means = (float(sample_a.mean()), float(sample_b.mean()))
    difference = means[0] - means[1]
    if len(sample_a) < minimum_base or len(sample_b) < minimum_base:
        return _skipped_result(
            "Welch t-test",
            alpha,
            difference,
            means,
            "Невзвешенная база одной из групп ниже установленного порога.",
        )
    if len(sample_a) < 2 or len(sample_b) < 2:
        return _skipped_result(
            "Welch t-test",
            alpha,
            difference,
            means,
            "Для оценки дисперсии в каждой группе нужны минимум два наблюдения.",
        )

    variances = (float(sample_a.var(ddof=1)), float(sample_b.var(ddof=1)))
    variance_terms = (variances[0] / len(sample_a), variances[1] / len(sample_b))
    standard_error_squared = variance_terms[0] + variance_terms[1]
    if standard_error_squared == 0:
        return _skipped_result(
            "Welch t-test",
            alpha,
            difference,
            means,
            "Нулевая дисперсия обеих групп не позволяет выполнить Welch t-test.",
            group_variances=variances,
        )

    standard_error = math.sqrt(standard_error_squared)
    degrees_of_freedom = standard_error_squared**2 / (
        variance_terms[0] ** 2 / (len(sample_a) - 1)
        + variance_terms[1] ** 2 / (len(sample_b) - 1)
    )
    statistic = difference / standard_error
    p_value = float(2 * student_t.sf(abs(statistic), degrees_of_freedom))
    critical = float(student_t.ppf(1 - alpha / 2, degrees_of_freedom))
    interval = (
        difference - critical * standard_error,
        difference + critical * standard_error,
    )
    significant = p_value < alpha
    return StatisticalTestResult(
        method="Welch t-test",
        performed=True,
        significant=significant,
        direction=_direction(difference, significant),
        alpha=alpha,
        statistic=statistic,
        p_value=p_value,
        difference=difference,
        confidence_interval=interval,
        degrees_of_freedom=degrees_of_freedom,
        group_estimates=means,
        group_variances=variances,
    )


def _validate_binomial_sample(successes: int, base: int, label: str) -> None:
    if not isinstance(base, int) or isinstance(base, bool) or base <= 0:
        raise ValueError(f"База группы {label} должна быть положительным целым числом.")
    if not isinstance(successes, int) or isinstance(successes, bool):
        raise ValueError(f"Числитель группы {label} должен быть целым числом.")
    if not 0 <= successes <= base:
        raise ValueError(f"Числитель группы {label} должен находиться между 0 и базой.")


def _adjusted_alpha(confidence_level: float, comparisons: int) -> float:
    if not 0 < confidence_level < 1:
        raise ValueError("Уровень доверия должен находиться между 0 и 1.")
    if not isinstance(comparisons, int) or isinstance(comparisons, bool) or comparisons < 1:
        raise ValueError("Число сравнений должно быть положительным целым числом.")
    return (1 - confidence_level) / comparisons


def _finite_sample(values: Iterable[float]) -> np.ndarray:
    sample = np.asarray(list(values), dtype=float)
    return sample[np.isfinite(sample)]


def _direction(difference: float, significant: bool) -> Direction:
    if not significant:
        return "none"
    return "higher" if difference > 0 else "lower"


def _skipped_result(
    method: str,
    alpha: float,
    difference: float,
    group_estimates: tuple[float, float],
    reason: str,
    *,
    expected_frequencies: tuple[float, float, float, float] | None = None,
    group_variances: tuple[float, float] | None = None,
    group_bases: tuple[int, int] | None = None,
    group_successes: tuple[int, int] | None = None,
) -> StatisticalTestResult:
    return StatisticalTestResult(
        method=method,
        performed=False,
        significant=None,
        direction=None,
        alpha=alpha,
        statistic=None,
        p_value=None,
        difference=difference,
        confidence_interval=None,
        reason=reason,
        expected_frequencies=expected_frequencies,
        group_estimates=group_estimates,
        group_variances=group_variances,
        group_bases=group_bases,
        group_successes=group_successes,
    )
