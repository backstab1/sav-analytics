from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from statistics import NormalDist
from typing import Any, Literal

import numpy as np
from scipy.stats import chi2, studentized_range
from scipy.stats import f as fisher_f
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
    group_successes: tuple[float, float] | None = None
    group_weight_sums: tuple[float, float] | None = None
    effective_bases: tuple[float, float] | None = None
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
    return _pooled_proportion_test(
        alpha,
        difference,
        estimates,
        (base_a, base_b),
        pooled,
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


def subgroup_vs_total_z_test(
    outcome: Iterable[bool],
    total_mask: Iterable[bool],
    subgroup_mask: Iterable[bool],
    *,
    eligible_mask: Iterable[bool] | None = None,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    """Compare a subgroup with the Total that contains it, as client macros do.

    Статистически это не тест независимых выборок: подгруппа входит в Total и
    смещает его к себе, поэтому различие систематически занижается и вывод
    сдвинут в сторону «незначимо». Схема существует только ради совпадения с
    клиентскими макросами, которые считают именно так.

    Корректный учёт пересечения давал бы ровно тот же z, что и
    ``subgroup_vs_rest_z_test``: и разница долей, и её стандартная ошибка
    масштабируются одним множителем ``n_rest / n_total``. Поэтому отдельного
    «правильного сравнения с Total» не существует — есть Rest.
    """
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
    total_base_mask = total & eligible
    return proportion_z_test(
        int((selected & subgroup_base_mask).sum()),
        int(subgroup_base_mask.sum()),
        int((selected & total_base_mask).sum()),
        int(total_base_mask.sum()),
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
            group_bases=(len(sample_a), len(sample_b)),
        )
    if len(sample_a) < 2 or len(sample_b) < 2:
        return _skipped_result(
            "Welch t-test",
            alpha,
            difference,
            means,
            "Для оценки дисперсии в каждой группе нужны минимум два наблюдения.",
            group_bases=(len(sample_a), len(sample_b)),
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
            group_bases=(len(sample_a), len(sample_b)),
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
        group_bases=(len(sample_a), len(sample_b)),
    )


def effective_sample_size(weights: Iterable[float]) -> float:
    sample = np.asarray(list(weights), dtype=float)
    if not len(sample) or np.any(~np.isfinite(sample)) or np.any(sample <= 0):
        raise ValueError("Веса должны быть конечными положительными числами.")
    return float(sample.sum() ** 2 / np.square(sample).sum())


def weighted_proportion_z_test(
    outcome_a: Iterable[bool],
    weights_a: Iterable[float],
    outcome_b: Iterable[bool],
    weights_b: Iterable[float],
    *,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    selected_a, sample_weights_a = _weighted_sample(outcome_a, weights_a)
    selected_b, sample_weights_b = _weighted_sample(outcome_b, weights_b)
    alpha = _adjusted_alpha(confidence_level, comparisons)
    bases = (len(selected_a), len(selected_b))
    effective_bases = (
        effective_sample_size(sample_weights_a),
        effective_sample_size(sample_weights_b),
    )
    weight_sums = (float(sample_weights_a.sum()), float(sample_weights_b.sum()))
    successes = (
        float(sample_weights_a[selected_a].sum()),
        float(sample_weights_b[selected_b].sum()),
    )
    estimates = (successes[0] / weight_sums[0], successes[1] / weight_sums[1])
    difference = estimates[0] - estimates[1]
    common = {
        "group_bases": bases,
        "group_successes": successes,
        "group_weight_sums": weight_sums,
        "effective_bases": effective_bases,
        "approximate": True,
    }
    if min(bases) < minimum_base:
        return _skipped_result(
            "z-test", alpha, difference, estimates,
            "Невзвешенная база одной из групп ниже установленного порога.",
            **common,
        )
    if min(effective_bases) < minimum_base:
        return _skipped_result(
            "z-test", alpha, difference, estimates,
            "Эффективная база одной из групп ниже установленного порога.",
            **common,
        )
    pooled = (
        estimates[0] * effective_bases[0] + estimates[1] * effective_bases[1]
    ) / sum(effective_bases)
    return _pooled_proportion_test(alpha, difference, estimates, effective_bases, pooled, **common)


def weighted_welch_t_test(
    values_a: Iterable[float],
    weights_a: Iterable[float],
    values_b: Iterable[float],
    weights_b: Iterable[float],
    *,
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    sample_a, sample_weights_a = _weighted_numeric_sample(values_a, weights_a)
    sample_b, sample_weights_b = _weighted_numeric_sample(values_b, weights_b)
    alpha = _adjusted_alpha(confidence_level, comparisons)
    bases = (len(sample_a), len(sample_b))
    effective_bases = (
        effective_sample_size(sample_weights_a),
        effective_sample_size(sample_weights_b),
    )
    means = (
        float(np.average(sample_a, weights=sample_weights_a)),
        float(np.average(sample_b, weights=sample_weights_b)),
    )
    difference = means[0] - means[1]
    common = {
        "group_bases": bases,
        "group_weight_sums": (float(sample_weights_a.sum()), float(sample_weights_b.sum())),
        "effective_bases": effective_bases,
        "approximate": True,
    }
    if min(bases) < minimum_base:
        return _skipped_result(
            "Welch t-test", alpha, difference, means,
            "Невзвешенная база одной из групп ниже установленного порога.", **common,
        )
    if min(effective_bases) < minimum_base:
        return _skipped_result(
            "Welch t-test", alpha, difference, means,
            "Эффективная база одной из групп ниже установленного порога.", **common,
        )
    variances = (
        _weighted_variance(sample_a, sample_weights_a, means[0]),
        _weighted_variance(sample_b, sample_weights_b, means[1]),
    )
    variance_terms = (
        variances[0] / effective_bases[0],
        variances[1] / effective_bases[1],
    )
    standard_error_squared = sum(variance_terms)
    if standard_error_squared == 0:
        return _skipped_result(
            "Welch t-test", alpha, difference, means,
            "Нулевая дисперсия обеих групп не позволяет выполнить Welch t-test.",
            group_variances=variances, **common,
        )
    degrees_of_freedom = standard_error_squared**2 / (
        variance_terms[0] ** 2 / (effective_bases[0] - 1)
        + variance_terms[1] ** 2 / (effective_bases[1] - 1)
    )
    statistic = difference / math.sqrt(standard_error_squared)
    p_value = float(2 * student_t.sf(abs(statistic), degrees_of_freedom))
    critical = float(student_t.ppf(1 - alpha / 2, degrees_of_freedom))
    interval = (
        difference - critical * math.sqrt(standard_error_squared),
        difference + critical * math.sqrt(standard_error_squared),
    )
    significant = p_value < alpha
    return StatisticalTestResult(
        method="Welch t-test", performed=True, significant=significant,
        direction=_direction(difference, significant), alpha=alpha,
        statistic=statistic, p_value=p_value, difference=difference,
        confidence_interval=interval, degrees_of_freedom=degrees_of_freedom,
        group_estimates=means, group_variances=variances, **common,
    )


def balance_z_test(
    scores_a: Iterable[float],
    scores_b: Iterable[float],
    *,
    weights_a: Iterable[float] | None = None,
    weights_b: Iterable[float] | None = None,
    method: str = "balance z-test",
    confidence_level: float = 0.95,
    comparisons: int = 1,
    minimum_base: int = 30,
) -> StatisticalTestResult:
    """Compare two independent -1/0/+1 balances such as NPS or CSAT balance."""
    sample_a = _finite_sample(scores_a)
    sample_b = _finite_sample(scores_b)
    if not len(sample_a) or not len(sample_b):
        raise ValueError("Обе группы должны содержать хотя бы одно значение баланса.")
    if not np.isin(sample_a, (-1, 0, 1)).all() or not np.isin(sample_b, (-1, 0, 1)).all():
        raise ValueError("Баланс допускает только значения -1, 0 и 1.")
    weighted = weights_a is not None or weights_b is not None
    if weighted and (weights_a is None or weights_b is None):
        raise ValueError("Веса должны быть переданы для обеих групп.")
    if weighted:
        weight_a = _validated_weights(weights_a, len(sample_a))
        weight_b = _validated_weights(weights_b, len(sample_b))
    else:
        weight_a = np.ones(len(sample_a), dtype=float)
        weight_b = np.ones(len(sample_b), dtype=float)
    bases = (len(sample_a), len(sample_b))
    effective_bases = (
        effective_sample_size(weight_a),
        effective_sample_size(weight_b),
    )
    estimates = (
        float(np.average(sample_a, weights=weight_a)),
        float(np.average(sample_b, weights=weight_b)),
    )
    variances = (
        float(np.average(np.abs(sample_a), weights=weight_a) - estimates[0] ** 2),
        float(np.average(np.abs(sample_b), weights=weight_b) - estimates[1] ** 2),
    )
    difference = estimates[0] - estimates[1]
    alpha = _adjusted_alpha(confidence_level, comparisons)
    common = {
        "group_estimates": estimates,
        "group_variances": variances,
        "group_bases": bases,
        "group_weight_sums": (
            float(weight_a.sum()),
            float(weight_b.sum()),
        ) if weighted else None,
        "effective_bases": effective_bases if weighted else None,
        "approximate": weighted,
    }
    if min(bases) < minimum_base:
        return _skipped_result(
            method,
            alpha,
            difference,
            estimates,
            "Невзвешенная база одной из групп ниже установленного порога.",
            **common,
        )
    if weighted and min(effective_bases) < minimum_base:
        return _skipped_result(
            method,
            alpha,
            difference,
            estimates,
            "Эффективная база одной из групп ниже установленного порога.",
            **common,
        )
    variance = variances[0] / effective_bases[0] + variances[1] / effective_bases[1]
    if variance <= 0:
        return _skipped_result(
            method,
            alpha,
            difference,
            estimates,
            "Нулевая дисперсия не позволяет выполнить z-test баланса.",
            **common,
        )
    standard_error = math.sqrt(variance)
    return _normal_test_result(method, alpha, difference, standard_error, standard_error, **common)


def _pooled_proportion_test(
    alpha: float,
    difference: float,
    estimates: tuple[float, float],
    sizes: tuple[float, float],
    pooled: float,
    **fields: Any,
) -> StatisticalTestResult:
    """Общая часть z-test двух долей: объединённая доля, ожидаемые частоты 2×2,
    объединённая ошибка для теста и раздельная — для интервала.

    `sizes` — невзвешенные базы или эффективные, если данные взвешены.
    """
    expected = (
        sizes[0] * pooled,
        sizes[0] * (1 - pooled),
        sizes[1] * pooled,
        sizes[1] * (1 - pooled),
    )
    if any(value < 5 for value in expected):
        return _skipped_result(
            "z-test", alpha, difference, estimates,
            "Хотя бы одна ожидаемая частота таблицы 2×2 меньше 5.",
            expected_frequencies=expected,
            **fields,
        )
    pooled_standard_error = math.sqrt(pooled * (1 - pooled) * (1 / sizes[0] + 1 / sizes[1]))
    if pooled_standard_error == 0:
        return _skipped_result(
            "z-test", alpha, difference, estimates,
            "Нулевая дисперсия не позволяет выполнить z-test.",
            expected_frequencies=expected,
            **fields,
        )
    interval_standard_error = math.sqrt(
        estimates[0] * (1 - estimates[0]) / sizes[0]
        + estimates[1] * (1 - estimates[1]) / sizes[1]
    )
    return _normal_test_result(
        "z-test",
        alpha,
        difference,
        pooled_standard_error,
        interval_standard_error,
        expected_frequencies=expected,
        group_estimates=estimates,
        **fields,
    )


def _normal_test_result(
    method: str,
    alpha: float,
    difference: float,
    test_standard_error: float,
    interval_standard_error: float,
    **fields: Any,
) -> StatisticalTestResult:
    """Двусторонний тест по нормальному распределению и интервал разности."""
    statistic = difference / test_standard_error
    p_value = math.erfc(abs(statistic) / math.sqrt(2))
    critical = NormalDist().inv_cdf(1 - alpha / 2)
    interval = (
        difference - critical * interval_standard_error,
        difference + critical * interval_standard_error,
    )
    significant = p_value < alpha
    return StatisticalTestResult(
        method=method,
        performed=True,
        significant=significant,
        direction=_direction(difference, significant),
        alpha=alpha,
        statistic=statistic,
        p_value=p_value,
        difference=difference,
        confidence_interval=interval,
        **fields,
    )


def _validated_weights(weights: Iterable[float] | None, expected: int) -> np.ndarray:
    sample = np.asarray([] if weights is None else list(weights), dtype=float)
    if len(sample) != expected or np.any(~np.isfinite(sample)) or np.any(sample <= 0):
        raise ValueError("Веса должны совпадать с выборкой и быть положительными.")
    return sample


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
    sample = (
        np.asarray(values, dtype=float)
        if isinstance(values, np.ndarray)
        else np.asarray(list(values), dtype=float)
    )
    return sample[np.isfinite(sample)]


def _weighted_sample(
    outcome: Iterable[bool], weights: Iterable[float]
) -> tuple[np.ndarray, np.ndarray]:
    selected = np.asarray(list(outcome), dtype=bool)
    sample_weights = np.asarray(list(weights), dtype=float)
    if not len(selected) or len(selected) != len(sample_weights):
        raise ValueError("Значения и веса должны иметь одинаковую ненулевую длину.")
    if np.any(~np.isfinite(sample_weights)) or np.any(sample_weights <= 0):
        raise ValueError("Веса должны быть конечными положительными числами.")
    return selected, sample_weights


def _weighted_numeric_sample(
    values: Iterable[float], weights: Iterable[float]
) -> tuple[np.ndarray, np.ndarray]:
    sample = np.asarray(list(values), dtype=float)
    sample_weights = np.asarray(list(weights), dtype=float)
    if not len(sample) or len(sample) != len(sample_weights):
        raise ValueError("Значения и веса должны иметь одинаковую ненулевую длину.")
    valid = np.isfinite(sample)
    sample = sample[valid]
    sample_weights = sample_weights[valid]
    if not len(sample):
        raise ValueError("Группа должна содержать хотя бы одно числовое значение.")
    if np.any(~np.isfinite(sample_weights)) or np.any(sample_weights <= 0):
        raise ValueError("Веса должны быть конечными положительными числами.")
    return sample, sample_weights


def _weighted_variance(values: np.ndarray, weights: np.ndarray, mean: float) -> float:
    denominator = weights.sum() - np.square(weights).sum() / weights.sum()
    if denominator <= 0:
        return 0.0
    return float(np.sum(weights * np.square(values - mean)) / denominator)


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
    group_successes: tuple[float, float] | None = None,
    group_weight_sums: tuple[float, float] | None = None,
    effective_bases: tuple[float, float] | None = None,
    approximate: bool = False,
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
        group_weight_sums=group_weight_sums,
        effective_bases=effective_bases,
        approximate=approximate,
    )


@dataclass(frozen=True)
class OverallTestResult:
    """Общий тест блока баннера: связан ли показатель с колонками целиком.

    В отличие от попарного теста здесь нет двух групп, разницы и интервала —
    только статистика, степени свободы и p-value по всем колонкам блока.
    """

    method: str
    performed: bool
    significant: bool | None
    alpha: float
    statistic: float | None
    degrees_of_freedom: tuple[float, ...] | None
    p_value: float | None
    bases: tuple[int, ...]
    reason: str | None = None
    min_expected: float | None = None
    # На весах: размер колонки — эффективная база Киша, p-value приближённый.
    effective_bases: tuple[float, ...] | None = None


CHI_SQUARE = "Хи-квадрат Пирсона"
WELCH_ANOVA = "Welch ANOVA"


def skipped_overall(
    method: str,
    confidence_level: float,
    bases: tuple[int, ...],
    reason: str,
    *,
    min_expected: float | None = None,
) -> OverallTestResult:
    return OverallTestResult(
        method=method,
        performed=False,
        significant=None,
        alpha=1 - confidence_level,
        statistic=None,
        degrees_of_freedom=None,
        p_value=None,
        bases=bases,
        reason=reason,
        min_expected=min_expected,
    )


def chi_square_test(
    counts: Iterable[Iterable[float]],
    *,
    confidence_level: float,
    minimum_base: int,
) -> OverallTestResult:
    """Хи-квадрат Пирсона без поправки на непрерывность.

    Строки таблицы — ответы, столбцы — колонки блока. Тест не выполняется,
    если база колонки ниже порога или ожидаемые частоты малы: хотя бы одна
    меньше 1 или больше 20% ячеек меньше 5 (правило Кокрена). Только для
    невзвешенных данных: взвешенному нужен Rao–Scott.
    """
    table = np.asarray([list(row) for row in counts], dtype=float)
    bases = tuple(int(value) for value in table.sum(axis=0)) if table.size else ()
    small = [base for base in bases if base < minimum_base]
    if small:
        return skipped_overall(
            CHI_SQUARE, confidence_level, bases, f"База колонки меньше {minimum_base}."
        )
    table = table[table.sum(axis=1) > 0]
    if table.size:
        table = table[:, table.sum(axis=0) > 0]
    if table.ndim != 2 or table.shape[0] < 2 or table.shape[1] < 2:
        return skipped_overall(
            CHI_SQUARE,
            confidence_level,
            bases,
            "Нужны хотя бы два ответа и две колонки с респондентами.",
        )
    total = table.sum()
    expected = np.outer(table.sum(axis=1), table.sum(axis=0)) / total
    min_expected = float(expected.min())
    if min_expected < 1 or float((expected < 5).mean()) > 0.2:
        return skipped_overall(
            CHI_SQUARE,
            confidence_level,
            bases,
            "Ожидаемые частоты малы: есть меньше 1 или больше 20% ячеек меньше 5.",
            min_expected=min_expected,
        )
    statistic = float(((table - expected) ** 2 / expected).sum())
    degrees = float((table.shape[0] - 1) * (table.shape[1] - 1))
    p_value = float(chi2.sf(statistic, degrees))
    alpha = 1 - confidence_level
    return OverallTestResult(
        method=CHI_SQUARE,
        performed=True,
        significant=p_value < alpha,
        alpha=alpha,
        statistic=statistic,
        degrees_of_freedom=(degrees,),
        p_value=p_value,
        bases=bases,
        min_expected=min_expected,
    )


def welch_anova(
    groups: Iterable[Iterable[float]],
    *,
    confidence_level: float,
    minimum_base: int,
) -> OverallTestResult:
    """Welch ANOVA: различаются ли средние колонок, без равенства дисперсий.

    Тот же отказ от равных дисперсий, что у Welch t-test (инвариант 3).
    """
    samples = [_finite_sample(group) for group in groups]
    bases = tuple(len(sample) for sample in samples)
    if any(base < minimum_base for base in bases):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, f"База колонки меньше {minimum_base}."
        )
    if len(samples) < 2 or any(base < 2 for base in bases):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, "Нужны хотя бы две колонки по два значения."
        )
    variances = np.array([sample.var(ddof=1) for sample in samples])
    if np.any(variances <= 0):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, "В колонке нет разброса значений."
        )
    counts = np.array(bases, dtype=float)
    means = np.array([sample.mean() for sample in samples])
    return _welch_anova_result(counts, means, variances, confidence_level, bases)


