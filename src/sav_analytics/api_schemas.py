from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .core.models import QuestionType, VariableRole


class QuestionUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=5000)
    question_type: QuestionType | None = None
    role: VariableRole | None = None
    included_in_report: bool | None = None
    special_values: list[str | int | float] | None = None
    special_items: list[str] | None = None
    special_metric: Literal["none", "nps", "csat"] | None = None
    # Коды, которыми в массиве помечен пропуск по ветке анкеты. В отличие от
    # special_values они убираются и из распределения, и из валидной базы.
    not_applicable_values: list[str | int | float] | None = Field(
        default=None, max_length=100
    )


class QuestionOrder(BaseModel):
    codes: list[str]


class NotApplicableMark(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    values: list[str | int | float] = Field(default_factory=list, max_length=100)


class NotApplicableUpdate(BaseModel):
    # Подтверждение идёт группой: заглушка обычно лежит сразу в десятках
    # вопросов, и поштучные запросы конфликтовали бы по ревизии конфигурации.
    marks: list[NotApplicableMark] = Field(min_length=1, max_length=500)


class RangeCategory(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> RangeCategory:
        if self.lower is None and self.upper is None:
            raise ValueError("Укажите хотя бы одну границу диапазона.")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Нижняя граница не может быть выше верхней.")
        return self


class NumericRecodeDefinition(BaseModel):
    mode: Literal["ranges"] = "ranges"
    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=500)
    source_variable: str = Field(min_length=1, max_length=64)
    categories: list[RangeCategory] = Field(min_length=2, max_length=100)


class CategoryGroup(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    values: list[str | int | float] = Field(min_length=1, max_length=500)


class CategoricalRecodeDefinition(BaseModel):
    mode: Literal["categories"]
    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=500)
    source_variable: str = Field(min_length=1, max_length=64)
    categories: list[CategoryGroup] = Field(min_length=2, max_length=100)


RecodeDefinition = NumericRecodeDefinition | CategoricalRecodeDefinition


class BannerSource(BaseModel):
    kind: Literal["question", "recoding"]
    ref: str = Field(min_length=1, max_length=64)


class BannerBlock(BaseModel):
    label: str | None = Field(default=None, max_length=250)
    sources: list[BannerSource] = Field(min_length=1, max_length=2)


class ReportSettingsDefinition(BaseModel):
    compare_to_total: bool = False
    # С кем сравнивается подгруппа: с непересекающимся остатком (по умолчанию)
    # или с самим Total, как это делают клиентские макросы.
    compare_target: Literal["rest", "total"] = "rest"
    compare_pairwise: bool = False
    confidence_level: float = Field(default=0.95, gt=0, lt=1)
    bonferroni: bool = False
    minimum_base: int = Field(default=30, ge=1, le=100_000)
    weight_variable: str | None = Field(default=None, max_length=64)
    calculated_weight_id: UUID | None = None
    wave_comparison: Literal["none", "previous", "control"] = "none"
    wave_control_value: str | int | float | None = None

    @model_validator(mode="after")
    def validate_weight_selection(self) -> Self:
        if self.weight_variable and self.calculated_weight_id:
            raise ValueError("Выберите готовый или рассчитанный вес, но не оба сразу.")
        if self.wave_comparison == "control" and self.wave_control_value is None:
            raise ValueError("Для контрольного сравнения выберите контрольную волну.")
        return self


class BannerDefinition(ReportSettingsDefinition):
    """Структура баннера.

    Поля настроек отчёта унаследованы только для чтения старых клиентов API.
    Новый интерфейс сохраняет их через отдельный endpoint и отправляет сюда
    исключительно название и блоки колонок.
    """

    name: str = Field(min_length=1, max_length=500)
    blocks: list[BannerBlock] = Field(min_length=1, max_length=50)


class ReportBannerUpdate(BaseModel):
    banner_id: UUID | None = None


class WeightTarget(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    values: list[str | int | float] = Field(min_length=1, max_length=500)
    percent: float = Field(gt=0, le=100)


class WeightDimension(BaseModel):
    variable: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=500)
    targets: list[WeightTarget] = Field(min_length=2, max_length=100)


class CalculatedWeightDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    dimensions: list[WeightDimension] = Field(min_length=1, max_length=20)
    lower_bound: float | None = Field(default=0.3, gt=0)
    upper_bound: float | None = Field(default=3.0, gt=0)
    tolerance: float = Field(default=0.001, gt=0, lt=1)
    maximum_iterations: int = Field(default=500, ge=1, le=5000)

    @model_validator(mode="after")
    def validate_limits(self) -> CalculatedWeightDefinition:
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound >= self.upper_bound
        ):
            raise ValueError("Нижняя граница веса должна быть меньше верхней.")
        return self


class FilterSource(BaseModel):
    kind: Literal["question", "recoding"]
    ref: str = Field(min_length=1, max_length=64)


class FilterCondition(BaseModel):
    kind: Literal["condition"] = "condition"
    source: FilterSource
    operator: Literal[
        "eq", "ne", "in", "not_in", "gt", "lt", "between", "filled", "missing",
        "selected", "selected_any", "selected_all", "selected_none",
    ]
    values: list[str | int | float] = Field(default_factory=list, max_length=500)
    lower: float | None = None
    upper: float | None = None


class FilterGroup(BaseModel):
    kind: Literal["group"] = "group"
    operator: Literal["and", "or"] = "and"
    items: list[FilterCondition | FilterGroup] = Field(min_length=1, max_length=50)


class FilterDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    rule: FilterGroup


class QuestionBaseUpdate(BaseModel):
    filter_id: UUID | None = None
