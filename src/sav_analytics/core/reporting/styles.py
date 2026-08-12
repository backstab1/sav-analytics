from __future__ import annotations

from typing import Any

from ..statistics import StatisticalTestResult

FONT = "Calibri"
INK = "#1C2521"
MUTED = "#78837E"
FAINT = "#A8B0AC"
ABSENT = "#C4CAC7"
HAIR = "#DFE4E1"
RULE = "#B9C3BD"
ACCENT = "#2E5E4A"
SOFT = "#F5F8F6"
POSITIVE = "#17724A"
NEGATIVE = "#AE382B"
BAR = "#CFE3D8"

UP = "▴"
DOWN = "▾"

LABEL_WIDTH = 40
COLUMN_WIDTH = 10
ROW_HEIGHT = 15.0
QUESTION_HEIGHT = 30.0
#: 15 — дефолтная высота строки Excel, и xlsxwriter в этом случае не пишет
#: ``customHeight``: строка остаётся авторазмерной и растягивается под перенос
#: подписи. Сдвинутый дефолт заставляет записать высоту явно и прибивает её.
DEFAULT_ROW_HEIGHT = 14.4

#: Строки показателей складываются под строку вопроса: сводная строка стоит
#: над деталями, поэтому в :func:`outline_settings` symbols_below выключен.
OUTLINE_DETAIL = {"level": 1}

COMMENT_BOX = {
    "author": "sav-analytics",
    "color": "#FBFCFB",
    "font_name": FONT,
    "font_size": 9,
    "width": 210,
    "height": 88,
}

#: При включённом выводе p-value в примечание уходит полный протокол теста —
#: тот же текст, что в `statistics.txt`. Коробка под сводку из двух строк для
#: него мала, и Excel обрезает содержимое, а не прокручивает его.
COMMENT_BOX_DETAILED = {**COMMENT_BOX, "width": 340, "height": 320}