def weighted_welch_anova(
    groups: Iterable[Iterable[float]],
    group_weights: Iterable[Iterable[float]],
    *,
    confidence_level: float,
    minimum_base: int,
) -> OverallTestResult:
    """Welch ANOVA на весах: размер группы — её эффективная база Киша.

    То же приближение, что у взвешенного Welch t-test (инвариант 4):
    взвешенные среднее и дисперсия, а вместо числа наблюдений — `n_eff`.
    Порог базы проверяется и по невзвешенной, и по эффективной базе.
    """
    prepared = [
        _weighted_numeric_sample(values, weights)
        for values, weights in zip(groups, group_weights, strict=True)
    ]
    bases = tuple(len(sample) for sample, _ in prepared)
    effective = np.array([effective_sample_size(weights) for _, weights in prepared])
    if any(base < minimum_base for base in bases) or np.any(effective < minimum_base):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, f"База колонки меньше {minimum_base}."
        )
    if len(prepared) < 2 or np.any(effective <= 1):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, "Нужны хотя бы две колонки по два значения."
        )
    means = np.array([np.average(sample, weights=weights) for sample, weights in prepared])
    variances = np.array(
        [
            _weighted_variance(sample, weights, float(mean))
            for (sample, weights), mean in zip(prepared, means, strict=True)
        ]
    )
    if np.any(variances <= 0):
        return skipped_overall(
            WELCH_ANOVA, confidence_level, bases, "В колонке нет разброса значений."
        )
    result = _welch_anova_result(effective, means, variances, confidence_level, bases)
    return replace(result, effective_bases=tuple(float(value) for value in effective))


