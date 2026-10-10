"""Презентация PPTX рядом с книгой (PQ.13) без новых зависимостей.

Слайд на вопрос — одна таблица: строки — частоты или средние вопроса,
колонка Total нарисована полосами, остальные колонки — числа по всему
баннеру книги с цветом значимости и буквами колонок.

Числа не считаются заново — они читаются из записи листа `topline_main`,
которую пишет тот же `write_topline`, что и книгу, на тех же данных. Цвет
значимости и буквы приходят из формата и примечания ячейки, поэтому слайд
совпадает с книгой и `statistics.txt` по построению.

Таблица собрана фигурами, а не таблицей PowerPoint: строка таблицы растёт
сама, когда подпись переносится, и полоса Total уехала бы от своей строки.
Высоту строки здесь задаёт код. Оформление нейтральное; хороший эталон
презентации — отдельная задача.
"""

from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

from ..report_books import active_book_name
from ..report_settings import resolved_report_settings
from .data import ReportData
from .excel_layout import banner_blocks, write_topline
from .parameters import report_parameters
from .recording import RecordingSheet, RecordingWorkbook, table_columns, table_questions
from .styles import report_formats

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_TABLE = "http://schemas.openxmlformats.org/drawingml/2006/table"

INK = "1D1D1F"
MUTED = "6B6B6B"
FAINT = "A6A6A6"
BRAND = "1D7A4F"
BAR = "8CC2A8"
HIGHER = "1D7A4F"
LOWER = "C93A3A"
RULE = "D9DFDC"
HEAD = "F2F4F3"

EMU = 914400  # на дюйм
SLIDE_WIDTH = 12192000
SLIDE_HEIGHT = 6858000

LEFT = 0.3
WIDTH = 12.73
TABLE_TOP = 1.2
TABLE_BOTTOM = 6.95
#: Насколько строка может вырасти, растягивая таблицу на высоту слайда.
MAX_STRETCH = 0.45
ROW_HEIGHT = 0.3
LINE_HEIGHT = 0.17
BLOCK_HEIGHT = 0.28
HEAD_LINE = 0.16
MAX_HEAD_LINES = 4
#: Подписи строк и полосы Total при немногих колонках баннера и при многих.
WIDE_LABEL = 3.1
WIDE_BAR = 3.2
NARROW_LABEL = 2.3
NARROW_BAR = 2.2
#: Уже этого колонка баннера при широких полосах не делается — сужаются полосы.
WIDE_COLUMN_MIN = 0.9
#: Шире колонка не нужна: число и буквы помещаются с запасом.
MAX_COLUMN = 1.6
#: Уже число с буквами не помещается; колонки уходят на продолжение слайда.
MIN_COLUMN = 0.6
#: Строк параметров на слайде.
PARAMETER_ROWS = 16

MEAN_LABEL = "Среднее"


# --------------------------------------------------------------- содержание
@dataclass
class Cell:
    text: str
    #: Буквы колонок, выше которых ячейка значимо; строчные — второй уровень.
    letters: str = ""
    direction: str | None = None
    small: bool = False


@dataclass
class Row:
    label: str
    value: float | None
    cells: list[Cell]


@dataclass
class Layout:
    """Геометрия таблицы слайда в дюймах: от неё зависят и строки, и колонки."""

    label_width: float
    bar_width: float
    column_width: float
    head_height: float

    @property
    def total_x(self) -> float:
        return LEFT + self.label_width

    @property
    def columns_x(self) -> float:
        return self.total_x + self.bar_width


@dataclass
class QuestionSlide:
    code: str
    title: str
    #: Что в строках: «% от всех опрошенных» или «Среднее».
    measure: str
    #: Total и колонки баннера этого слайда; у каждой — буква, подпись, база, блок.
    columns: list[dict[str, Any]]
    rows: list[Row]
    note: str
    layout: Layout
    part: int = 1
    parts: int = 1
    #: Масштаб полос: наибольшее значение Total во всём вопросе.
    scale: float = 0.0


