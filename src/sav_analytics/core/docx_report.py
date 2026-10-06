"""DOCX автоотчёта (PQ.18) без новых зависимостей.

Документ собирается из XML WordprocessingML вручную. Графики — родные
диаграммы Word (DrawingML chart) со встроенной книгой Excel с теми же
числами: заказчик правит цвета, подписи и данные прямо в Word («Изменить
данные»). Числа берутся из собранного отчёта — то есть из ядра — и
совпадают с книгой и `statistics.txt`.

Структура: титул, ключевые выводы, задача и методология, разделы (текст,
затем у каждого вопроса — график по итогу и таблица «% по группам разреза»
с базой и знаками значимости), общий вывод.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

import xlsxwriter

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

BRAND = "1D7A4F"
EMU_PER_INCH = 914400
CHART_WIDTH = int(6.2 * EMU_PER_INCH)
MAX_TABLE_COLUMNS = 9
ARROWS = {"higher": " ▲", "lower": " ▼"}


def _text(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def _run(text: str, *, bold: bool = False, size: int | None = None, color: str | None = None,
         italic: bool = False) -> str:
    props = []
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    lines = '</w:t><w:br/><w:t xml:space="preserve">'.join(
        _text(line) for line in text.split("\n")
    )
    return f'<w:r>{rpr}<w:t xml:space="preserve">{lines}</w:t></w:r>'


def _paragraph(text: str = "", *, style: str | None = None, **run: Any) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_run(text, **run) if text else ''}</w:p>"


def _paragraphs(text: str, style: str | None = None) -> str:
    return "".join(_paragraph(line, style=style) for line in text.splitlines() if line.strip())


def _cell(text: str, *, bold: bool = False, shade: str | None = None, width: int,
          align: str = "left", color: str | None = None) -> str:
    shading = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shading}</w:tcPr>'
        f'<w:p><w:pPr><w:spacing w:before="20" w:after="20"/><w:jc w:val="{align}"/></w:pPr>'
        f"{_run(text, bold=bold, size=16, color=color)}</w:p></w:tc>"
    )


def _table(card: dict[str, Any]) -> str:
    """Таблица «% по группам»: строка — вариант, колонка — группа с базой."""
    table = card["table"]
    columns = table["columns"][:MAX_TABLE_COLUMNS]
    width_total = 9600
    first = 2800
    other = (width_total - first) // max(1, len(columns))
    grid = f'<w:gridCol w:w="{first}"/>' + "".join(
        f'<w:gridCol w:w="{other}"/>' for _ in columns
    )
    head = _cell("", width=first, shade="F2F4F3") + "".join(
        _cell(f"{column['label']}\nn={column['base']}", bold=True, width=other,
              shade="F2F4F3", align="center")
        for column in columns
    )
    rows = [f"<w:tr><w:trPr><w:tblHeader/></w:trPr>{head}</w:tr>"]
    for row in table["rows"]:
        cells = [_cell(row["label"], width=first)]
        for cell in row["cells"][: len(columns)]:
            value = cell["value"]
            text = "—" if value is None else f"{value:g}%{ARROWS.get(cell['direction'] or '', '')}"
            color = {"higher": BRAND, "lower": "C93A3A"}.get(cell["direction"] or "")
            if cell.get("small"):
                color = "9A9A9A"
            cells.append(_cell(text, width=other, align="center", color=color))
        rows.append(f"<w:tr>{''.join(cells)}</w:tr>")
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="D9DFDC"/>'
        for side in ("top", "bottom", "insideH")
    )
    note = ""
    if len(table["columns"]) > MAX_TABLE_COLUMNS:
        note = _paragraph(
            "Показаны не все группы разреза — полная таблица в книге Excel.",
            size=14, italic=True, color="6B6B6B",
        )
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="{width_total}" w:type="dxa"/>'
        f"<w:tblBorders>{borders}</w:tblBorders>"
        '<w:tblLayout w:type="fixed"/></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{''.join(rows)}</w:tbl>{note}"
    )


def _chart_values(card: dict[str, Any]) -> tuple[list[str], list[float]]:
    labels, values = [], []
    for row in card["table"]["rows"]:
        value = row["cells"][0]["value"] if row["cells"] else None
        if value is None:
            continue
        labels.append(row["label"])
        values.append(float(value))
    return labels, values


def _chart_xml(card: dict[str, Any]) -> str:
    labels, values = _chart_values(card)
    count = len(labels)
    horizontal = card.get("chart", "bar") != "column"
    categories = "".join(
        f'<c:pt idx="{index}"><c:v>{_text(label)}</c:v></c:pt>' for index, label in enumerate(labels)
    )
    numbers = "".join(
        f'<c:pt idx="{index}"><c:v>{value:g}</c:v></c:pt>' for index, value in enumerate(values)
    )
    # Горизонтальные столбцы читаются сверху вниз в порядке анкеты.
    orientation = "maxMin" if horizontal else "minMax"
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="{_C}" xmlns:a="{_A}" xmlns:r="{_R}">
<c:roundedCorners val="0"/>
<c:chart>
<c:autoTitleDeleted val="1"/>
<c:plotArea><c:layout/>
<c:barChart>
<c:barDir val="{'bar' if horizontal else 'col'}"/><c:grouping val="clustered"/><c:varyColors val="0"/>
<c:ser><c:idx val="0"/><c:order val="0"/>
<c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>Итого, %</c:v></c:pt></c:strCache></c:strRef></c:tx>
<c:spPr><a:solidFill><a:srgbClr val="{BRAND}"/></a:solidFill></c:spPr>
<c:invertIfNegative val="0"/>
<c:dLbls><c:numFmt formatCode="0&quot;%&quot;" sourceLinked="0"/><c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:endParaRPr lang="ru-RU"/></a:p></c:txPr><c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="0"/><c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/></c:dLbls>
<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${count + 1}</c:f><c:strCache><c:ptCount val="{count}"/>{categories}</c:strCache></c:strRef></c:cat>
<c:val><c:numRef><c:f>Sheet1!$B$2:$B${count + 1}</c:f><c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="{count}"/>{numbers}</c:numCache></c:numRef></c:val>
</c:ser>
<c:gapWidth val="60"/>
<c:axId val="1001"/><c:axId val="1002"/>
</c:barChart>
<c:catAx><c:axId val="1001"/><c:scaling><c:orientation val="{orientation}"/></c:scaling><c:delete val="0"/><c:axPos val="{'l' if horizontal else 'b'}"/><c:numFmt formatCode="General" sourceLinked="0"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:spPr><a:ln><a:solidFill><a:srgbClr val="BFBFBF"/></a:solidFill></a:ln></c:spPr><c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:endParaRPr lang="ru-RU"/></a:p></c:txPr><c:crossAx val="1002"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>
<c:valAx><c:axId val="1002"/><c:scaling><c:orientation val="minMax"/><c:min val="0"/></c:scaling><c:delete val="1"/><c:axPos val="{'b' if horizontal else 'l'}"/><c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="1001"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>
<c:spPr><a:noFill/></c:spPr>
</c:plotArea>
<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>
</c:chart>
<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900"><a:latin typeface="Arial"/></a:defRPr></a:pPr><a:endParaRPr lang="ru-RU"/></a:p></c:txPr>
<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData>
</c:chartSpace>"""