def _welch_anova_result(
    counts: np.ndarray,
    means: np.ndarray,
    variances: np.ndarray,
    confidence_level: float,
    bases: tuple[int, ...],
) -> OverallTestResult:
    groups_count = len(counts)
    weights = counts / variances
    weight_sum = weights.sum()
    weighted_mean = float((weights * means).sum() / weight_sum)
    between = float((weights * (means - weighted_mean) ** 2).sum() / (groups_count - 1))
    lam = float(((1 - weights / weight_sum) ** 2 / (counts - 1)).sum())
    correction = 1 + 2 * (groups_count - 2) / (groups_count**2 - 1) * lam
    statistic = between / correction
    df1 = float(groups_count - 1)
    df2 = float((groups_count**2 - 1) / (3 * lam))
    p_value = float(fisher_f.sf(statistic, df1, df2))
    alpha = 1 - confidence_level
    return OverallTestResult(
        method=WELCH_ANOVA,
        performed=True,
        significant=p_value < alpha,
        alpha=alpha,
        statistic=statistic,
        degrees_of_freedom=(df1, df2),
        p_value=p_value,
        bases=bases,
    )



@dataclass(frozen=True)
class PairwiseMeanDifference:
    """Одна пара групп апостериорного теста Games–Howell."""

    first: int
    second: int
    difference: float
    statistic: float
    degrees_of_freedom: float
    p_value: float