@dataclass
class Presentation:
    title: str
    subtitle: list[str]
    slides: list[QuestionSlide] = field(default_factory=list)
    parameters: list[tuple[str, str]] = field(default_factory=list)


def presentation_from_data(data: ReportData, project: dict[str, Any]) -> Presentation:
    """Содержание презентации по записи листа `topline_main`.

    Лист пишется тем же `write_topline` и с теми же данными, что в книге, но
    в память: числа, цвет значимости и буквы — те же, что на листе.
    """
    recording = RecordingSheet()
    positions = write_topline(
        recording,
        data,
        project,
        data.questions,
        report_formats(RecordingWorkbook(), data.statistical_settings),
        [],
        "topline_main",
        valid_denominator=False,
    )
    header_rows = 6 if data.statistical_settings["weights"] is not None else 5
    columns = table_columns(recording, data, header_rows)
    for start, end, block in banner_blocks(data.columns):
        for index in range(start - 1, end):
            columns[index]["block"] = block or ""
    questions = table_questions(recording, data.questions, positions, len(data.columns))
    settings = resolved_report_settings(data.configuration, data.active_banner)
    note = _note(columns, settings, data.statistical_settings["weight_label"])

    banner = (data.active_banner or {}).get("name")
    subtitle = [
        " · ".join(
            part
            for part in (active_book_name(data.configuration), date.today().strftime("%d.%m.%Y"))
            if part
        ),
        f"Разрез: {banner}" if banner else "Разрез не задан — только Total",
        f"База: {_number(columns[0]['base'], 0)} опрошенных",
    ]
    if data.statistical_settings["weight_label"]:
        subtitle.append(f"Вес: {data.statistical_settings['weight_label']}")
    result = Presentation(
        title=str(project.get("name") or "Отчёт"),
        subtitle=subtitle,
        parameters=report_parameters(project, data),
    )
    for question in questions:
        result.slides.extend(_question_slides(question, columns, note))
    return result


def _question_slides(
    question: dict[str, Any], columns: list[dict[str, Any]], note: str
) -> list[QuestionSlide]:
    """Слайды вопроса: строки и колонки, которые не поместились, — на следующих.

    Колонки баннера делятся на части по ширине, и в каждой части Total стоит
    первым; строки делятся по высоте. Число на слайде не урезается и не
    прячется — оно переезжает на продолжение.
    """
    label = str(question["label"])
    measure, picked = _rows(question["rows"], label)
    if not picked:
        return []
    percent = measure.startswith("%")
    rows = [
        Row(name, row["cells"][0].get("value"), [_cell(cell, percent) for cell in row["cells"]])
        for name, row in picked
    ]
    scale = max((row.value for row in rows if row.value is not None), default=0.0)

    slides: list[tuple[list[int], Layout, list[Row]]] = []
    pages = _column_pages(columns)
    # Одна раскладка на все слайды вопроса: полосы Total одной длины, и
    # продолжение читается рядом с началом.
    widest = max(pages, key=len)
    layout = _layout(
        [columns[0], *(columns[index] for index in widest)],
        [columns[index] for page in pages for index in page],
    )
    for indexes in pages:
        shown = [0, *indexes]
        page_rows = [
            Row(row.label, row.value, [row.cells[index] for index in shown]) for row in rows
        ]
        for chunk in _paginate(page_rows, layout):
            slides.append((shown, layout, chunk))
    return [
        QuestionSlide(
            code=str(question["code"]),
            title=label,
            measure=measure,
            columns=[columns[index] for index in shown],
            rows=chunk,
            note=note,
            layout=layout,
            part=number,
            parts=len(slides),
            scale=float(scale),
        )
        for number, (shown, layout, chunk) in enumerate(slides, start=1)
    ]