def _chart_workbook(card: dict[str, Any]) -> bytes:
    """Встроенная книга диаграммы: «Изменить данные» в Word открывает её."""
    labels, values = _chart_values(card)
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    sheet = workbook.add_worksheet("Sheet1")
    sheet.write(0, 1, "Итого, %")
    for index, (label, value) in enumerate(zip(labels, values, strict=True), start=1):
        sheet.write(index, 0, label)
        sheet.write_number(index, 1, value)
    workbook.close()
    return buffer.getvalue()


def _chart_inline(number: int, card: dict[str, Any]) -> str:
    count = max(1, len(_chart_values(card)[0]))
    horizontal = card.get("chart", "bar") != "column"
    height = int((0.32 * count + 0.5) * EMU_PER_INCH) if horizontal else int(3 * EMU_PER_INCH)
    height = min(height, int(7 * EMU_PER_INCH))
    return (
        f'<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{CHART_WIDTH}" cy="{height}"/>'
        f'<wp:docPr id="{number}" name="Диаграмма {number}"/>'
        f'<a:graphic><a:graphicData uri="{_C}">'
        f'<c:chart r:id="rIdChart{number}"/>'
        f"</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def _styles() -> str:
    def heading(style_id: str, name: str, size: int, color: str, before: int) -> str:
        return (
            f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:spacing w:before="{before}" w:after="120"/>'
            f'<w:outlineLvl w:val="{0 if style_id == "Heading1" else 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:color w:val="{color}"/><w:sz w:val="{size}"/>'
            f'<w:szCs w:val="{size}"/></w:rPr></w:style>'
        )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial" w:eastAsia="Arial"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="48"/><w:szCs w:val="48"/><w:color w:val="1D1D1F"/></w:rPr></w:style>
{heading("Heading1", "heading 1", 32, BRAND, 360)}
{heading("Heading2", "heading 2", 24, "1D1D1F", 240)}
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="6B6B6B"/><w:sz w:val="16"/></w:rPr></w:style>
</w:styles>"""


def _numbering() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{_W}">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""


def build_docx(report: dict[str, Any], brief: dict[str, Any], title: str) -> bytes:
    """Собрать DOCX из отчёта. Скрытые человеком графики не выводятся."""
    body: list[str] = []
    charts: list[dict[str, Any]] = []
    body.append(_paragraph(title, style="Title"))
    subtitle = f"Аналитический отчёт · {date.today().strftime('%d.%m.%Y')}"
    if brief.get("object"):
        subtitle += f" · объект интереса: {brief['object']}"
    body.append(_paragraph(subtitle, color="6B6B6B"))

    body.append(_paragraph("Ключевые выводы", style="Heading1"))
    body.append(_paragraphs(report["summary"]["text"], style="ListBullet"))

    body.append(_paragraph("Задача и методология", style="Heading1"))
    if brief.get("tasks"):
        body.append(_paragraph("Задачи исследования", style="Heading2"))
        body.append(_paragraphs(brief["tasks"]))
    if brief.get("description"):
        body.append(_paragraph("Об исследовании", style="Heading2"))
        body.append(_paragraphs(brief["description"]))
    body.append(_paragraph("Методология", style="Heading2"))
    body.append(_paragraphs(report.get("method") or ""))

    for section in report["sections"]:
        body.append(_paragraph(section["title"], style="Heading1"))
        body.append(_paragraphs(section["text"]))
        for card in section["cards"]:
            if card.get("hidden"):
                continue
            body.append(_paragraph(card["label"], style="Heading2"))
            if _chart_values(card)[0]:
                charts.append(card)
                body.append(_chart_inline(len(charts), card))
            body.append(_table(card))
            body.append(_paragraph(
                "% от всех опрошенных в группе; ▲▼ — значимо выше и ниже; серым — малая база.",
                style="Caption",
            ))

    body.append(_paragraph("Общий вывод", style="Heading1"))
    body.append(_paragraphs(report["conclusion"]["text"]))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}" xmlns:wp="{_WP}" xmlns:a="{_A}" '
        f'xmlns:c="{_C}"><w:body>{"".join(body)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
        'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    chart_rels = "".join(
        f'<Relationship Id="rIdChart{index}" '
        f'Type="{_R}/chart" Target="charts/chart{index}.xml"/>'
        for index in range(1, len(charts) + 1)
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rIdStyles" Type="{_R}/styles" Target="styles.xml"/>'
        f'<Relationship Id="rIdNumbering" Type="{_R}/numbering" Target="numbering.xml"/>'
        f"{chart_rels}</Relationships>"
    )
    chart_overrides = "".join(
        f'<Override PartName="/word/charts/chart{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        for index in range(1, len(charts) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="xlsx" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>'
        '<Override PartName="/word/document.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        f"{chart_overrides}</Types>"
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_R}/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _styles())
        archive.writestr("word/numbering.xml", _numbering())
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        for index, card in enumerate(charts, start=1):
            archive.writestr(f"word/charts/chart{index}.xml", _chart_xml(card))
            archive.writestr(
                f"word/charts/_rels/chart{index}.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{_PKG}"><Relationship Id="rId1" '
                f'Type="{_R}/package" '
                f'Target="../embeddings/Microsoft_Excel_Worksheet{index}.xlsx"/>'
                "</Relationships>",
            )
            archive.writestr(
                f"word/embeddings/Microsoft_Excel_Worksheet{index}.xlsx", _chart_workbook(card)
            )
    return buffer.getvalue()