def games_howell(
    counts: Iterable[float], means: Iterable[float], variances: Iterable[float]
) -> list[PairwiseMeanDifference]:
    """Games–Howell: какие именно пары групп различаются после Welch ANOVA.

    Как Welch, не предполагает равных дисперсий: у каждой пары своя ошибка
    `√(s²ᵢ/nᵢ + s²ⱼ/nⱼ)` и степени свободы Уэлча–Саттертуэйта. Семейство —
    все пары этих групп: p-value берётся из распределения стьюдентизированного
    размаха для `k` групп, поэтому отдельная поправка на множественность не
    нужна. При двух группах p-value совпадает с Welch t-test. На весах сюда
    передаются эффективные базы и взвешенные дисперсии.
    """
    sizes = np.asarray(list(counts), dtype=float)
    centres = np.asarray(list(means), dtype=float)
    spreads = np.asarray(list(variances), dtype=float)
    groups_count = len(sizes)
    pairs = []
    for first in range(groups_count):
        for second in range(first + 1, groups_count):
            terms = spreads[first] / sizes[first], spreads[second] / sizes[second]
            standard_error = math.sqrt(terms[0] + terms[1])
            difference = float(centres[first] - centres[second])
            statistic = difference / standard_error if standard_error else 0.0
            degrees = (terms[0] + terms[1]) ** 2 / (
                terms[0] ** 2 / (sizes[first] - 1) + terms[1] ** 2 / (sizes[second] - 1)
            )
            p_value = float(
                studentized_range.sf(abs(statistic) * math.sqrt(2), groups_count, degrees)
            )
            pairs.append(
                PairwiseMeanDifference(
                    first=first,
                    second=second,
                    difference=difference,
                    statistic=statistic,
                    degrees_of_freedom=float(degrees),
                    p_value=min(1.0, p_value),
                )
            )
    return pairs