class ReportFormats:
    """Форматы книги, собираемые по требованию.

    Комбинаций набирается больше, чем удобно перечислять руками: семейство
    (доля или среднее) × направление против тотала × малая база × маркер волны
    × производная строка × левая линейка блока. Поэтому формат описывается
    набором свойств, а одинаковые наборы переиспользуются через кэш — иначе
    xlsxwriter заводит новый объект на каждый вызов.
    """

    def __init__(self, workbook: Any) -> None:
        self._workbook = workbook
        self._cache: dict[tuple[tuple[str, Any], ...], Any] = {}

    def get(self, **properties: Any) -> Any:
        key = tuple(sorted(properties.items()))
        if key not in self._cache:
            self._cache[key] = self._workbook.add_format(
                {"font_name": FONT, "font_size": 10, **properties}
            )
        return self._cache[key]

    # ------------------------------------------------------------------ шапка
    def title(self) -> Any:
        return self.get(font_size=14, bold=True, font_color=INK, valign="vcenter")

    def meta(self) -> Any:
        return self.get(font_size=9, font_color=MUTED, align="left", valign="vcenter")

    def block(self) -> Any:
        return self.get(
            font_size=8,
            bold=True,
            font_color=MUTED,
            align="center",
            bottom=1,
            bottom_color=HAIR,
        )

    def column_label(self, *, separated: bool = False) -> Any:
        return self.get(
            font_size=9,
            bold=True,
            font_color=INK,
            align="center",
            valign="vcenter",
            text_wrap=True,
            **self._divider(separated),
        )

    def column_letter(self) -> Any:
        return self.get(font_size=8, font_color=MUTED, align="center")

    def base(self, *, separated: bool = False, rule: bool = True) -> Any:
        """Строка базы в шапке.

        При включённом весе таких строк две, и линейка под шапкой принадлежит
        нижней из них: ``rule=False`` убирает её у верхней.
        """
        return self.get(
            font_size=9,
            font_color=MUTED,
            align="center",
            num_format="#,##0",
            **self._rule(rule),
            **self._divider(separated),
        )

    def base_label(self, *, rule: bool = True) -> Any:
        return self.get(font_size=8, font_color=MUTED, **self._rule(rule))

    # ------------------------------------------------------------------- тело
    def question(self) -> Any:
        """Формулировка вопроса: пишется только в колонку A, поверх — линейка."""
        return self.get(
            font_size=11,
            bold=True,
            font_color=INK,
            align="left",
            valign="top",
            text_wrap=True,
            top=2,
            top_color=ACCENT,
        )

    def question_rule(self) -> Any:
        """Продолжение линейки над вопросом по колонкам баннера."""
        return self.get(top=2, top_color=ACCENT)

    def row_label(self) -> Any:
        return self.get(
            font_color=INK,
            align="left",
            valign="top",
            text_wrap=True,
            bottom=1,
            bottom_color=HAIR,
        )

    def subquestion(self) -> Any:
        return self.get(
            bold=True,
            font_color=ACCENT,
            bg_color=SOFT,
            align="left",
            valign="top",
            text_wrap=True,
            bottom=1,
            bottom_color=HAIR,
        )

    def derived_label(self) -> Any:
        return self.subquestion()

    def value(
        self,
        family: str,
        *,
        direction: str | None = None,
        small: bool = False,
        wave: str | None = None,
        separated: bool = False,
        derived: bool = False,
    ) -> Any:
        """Числовая ячейка.

        Отличие от тотала несёт цвет цифры, отличие от волны — маркер слева.
        Маркер задан префиксом числового формата; ячейки без маркера получают
        отбивку ``_▴_`` шириной ровно в маркер, поэтому цифры всех колонок
        стоят на одной вертикали.
        """
        digits = "0.0" if family == "mean" else "0"
        if wave == "higher":
            num_format = f'"{UP} "{digits}'
        elif wave == "lower":
            num_format = f'"{DOWN} "{digits}'
        else:
            num_format = f"_{UP}_ {digits}"
        properties: dict[str, Any] = {
            "num_format": num_format,
            **self._frame(separated=separated, derived=derived),
        }
        if small:
            properties["font_color"] = FAINT
        else:
            properties["font_color"] = {
                "higher": POSITIVE,
                "lower": NEGATIVE,
            }.get(direction, INK)
            properties["bold"] = direction in {"higher", "lower"}
        return self.get(**properties)

    def absent(self, *, separated: bool = False, derived: bool = False) -> Any:
        """Нет значения: бледное тире вместо пустоты, чтобы строка не рвалась."""
        return self.get(
            font_color=ABSENT,
            **self._frame(separated=separated, derived=derived),
        )

    # ------------------------------------------------------------- содержание
    def contents_header(self) -> Any:
        return self.get(font_size=9, bold=True, font_color=MUTED, bottom=2, bottom_color=RULE)

    def link(self) -> Any:
        return self.get(bold=True, font_color=ACCENT, bottom=1, bottom_color=HAIR)

    def contents_label(self) -> Any:
        return self.get(font_color=INK, bottom=1, bottom_color=HAIR)

    def contents_sheet(self) -> Any:
        return self.get(font_size=9, font_color=MUTED, bottom=1, bottom_color=HAIR)

    def legend(self) -> Any:
        return self.get(font_size=9, font_color=MUTED)

    # ------------------------------------------------------------ внутреннее
    def _frame(self, *, separated: bool, derived: bool) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "align": "right",
            "valign": "vcenter",
            "bottom": 1,
            "bottom_color": HAIR,
        }
        if derived:
            properties["bg_color"] = SOFT
        properties.update(self._divider(separated))
        return properties

    def _divider(self, separated: bool) -> dict[str, Any]:
        return {"left": 1, "left_color": HAIR} if separated else {}

    def _rule(self, rule: bool) -> dict[str, Any]:
        return {"bottom": 2, "bottom_color": RULE} if rule else {}


def _formats(workbook: Any) -> ReportFormats:
    return ReportFormats(workbook)


def _result_format(
    formats: ReportFormats,
    family: str,
    base: int,
    result: StatisticalTestResult | None,
    settings: dict[str, Any],
    wave_result: StatisticalTestResult | None = None,
    *,
    separated: bool = False,
    derived: bool = False,
) -> Any:
    small = 0 < base < settings["minimum_base"]
    direction = None
    if not small and result is not None and result.significant:
        if result.direction in {"higher", "lower"}:
            direction = result.direction
    wave = None
    if wave_result is not None and wave_result.significant:
        if wave_result.direction in {"higher", "lower"}:
            wave = wave_result.direction
    return formats.value(
        family,
        direction=direction,
        small=small,
        wave=wave,
        separated=separated,
        derived=derived,
    )
