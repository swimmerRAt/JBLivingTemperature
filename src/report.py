"""Build the downloadable PDF report for one selected town."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from src.dashboard_model import DRIVER_LABELS
from src.limitations import medical_changes, weight_sensitivity
from src.ui import (accessibility_rows, build_forecast_rows, build_indicator_rows, build_status_cards,
                    format_metric)
from src.visualization.map import GRADE_COLORS, MISSING_COLOR

ROOT = Path(__file__).resolve().parents[1]
LOGO_PNG = ROOT / "assets" / "logo.png"
# The bundled font comes first so every machine renders the same report.
FONT_CANDIDATES = [
    (ROOT / "assets/fonts/NanumGothic.ttf", ROOT / "assets/fonts/NanumGothicBold.ttf"),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")),
    (Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/malgunbd.ttf")),
    (Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"), Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")),
]
FONT, FONT_BOLD = "ReportKR", "ReportKR-Bold"

ORANGE, ORANGE_SOFT = HexColor("#ff6f0f"), HexColor("#fff2e8")
INK, BODY, MUTED = HexColor("#212124"), HexColor("#4d5159"), HexColor("#868b94")
LINE, CANVAS = HexColor("#eaebee"), HexColor("#f7f8fa")
STAGE_COLORS = {"안정": HexColor("#00a05b"), "주의": HexColor("#d98a00"), "위험": HexColor("#f04438")}
STATUS_LABELS = {"computed": "계산됨", "unconfirmed_center": "센터 위치 미확인", "unmapped_origin": "보행망 미연결"}
PAGE_WIDTH = A4[0] - 36 * mm


def report_font_available() -> bool:
    return any(regular.exists() and bold.exists() for regular, bold in FONT_CANDIDATES)


def _register_fonts() -> None:
    if FONT in pdfmetrics.getRegisteredFontNames():
        return
    for regular, bold in FONT_CANDIDATES:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont(FONT, str(regular)))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
            return
    raise FileNotFoundError("PDF 보고서에 쓸 한글 폰트가 없습니다. assets/fonts/NanumGothic.ttf를 확인하세요.")


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=FONT, fontSize=9, leading=13.5, textColor=BODY, wordWrap="CJK")
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.5, leading=11, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=22, leading=28, textColor=INK),
        "brand": ParagraphStyle("brand", parent=base, fontName=FONT_BOLD, fontSize=11, textColor=INK),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=13, leading=18, textColor=INK,
                             spaceBefore=14, spaceAfter=6),
        "label": ParagraphStyle("label", parent=base, fontSize=8, leading=11, textColor=MUTED),
        "value": ParagraphStyle("value", parent=base, fontName=FONT_BOLD, fontSize=15, leading=20, textColor=INK),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=12),
        "cell_bold": ParagraphStyle("cell_bold", parent=base, fontName=FONT_BOLD, fontSize=8.5, leading=12, textColor=INK),
    }


def _text(value: Any) -> str:
    return escape("-" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value))


def _table(frame: pd.DataFrame, styles: dict, widths: list[float] | None = None) -> Table:
    data = [[Paragraph(_text(column), styles["cell_bold"]) for column in frame.columns]]
    data += [[Paragraph(_text(value), styles["cell"]) for value in record] for record in frame.itertuples(index=False)]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ORANGE_SOFT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _section(title: str, styles: dict, *flowables) -> KeepTogether:
    return KeepTogether([Paragraph(escape(title), styles["h2"]), *flowables])


def _temperature_bar(score: Any, grade: Any) -> Drawing:
    drawing = Drawing(PAGE_WIDTH, 22)
    drawing.add(Rect(0, 10, PAGE_WIDTH, 8, rx=4, ry=4, fillColor=HexColor("#eceef1"), strokeColor=None))
    if score is not None and not pd.isna(score):
        width = PAGE_WIDTH * max(0.0, min(100.0, float(score))) / 100
        drawing.add(Rect(0, 10, max(width, 8), 8, rx=4, ry=4,
                         fillColor=HexColor(GRADE_COLORS.get(grade, MISSING_COLOR)), strokeColor=None))
    for x, label, anchor in [(0, "0° 쌀쌀해요", "start"), (PAGE_WIDTH / 2, "50°", "middle"), (PAGE_WIDTH, "100° 따뜻해요", "end")]:
        drawing.add(String(x, 0, label, fontName=FONT, fontSize=7, fillColor=MUTED, textAnchor=anchor))
    return drawing


def _driver_chart(drivers: list[dict]) -> Drawing:
    rows = sorted(drivers, key=lambda item: item["score"], reverse=True)
    label_width, bar_width, row_height = 110, PAGE_WIDTH - 150, 18
    drawing = Drawing(PAGE_WIDTH, row_height * len(rows))
    for index, item in enumerate(rows):
        y = drawing.height - (index + 1) * row_height + 4
        drawing.add(String(0, y + 2, item["label"], fontName=FONT, fontSize=8, fillColor=BODY))
        drawing.add(Rect(label_width, y, bar_width, 9, rx=4, ry=4, fillColor=CANVAS, strokeColor=None))
        drawing.add(Rect(label_width, y, max(bar_width * item["score"] / 100, 4), 9, rx=4, ry=4,
                         fillColor=ORANGE if item["score"] >= 50 else HexColor("#ffb685"), strokeColor=None))
        drawing.add(String(label_width + bar_width + 8, y + 1, f'{item["score"]:.0f}', fontName=FONT_BOLD,
                           fontSize=8, fillColor=INK))
    return drawing


def _kpi_grid(cards: list[dict], styles: dict) -> Table:
    cells = []
    for card in cards[1:]:
        value_style = styles["value"]
        if card["key"] == "risk" and card["value"] in STAGE_COLORS:
            value_style = ParagraphStyle("risk", parent=styles["value"], textColor=STAGE_COLORS[card["value"]])
        cells.append([Paragraph(escape(card["label"]), styles["label"]), Paragraph(escape(card["value"]), value_style),
                      Paragraph(escape(card["detail"]), styles["small"])])
    cells.append("")
    width = PAGE_WIDTH / 3
    table = Table([cells[0:3], cells[3:6]], colWidths=[width] * 3)
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
    ]))
    return table


def _accessibility_frame(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for record in rows.sort_values("departure_time").to_dict("records") if "departure_time" in rows else rows.to_dict("records"):
        computed = record.get("status") == "computed"
        count = lambda key: format_metric(record.get(key), "곳", 0) if computed else "-"
        records.append([f'평일 {str(record.get("departure_time", ""))[:5]}',
                        STATUS_LABELS.get(record.get("status"), "계산됨"),
                        count("accessible_facilities_30m"), count("accessible_medical_30m"), count("accessible_pharmacy_30m")])
    return pd.DataFrame(records, columns=["출발 시각", "계산 상태", "병의원·약국 합계", "병의원", "약국"])


def _medical_frame(changes: pd.DataFrame) -> pd.DataFrame:
    kinds = {"hospital": "병의원", "pharmacy": "약국"}
    return pd.DataFrame(
        [[kinds.get(item.kind, item.kind), item.baseline_period, format_metric(item.baseline_count, "곳", 0),
          item.current_period, format_metric(item.current_count, "곳", 0), format_metric(item.change, "곳", 0)]
         for item in changes.itertuples(index=False)],
        columns=["구분", "비교 시점", "비교 기관 수", "최근 시점", "최근 기관 수", "증감"],
    )


def _footer(region: str, created: str):
    def draw(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"전북 생활 온도 · {region} 생활 리포트 · {created} 생성")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{document.page}쪽")
        canvas.restoreState()
    return draw


def build_report_pdf(snapshot: dict[str, object]) -> bytes:
    _register_fonts()
    styles = _styles()
    row = snapshot.get("selected_region", {}) or {}
    development = snapshot.get("development", {}) or {}
    region = row.get("region_name") or "선택 지역"
    created = date.today().strftime("%Y년 %m월 %d일")
    cards = build_status_cards(snapshot)
    story: list = []

    # Cover header: logo, brand, town title.
    brand = [Paragraph("전북 생활 온도", styles["brand"]),
             Paragraph(f"동네 생활 리포트 · {created}", styles["small"])]
    logo = Image(str(LOGO_PNG), width=30, height=30) if LOGO_PNG.exists() else ""
    header = Table([[logo, brand]], colWidths=[38, PAGE_WIDTH - 38])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 10), Paragraph(f"{escape(region)} 생활 리포트", styles["title"]),
              Paragraph("생활 인프라와 인구 흐름을 하나의 온도로 읽은 결과입니다. 점수는 전북 읍면동 242곳 사이의 상대 비교입니다.",
                        styles["base"]), Spacer(1, 12)]

    # Living temperature + six KPI cards.
    score_card = cards[0]
    temperature = score_card["value"] if score_card["value"] == "데이터 없음" else f'{score_card["value"]}°'
    temp_color = HexColor(GRADE_COLORS.get(row.get("living_grade"), MISSING_COLOR))
    story += [
        Paragraph("우리 동네 생활 온도", styles["label"]),
        Paragraph(f'<font color="{temp_color.hexval().replace("0x", "#")}">{escape(temperature)}</font>'
                  f'<font size="10" color="#4d5159">  {escape(score_card["detail"])}</font>',
                  ParagraphStyle("temp", parent=styles["value"], fontSize=26, leading=32)),
        _temperature_bar(row.get("living_score"), row.get("living_grade")),
        Spacer(1, 12), _kpi_grid(cards, styles),
    ]

    # Score breakdown and ranking sensitivity.
    weights = snapshot.get("weights") or {}
    weight_text = " · ".join(f"{label} {weights.get(key, 0):g}" for key, label in DRIVER_LABELS.items())
    score_parts = [_driver_chart(snapshot.get("selected_drivers", [])) if snapshot.get("selected_drivers") else
                   Paragraph("항목별 점수를 계산할 데이터가 없습니다.", styles["base"]),
                   Spacer(1, 4), Paragraph(f"적용 가중치 · {escape(weight_text)}", styles["small"])]
    sensitivity = weight_sensitivity(snapshot.get("regions", pd.DataFrame()), weights)
    if not sensitivity.empty:
        selected = sensitivity[sensitivity.region_code.astype(str).eq(str(row.get("region_code")))]
        if not selected.empty:
            item = selected.iloc[0]
            score_parts.append(Paragraph(
                f"현재 공동순위 {int(item.current_rank)}위 · 가중치를 항목별 ±20% 바꾸면 {item.best_rank}~{item.worst_rank}위,"
                f" 등급 {escape(str(item.grade_variants))}", styles["small"]))
    story.append(_section("1. 생활 온도 구성", styles, *score_parts))

    # 30-minute car-free access.
    access = accessibility_rows(development, row.get("region_code"))
    if access.empty:
        access_parts = [Paragraph("선택 지역의 30분 생활권 결과가 없습니다.", styles["base"])]
    else:
        origin = access.iloc[0].get("origin_name")
        access_parts = [Paragraph(f"출발 행정복지센터 · {_text(origin)}", styles["cell_bold"]), Spacer(1, 4),
                        _table(_accessibility_frame(access), styles),
                        Paragraph("KTDB 2025년 3월 평일 시간표·HIRA 2025년 3월 의료시설·OSM 보행망을 결합한 추정입니다. "
                                  "도보→버스→도보 경로이며 실시간 운행·주말·실제 진료 가능 여부는 반영하지 않습니다.", styles["small"])]
    story.append(_section("2. 차 없이 30분 안에 갈 수 있는 의료시설", styles, *access_parts))

    # 3-year decline forecast.
    forecast_parts = [_table(build_forecast_rows(row), styles, [PAGE_WIDTH * w for w in (.3, .25, .3, .15)])]
    if row.get("top3_risk_sectors"):
        forecast_parts.append(Paragraph(f"먼저 감소할 위험이 큰 업종 · {_text(row['top3_risk_sectors'])}", styles["base"]))
    for key, label in [("youth_forecast_drivers", "청년 예측 요인(SHAP)"), ("first_risk_drivers", "업종 위험 요인(SHAP)")]:
        if row.get(key):
            forecast_parts.append(Paragraph(f"{label} · {_text(row[key])}", styles["small"]))
    if row.get("youth_forecast_note"):
        forecast_parts.append(Paragraph(f"※ {_text(row['youth_forecast_note'])}", styles["small"]))
    story.append(_section("3. 3년 내 감소 위험 (예측)", styles, *forecast_parts))

    # Observed healthcare change.
    changes = medical_changes(development.get("medical_history", pd.DataFrame()), str(row.get("region_code")))
    medical_parts = ([_table(_medical_frame(changes), styles),
                      Paragraph("관측 기관 수의 차이이며 실제 개·폐업이나 향후 예측이 아닙니다.", styles["small"])]
                     if not changes.empty else [Paragraph("비교 가능한 의료시설 이력이 없습니다.", styles["base"])])
    story.append(_section("4. 의료시설 변화", styles, *medical_parts))

    # Population and infrastructure indicators.
    indicators = pd.concat([build_indicator_rows(row), pd.DataFrame([
        ["버스 정류장(승강장) 수", format_metric(row.get("bus_stop_count"), digits=0)],
        ["경유 버스 노선 수", format_metric(row.get("bus_route_count"), digits=0)],
        ["사업체 수", format_metric(row.get("business_count"), digits=0)],
        ["사업체 변화(2020년 이후)", format_metric(row.get("business_change_since_2020"), suffix="%")],
    ], columns=["지표", "값"])], ignore_index=True)
    story.append(_section("5. 인구와 생활 인프라 지표", styles, _table(indicators, styles, [PAGE_WIDTH * .45, PAGE_WIDTH * .55]),
                          Paragraph("빈집 비율과 청년 순이동은 시군 단위 자료라 같은 시군의 읍면동에 같은 값이 들어갑니다.", styles["small"])))

    # Recommendations under the same weights.
    recommendations = pd.DataFrame(
        [[str(rank), item.get("region_name"), f'{format_metric(item.get("living_score"))}°',
          f'{item.get("living_grade") or "-"}등급 · {item.get("forecast_stage") or "-"}', item.get("reason")]
         for rank, item in enumerate(snapshot.get("recommendations", []), start=1)],
        columns=["순위", "동네", "생활 온도", "등급·3년 위험", "추천 이유"])
    if not recommendations.empty:
        story.append(_section("6. 같은 조건으로 추천하는 동네", styles,
                              _table(recommendations, styles, [PAGE_WIDTH * w for w in (.07, .2, .12, .16, .45)])))

    # Sources and caveats.
    quality = snapshot.get("quality", {}) or {}
    notes = [*snapshot.get("notes", []), *quality.get("warnings", {}).values()]
    sources = " · ".join(quality.get("sources", {}).values())
    story.append(_section("자료 출처와 유의사항", styles,
                          *[Paragraph(f"· {_text(note)}", styles["small"]) for note in notes],
                          Paragraph(f"· 기준 자료: {_text(sources)}", styles["small"]),
                          Paragraph("· 생활 온도와 A~E 등급은 전북 읍면동 사이의 상대평가이며, 예측값은 모델 추정치입니다.", styles["small"])))

    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                                 bottomMargin=18 * mm, title=f"{region} 생활 리포트", author="전북 생활 온도")
    footer = _footer(region, created)
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