def weighted_correlation(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray, *, ranks: bool = False
) -> tuple[float, float, float]:
    """Взвешенная корреляция и её приближённый p-value через `n_eff`.

    Пирсон — через взвешенную ковариацию; Спирмен (`ranks=True`) — Пирсон
    взвешенных рангов, где ранг значения — середина его накопленного веса.
    p-value — по `t = r·√((n_eff − 2)/(1 − r²))` с `n_eff − 2` степенями
    свободы (`requirements.md` §11). Возвращает r, p-value и `n_eff`.
    """
    if ranks:
        x = _weighted_ranks(x, weights)
        y = _weighted_ranks(y, weights)
    total = weights.sum()
    mean_x = float((weights * x).sum() / total)
    mean_y = float((weights * y).sum() / total)
    covariance = float((weights * (x - mean_x) * (y - mean_y)).sum())
    spread = math.sqrt(
        float((weights * (x - mean_x) ** 2).sum()) * float((weights * (y - mean_y) ** 2).sum())
    )
    r = covariance / spread if spread else 0.0
    r = max(-1.0, min(1.0, r))
    effective = effective_sample_size(weights)
    if effective <= 2 or abs(r) >= 1:
        return r, 0.0 if abs(r) >= 1 else 1.0, effective
    statistic = r * math.sqrt((effective - 2) / (1 - r**2))
    return r, float(2 * student_t.sf(abs(statistic), effective - 2)), effective


def _weighted_ranks(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    position = 0
    cumulative = 0.0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        tied = order[position : end + 1]
        tied_weight = float(weights[tied].sum())
        ranks[tied] = cumulative + (tied_weight + 1) / 2
        cumulative += tied_weight
        position = end + 1
    return ranks