def _column_pages(columns: list[dict[str, Any]]) -> list[list[int]]:
    """Номера колонок баннера по слайдам: сколько влезает по ширине.

    Блок баннера не разрывается между слайдами, пока помещается целиком:
    «Москва» на одном слайде и остальные регионы на другом не сравнить.
    """
    count = len(columns) - 1
    if count == 0:
        return [[]]
    per_page = max(1, int((WIDTH - NARROW_LABEL - NARROW_BAR) // MIN_COLUMN))
    if count <= per_page:
        return [list(range(1, count + 1))]
    blocks: list[list[int]] = []
    for index in range(1, count + 1):
        if blocks and columns[index].get("block") == columns[blocks[-1][-1]].get("block"):
            blocks[-1].append(index)
        else:
            blocks.append([index])
    # Слайды наполняются поровну: 7 и 8 колонок читаются лучше, чем 11 и 4.
    # Если ровная доля дробит блоки на лишний слайд — заполняем до предела.
    needed = math.ceil(count / per_page)
    even = _pack(blocks, math.ceil(count / needed))
    return even if len(even) == needed else _pack(blocks, per_page)


def _pack(blocks: list[list[int]], limit: int) -> list[list[int]]:
    pages: list[list[int]] = [[]]
    for block in blocks:
        rest = list(block)
        while rest:
            if pages[-1] and len(pages[-1]) + len(rest) > limit:
                pages.append([])
            take = limit - len(pages[-1])
            pages[-1].extend(rest[:take])
            rest = rest[take:]
    return pages


def _layout(columns: list[dict[str, Any]], every: list[dict[str, Any]]) -> Layout:
    """Раскладка по самому широкому слайду вопроса; шапка — по всем его колонкам."""
    count = len(columns) - 1
    if count == 0:
        return Layout(WIDE_LABEL, WIDTH - WIDE_LABEL, 0.0, _head_height(every, 1.0))
    wide = WIDTH - WIDE_LABEL - WIDE_BAR
    if wide / count >= WIDE_COLUMN_MIN:
        label, bar = WIDE_LABEL, WIDE_BAR
    else:
        label, bar = NARROW_LABEL, NARROW_BAR
    width = min(MAX_COLUMN, (WIDTH - label - bar) / count)
    # Колонок мало — лишняя ширина уходит полосам Total, а не пустоте справа.
    bar = WIDTH - label - width * count
    return Layout(label, bar, width, _head_height(every, width))


def _wrap_lines(text: str, width: float, size: float, *, bold: bool = False) -> int:
    """Сколько строк займёт текст при переносе по словам.

    Средний знак Arial в кириллице — около 0,55 кегля, полужирного — 0,62;
    оценка с запасом: недооценка выдавливает подпись за шапку. Слово длиннее
    строки PowerPoint режет по буквам, и оно занимает несколько строк.
    """
    per_line = max(1, int(width * 72 / (size * (0.62 if bold else 0.55))))
    lines, used = 1, 0
    for word in text.split():
        length = len(word)
        if used and used + 1 + length > per_line:
            lines += 1
            used = 0
        while length > per_line:
            lines += 1
            length -= per_line
        used += length + (1 if used else 0)
    return lines


def _head_height(columns: list[dict[str, Any]], width: float) -> float:
    """Высота шапки: строка буквы колонки и подпись с переносом по словам."""
    lines = max(
        (_wrap_lines(str(column["label"]), width - 0.08, 8, bold=True) for column in columns),
        default=1,
    )
    return HEAD_LINE * (1 + min(MAX_HEAD_LINES, lines)) + 0.12


def _rows(
    rows: list[dict[str, Any]], label: str
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Строки слайда: частоты вопроса или средние.

    У матрицы — среднее каждого подвопроса, у вопроса с частотами — они,
    у числового — его среднее. Top/Bottom, NPS и остальные показатели
    остаются в книге: слайд показывает распределение или средние.
    """
    groups = _subquestions(rows)
    if groups and groups[0][0] is not None:
        means = [
            (_short(sub or "", label), row)
            for sub, items in groups
            for row in items
            if row["label"] == MEAN_LABEL
        ]
        return "Среднее", means
    plain = [
        (_short(str(row["label"]), label), row)
        for row in rows
        if row["kind"] == "value" and not row["derived"]
    ]
    if plain:
        return "% от всех опрошенных", plain
    return "Среднее", [(MEAN_LABEL, row) for row in rows if row["label"] == MEAN_LABEL]


def _subquestions(rows: list[dict[str, Any]]) -> list[tuple[str | None, list[dict[str, Any]]]]:
    groups: list[tuple[str | None, list[dict[str, Any]]]] = []
    for row in rows:
        if row["kind"] == "subquestion":
            groups.append((str(row["label"]), []))
        elif not groups:
            groups.append((None, [row]))
        else:
            groups[-1][1].append(row)
    return groups


def _short(text: str, question: str) -> str:
    """Подпись без повтора формулировки вопроса: «Вопрос: вариант» → «вариант»."""
    prefix = f"{question}: "
    return text[len(prefix):] if text.startswith(prefix) and len(text) > len(prefix) else text


def _cell(cell: dict[str, Any], percent: bool) -> Cell:
    value = cell.get("value")
    if value is None:
        return Cell("–")
    letters = "".join(cell.get("higher_than") or []) + "".join(
        letter.lower() for letter in cell.get("higher_than_secondary") or []
    )
    text = _number(float(value), int(cell.get("decimals") or 0)) + ("%" if percent else "")
    return Cell(text, letters, cell.get("direction"), bool(cell.get("small")))


def _row_height(row: Row, layout: Layout) -> float:
    lines = min(3, _wrap_lines(row.label, layout.label_width - 0.16, 10))
    return ROW_HEIGHT + LINE_HEIGHT * (lines - 1)


def _table_top(layout: Layout) -> float:
    """Где начинаются строки: под строкой блоков, шапкой и строкой базы."""
    return TABLE_TOP + BLOCK_HEIGHT + layout.head_height + ROW_HEIGHT


def _paginate(rows: list[Row], layout: Layout) -> list[list[Row]]:
    room = TABLE_BOTTOM - _table_top(layout)
    pages: list[list[Row]] = [[]]
    used = 0.0
    for row in rows:
        height = _row_height(row, layout)
        if pages[-1] and used + height > room:
            pages.append([])
            used = 0.0
        pages[-1].append(row)
        used += height
    return pages


def _number(value: float, decimals: int) -> str:
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def _note(columns: list[dict[str, Any]], settings: dict[str, Any], weight: str | None) -> str:
    total = columns[0]
    parts = [f"База: все опрошенные, N = {_number(total['base'], 0)}"]
    if total.get("weighted_base") is not None:
        parts[-1] += f", взвеш. {_number(total['weighted_base'], 0)}"
    if weight:
        parts.append(f"вес: {weight}")
    level = f"{settings['confidence_level'] * 100:g}%"
    if len(columns) > 1 and settings["compare_to_total"]:
        target = "Total" if settings["compare_target"] == "total" else "остальных"
        parts.append(f"цвет — значимо выше или ниже {target} при {level}")
    if len(columns) > 1 and settings["compare_pairwise"]:
        parts.append(f"буквы — значимо выше этих колонок при {level}")
    if len(columns) > 1:
        parts.append(f"бледным — база меньше {settings['minimum_base']}")
    return "; ".join(parts) + "."


# ------------------------------------------------------------------ PPTX
def _text(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def _emu(inches: float) -> int:
    return int(round(inches * EMU))


def _run(text: str, *, size: int, bold: bool = False, color: str = INK) -> str:
    return (
        f'<a:r><a:rPr lang="ru-RU" sz="{size}" b="{1 if bold else 0}" dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:latin typeface="Arial"/><a:cs typeface="Arial"/></a:rPr>'
        f"<a:t>{_text(text)}</a:t></a:r>"
    )


def _paragraph(text: str, *, size: int, bold: bool = False, color: str = INK,
               align: str = "l", space_after: int = 0) -> str:
    spacing = f'<a:spcAft><a:spcPts val="{space_after}"/></a:spcAft>' if space_after else ""
    return (
        f'<a:p><a:pPr algn="{align}">{spacing}</a:pPr>'
        f'{_run(text, size=size, bold=bold, color=color)}</a:p>'
    )


class _Shapes:
    """Фигуры слайда с номерами по порядку."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def _next(self) -> int:
        return len(self.items) + 2

    def text(self, name: str, x: float, y: float, w: float, h: float, paragraphs: str,
             *, anchor: str = "t") -> None:
        self.items.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{self._next()}" name="{_text(name)}"/>'
            '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
            f'<a:ext cx="{_emu(w)}" cy="{_emu(h)}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" '
            f'anchor="{anchor}"><a:noAutofit/></a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody></p:sp>'
        )

    def rect(self, name: str, x: float, y: float, w: float, h: float, color: str) -> None:
        self.items.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{self._next()}" name="{_text(name)}"/>'
            "<p:cNvSpPr/><p:nvPr/></p:nvSpPr>"
            f'<p:spPr><a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
            f'<a:ext cx="{max(1, _emu(w))}" cy="{_emu(h)}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:ln><a:noFill/></a:ln>'
            "</p:spPr></p:sp>"
        )

    def line(self, x: float, y: float, w: float) -> None:
        self.items.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{self._next()}" name="Линейка"/>'
            "<p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>"
            f'<p:spPr><a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
            f'<a:ext cx="{_emu(w)}" cy="0"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
            f'<a:ln w="6350"><a:solidFill><a:srgbClr val="{RULE}"/></a:solidFill></a:ln>'
            "</p:spPr></p:cxnSp>"
        )

    def vline(self, x: float, y: float, h: float) -> None:
        self.items.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{self._next()}" name="Граница блока"/>'
            "<p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>"
            f'<p:spPr><a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
            f'<a:ext cx="0" cy="{_emu(h)}"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
            f'<a:ln w="6350"><a:solidFill><a:srgbClr val="{RULE}"/></a:solidFill></a:ln>'
            "</p:spPr></p:cxnSp>"
        )

    def xml(self) -> str:
        return "".join(self.items)


def _slide(shapes: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld><p:spTree>'
        f"{_GROUP}{shapes}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _title_slide(presentation: Presentation) -> str:
    shapes = _Shapes()
    shapes.text("Заголовок", 0.8, 2.3, 11.7, 1.2,
                _paragraph(presentation.title, size=3600, bold=True), anchor="b")
    shapes.text("Подзаголовок", 0.8, 3.7, 11.7, 2.4, "".join(
        _paragraph(line, size=1600, color=MUTED, space_after=600)
        for line in presentation.subtitle
    ))
    return _slide(shapes.xml())


def _question_slide_xml(slide: QuestionSlide) -> str:
    shapes = _Shapes()
    layout = slide.layout
    title = slide.title + (f" ({slide.part} из {slide.parts})" if slide.parts > 1 else "")
    shapes.text("Заголовок", LEFT, 0.25, WIDTH, 0.9,
                _paragraph(slide.code, size=1100, color=MUTED)
                + _paragraph(title, size=2000, bold=True))

    # Подписи и полосы общие для слайдов вопроса, а колонки баннера делят
    # оставшуюся ширину своего слайда: продолжение не оставляет пустоты справа.
    count = len(slide.columns) - 1
    column_width = (
        min(MAX_COLUMN, (WIDTH - layout.label_width - layout.bar_width) / count)
        if count else 0.0
    )

    def column_x(index: int) -> tuple[float, float]:
        if index == 0:
            return layout.total_x, layout.bar_width
        return layout.columns_x + column_width * (index - 1), column_width

    # Кегль — от раскладки вопроса, а не от слайда: продолжение набрано так же.
    many = layout.column_width < WIDE_COLUMN_MIN
    head_size = 800 if many else 900

    # Строка блоков баннера: подпись над своими колонками, как в книге.
    top = TABLE_TOP
    start = 1
    while start < len(slide.columns):
        block = slide.columns[start].get("block", "")
        end = start
        while end + 1 < len(slide.columns) and slide.columns[end + 1].get("block", "") == block:
            end += 1
        x, _ = column_x(start)
        width = column_width * (end - start + 1)
        if block:
            shapes.text("Блок баннера", x + 0.03, top, width - 0.06, BLOCK_HEIGHT,
                        _paragraph(block, size=head_size, bold=True, color=MUTED, align="ctr"),
                        anchor="b")
        start = end + 1
    top += BLOCK_HEIGHT

    # Шапка: подпись показателя, затем буква и название каждой колонки.
    head = layout.head_height
    shapes.rect("Шапка", LEFT, top, WIDTH, head, HEAD)
    shapes.text("Показатель", LEFT + 0.08, top, layout.label_width - 0.1, head,
                _paragraph(slide.measure, size=1000, bold=True, color=MUTED), anchor="ctr")
    for index, column in enumerate(slide.columns):
        x, w = column_x(index)
        shapes.text(f"Колонка {column['letter']}", x + 0.04, top + 0.04, w - 0.08, head - 0.08,
                    _paragraph(column["letter"], size=head_size, color=MUTED,
                               align="l" if index == 0 else "ctr")
                    + _paragraph(str(column["label"]), size=head_size, bold=True,
                                 align="l" if index == 0 else "ctr"),
                    anchor="b")
    top += head
    shapes.text("База", LEFT + 0.08, top, layout.label_width - 0.1, ROW_HEIGHT,
                _paragraph("База, N", size=900, color=MUTED), anchor="ctr")
    for index, column in enumerate(slide.columns):
        x, w = column_x(index)
        shapes.text("База колонки", x + 0.04, top, w - 0.08, ROW_HEIGHT,
                    _paragraph(_number(column["base"], 0), size=900,
                               color=FAINT if column["small"] else MUTED,
                               align="l" if index == 0 else "ctr"),
                    anchor="ctr")
    top += ROW_HEIGHT
    shapes.line(LEFT, top, WIDTH)
    body_top = top

    # Таблица растягивается на высоту слайда: свободное место делится
    # между строками поровну, но строка не раздувается больше MAX_STRETCH.
    free = TABLE_BOTTOM - top - sum(_row_height(row, layout) for row in slide.rows)
    stretch = min(MAX_STRETCH, max(0.0, free / max(1, len(slide.rows))))
    size = 1200 if stretch > 0.2 and not many else 1000
    value_size = size if column_width >= 0.8 else 900

    # Полоса Total занимает колонку A, число — справа от полосы.
    track = layout.bar_width - 0.8
    for row in slide.rows:
        height = _row_height(row, layout) + stretch
        bar = min(height - 0.12, 0.42)
        shapes.text("Строка", LEFT + 0.08, top, layout.label_width - 0.16, height,
                    _paragraph(row.label, size=size), anchor="ctr")
        total = row.cells[0]
        if row.value is not None and slide.scale > 0:
            length = track * max(0.0, row.value) / slide.scale
            shapes.rect("Полоса Total", layout.total_x + 0.04, top + (height - bar) / 2,
                        length, bar, BAR)
            shapes.text("Total", layout.total_x + 0.1 + length, top, 0.75, height,
                        _paragraph(total.text, size=size, bold=True), anchor="ctr")
        else:
            shapes.text("Total", layout.total_x + 0.04, top, 0.75, height,
                        _paragraph(total.text, size=size), anchor="ctr")
        for index, cell in enumerate(row.cells[1:], start=1):
            x, w = column_x(index)
            color = FAINT if cell.small else {"higher": HIGHER, "lower": LOWER}.get(
                cell.direction or "", INK
            )
            runs = _run(cell.text, size=value_size, bold=cell.direction is not None, color=color)
            if cell.letters:
                # Буквы мельче и в той же строке: перенос под число путает,
                # к какой ячейке они относятся.
                runs += _run(f" {cell.letters}", size=value_size - 200, bold=True, color=color)
            shapes.text("Значение", x + 0.01, top, w - 0.02, height,
                        f'<a:p><a:pPr algn="ctr"/>{runs}</a:p>', anchor="ctr")
        top += height
        shapes.line(LEFT, top, WIDTH)

    # Границы блоков баннера — вертикальными линейками через всю таблицу.
    for index in range(2, len(slide.columns)):
        if slide.columns[index].get("block") != slide.columns[index - 1].get("block"):
            x, _ = column_x(index)
            shapes.vline(x, TABLE_TOP, top - TABLE_TOP)
    shapes.vline(layout.columns_x, body_top - ROW_HEIGHT, top - body_top + ROW_HEIGHT)

    shapes.text("Сноска", LEFT, 7.0, WIDTH, 0.35,
                _paragraph(slide.note, size=900, color=MUTED), anchor="b")
    return _slide(shapes.xml())


def _table_cell(text: str, *, color: str = INK) -> str:
    border = f'<a:solidFill><a:srgbClr val="{RULE}"/></a:solidFill>'
    return (
        '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>'
        f"{_paragraph(text, size=900, color=color)}</a:txBody>"
        '<a:tcPr marL="45720" marR="45720" marT="22860" marB="22860" anchor="ctr">'
        '<a:lnL w="0"><a:noFill/></a:lnL><a:lnR w="0"><a:noFill/></a:lnR>'
        f'<a:lnT w="6350">{border}</a:lnT><a:lnB w="6350">{border}</a:lnB>'
        "<a:noFill/></a:tcPr></a:tc>"
    )


def _parameters_slide(rows: list[tuple[str, str]], number: int, total: int) -> str:
    title = "Параметры отчёта" + (f" ({number} из {total})" if total > 1 else "")
    first = 3.2
    grid = f'<a:gridCol w="{_emu(first)}"/><a:gridCol w="{_emu(WIDTH - first)}"/>'
    body = "".join(
        f'<a:tr h="{_emu(0.3)}">{_table_cell(name, color=MUTED)}{_table_cell(value)}</a:tr>'
        for name, value in rows
    )
    shapes = _Shapes()
    shapes.text("Заголовок", LEFT, 0.35, WIDTH, 0.8, _paragraph(title, size=2000, bold=True))
    shapes.items.append(
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="90" name="Параметры"/>'
        '<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/>'
        f'</p:nvGraphicFramePr><p:xfrm><a:off x="{_emu(LEFT)}" y="{_emu(1.3)}"/>'
        f'<a:ext cx="{_emu(WIDTH)}" cy="{_emu(0.3 * len(rows))}"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="{_TABLE}"><a:tbl><a:tblPr/>'
        f"<a:tblGrid>{grid}</a:tblGrid>{body}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    return _slide(shapes.xml())


_THEME = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{_A}" name="sav-analytics"><a:themeElements>
<a:clrScheme name="sav-analytics">
<a:dk1><a:srgbClr val="{INK}"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="2B2B2B"/></a:dk2><a:lt2><a:srgbClr val="{HEAD}"/></a:lt2>
<a:accent1><a:srgbClr val="{BRAND}"/></a:accent1><a:accent2><a:srgbClr val="4F9D7A"/></a:accent2>
<a:accent3><a:srgbClr val="{BAR}"/></a:accent3><a:accent4><a:srgbClr val="{LOWER}"/></a:accent4>
<a:accent5><a:srgbClr val="6B6B6B"/></a:accent5><a:accent6><a:srgbClr val="B8860B"/></a:accent6>
<a:hlink><a:srgbClr val="{BRAND}"/></a:hlink><a:folHlink><a:srgbClr val="6B6B6B"/></a:folHlink>
</a:clrScheme>
<a:fontScheme name="sav-analytics">
<a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
</a:fontScheme>
<a:fmtScheme name="sav-analytics">
<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
</a:fmtScheme>
</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>"""

_GROUP = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)

_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldMaster xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
    '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
    f"<a:effectLst/></p:bgPr></p:bg><p:spTree>{_GROUP}</p:spTree></p:cSld>"
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
    'hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "<p:txStyles>"
    '<p:titleStyle><a:lvl1pPr><a:defRPr sz="2400"/></a:lvl1pPr></p:titleStyle>'
    '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1400"/></a:lvl1pPr></p:bodyStyle>'
    '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1400"/></a:lvl1pPr></p:otherStyle>'
    "</p:txStyles></p:sldMaster>"
)

_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldLayout xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" type="blank" preserve="1">'
    f'<p:cSld name="Пустой"><p:spTree>{_GROUP}</p:spTree></p:cSld>'
    "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
)


def _relationships(*items: tuple[str, str, str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_PKG}">'
        + "".join(
            f'<Relationship Id="{rid}" Type="{_R}/{kind}" Target="{target}"/>'
            for rid, kind, target in items
        )
        + "</Relationships>"
    )


def build_pptx(presentation: Presentation) -> bytes:
    """Собрать PPTX: титул, слайд на вопрос, параметры отчёта."""
    slides = [_title_slide(presentation)]
    slides.extend(_question_slide_xml(slide) for slide in presentation.slides)
    pages = [
        presentation.parameters[start : start + PARAMETER_ROWS]
        for start in range(0, len(presentation.parameters), PARAMETER_ROWS)
    ]
    slides.extend(
        _parameters_slide(rows, number, len(pages)) for number, rows in enumerate(pages, start=1)
    )

    overrides = [
        ("/ppt/presentation.xml", "presentationml.presentation.main+xml"),
        ("/ppt/slideMasters/slideMaster1.xml", "presentationml.slideMaster+xml"),
        ("/ppt/slideLayouts/slideLayout1.xml", "presentationml.slideLayout+xml"),
        ("/ppt/theme/theme1.xml", "theme+xml"),
        ("/ppt/presProps.xml", "presentationml.presProps+xml"),
        ("/ppt/tableStyles.xml", "presentationml.tableStyles+xml"),
        ("/docProps/app.xml", "extended-properties+xml"),
    ]
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, xml in enumerate(slides, start=1):
            archive.writestr(f"ppt/slides/slide{number}.xml", xml)
            archive.writestr(
                f"ppt/slides/_rels/slide{number}.xml.rels",
                _relationships(("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")),
            )
            overrides.append((f"/ppt/slides/slide{number}.xml", "presentationml.slide+xml"))

        slide_ids = "".join(
            f'<p:sldId id="{255 + number}" r:id="rIdSlide{number}"/>'
            for number in range(1, len(slides) + 1)
        )
        archive.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdMaster"/></p:sldMasterIdLst>'
            f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
            f'<p:sldSz cx="{SLIDE_WIDTH}" cy="{SLIDE_HEIGHT}"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            _relationships(
                ("rIdMaster", "slideMaster", "slideMasters/slideMaster1.xml"),
                ("rIdTheme", "theme", "theme/theme1.xml"),
                ("rIdProps", "presProps", "presProps.xml"),
                ("rIdTables", "tableStyles", "tableStyles.xml"),
                *(
                    (f"rIdSlide{number}", "slide", f"slides/slide{number}.xml")
                    for number in range(1, len(slides) + 1)
                ),
            ),
        )
        archive.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        archive.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _relationships(
                ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("rId2", "theme", "../theme/theme1.xml"),
            ),
        )
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
        archive.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _relationships(("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")),
        )
        archive.writestr("ppt/theme/theme1.xml", _THEME)
        archive.writestr(
            "ppt/presProps.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentationPr xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"/>',
        )
        archive.writestr(
            "ppt/tableStyles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<a:tblStyleLst xmlns:a="{_A}" def="{{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}}"/>',
        )
        archive.writestr(
            "docProps/app.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'extended-properties"><Application>sav-analytics</Application>'
            f"<Slides>{len(slides)}</Slides></Properties>",
        )
        archive.writestr(
            "_rels/.rels",
            _relationships(
                ("rId1", "officeDocument", "ppt/presentation.xml"),
                ("rId2", "extended-properties", "docProps/app.xml"),
            ),
        )
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join(
                f'<Override PartName="{part}" '
                f'ContentType="application/vnd.openxmlformats-officedocument.{kind}"/>'
                for part, kind in overrides
            )
            + "</Types>",
        )
    return content.getvalue()


__all__ = ["Presentation", "build_pptx", "presentation_from_data"]
