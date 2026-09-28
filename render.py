"""Compact Telegram dashboard rendered as a PNG with Pillow."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


WIDTH = 1400
BG = "#0D1729"
CARD = "#182842"
CARD_ALT = "#203451"
WHITE = "#F3F7FC"
MUTED = "#A6B5C9"
CYAN = "#64D9E6"
ORANGE = "#FFC186"
GREEN = "#8DE6B1"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def text_width(draw: ImageDraw.ImageDraw, value: str, face: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), value, font=face)
    return box[2] - box[0]


def fit_text(draw: ImageDraw.ImageDraw, value: str, face: ImageFont.ImageFont,
             max_width: int) -> str:
    value = " ".join(value.split())
    if text_width(draw, value, face) <= max_width:
        return value
    while value and text_width(draw, value + "…", face) > max_width:
        value = value[:-1]
    return value.rstrip() + "…"


def wrap_lines(draw: ImageDraw.ImageDraw, value: str, face: ImageFont.ImageFont,
               max_width: int, max_lines: int) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if text_width(draw, candidate, face) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = fit_text(draw, word, face, max_width)
        if len(lines) == max_lines:
            lines[-1] = fit_text(draw, lines[-1] + "…", face, max_width)
            return lines
        if index == len(words) - 1:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str = CARD) -> None:
    draw.rounded_rectangle(box, radius=23, fill=fill)


def photo_tile(canvas: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
               info: dict[str, Any], raw: bytes | None) -> None:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=16, fill=255)
    if raw:
        try:
            with Image.open(BytesIO(raw)) as original:
                tile = ImageOps.fit(original.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
            canvas.paste(tile, (x1, y1), mask)
        except (OSError, ValueError):
            raw = None
    if not raw:
        draw.rounded_rectangle(box, radius=16, fill=CARD_ALT)
        draw.text((x1 + 24, y1 + 75), "Фото недоступно", font=font(24), fill=MUTED)
    overlay = Image.new("RGBA", (w, 58), (8, 17, 31, 220))
    canvas.paste(overlay, (x1, y2 - 58), overlay)
    label = info.get("sender_name") or "Участник"
    caption = (info.get("text") or "").strip()
    if caption:
        label += " · " + caption
    draw.text((x1 + 15, y2 - 46), fit_text(draw, label, font(19, True), w - 30),
              font=font(19, True), fill=WHITE)


def render_dashboard(stats: dict[str, Any], photo_bytes: list[bytes | None] | None = None) -> bytes:
    """Return an adaptive-height PNG. photo_bytes matches stats['photos']."""
    photo_bytes = photo_bytes or []
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    topics = stats.get("topics", [])
    topic_layout = []
    for item in topics:
        lines = wrap_lines(measure, item["summary"], font(23), 1200, 6)
        topic_layout.append((item, lines, max(112, 70 + len(lines) * 30)))
    topic_height = 80 + sum(row[2] for row in topic_layout) + max(0, len(topic_layout) - 1) * 12 + 23
    if not topic_layout:
        topic_height = 185
    topic_y = 294
    lower_y = topic_y + topic_height + 18
    lower_height = 340
    photo_y = lower_y + lower_height + 18
    photos = stats.get("photos", [])[:6]
    photo_cols = 2 if len(photos) in {2, 4} else 3
    photo_rows = (len(photos) + photo_cols - 1) // photo_cols
    photo_height = 76 + photo_rows * 218 + max(0, photo_rows - 1) * 12 + 23 if photos else 0
    footer_y = photo_y + photo_height + (22 if photos else 0)
    height = footer_y + 55

    canvas = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((44, 38, 105, 99), radius=16, fill=CYAN)
    draw.text((61, 48), "24", font=font(30, True), fill=BG)
    draw.text((126, 37), "Дайджест чата", font=font(46, True), fill=WHITE)
    draw.text((127, 99), f"{stats['start']} — {stats['end']} · {stats['timezone']}",
              font=font(22), fill=MUTED)

    metrics = [
        ("Сообщений", str(stats["messages"]), CYAN),
        ("Участников", str(stats["participants"]), GREEN),
        ("Фотографий", str(stats["photo_count"]), ORANGE),
        ("Текстовых", str(stats["text_count"]), WHITE),
        ("Средний IQ*", str(stats["average_score"]) if stats.get("average_score") is not None else "—", ORANGE),
    ]
    for index, (label, value, accent) in enumerate(metrics):
        x = 44 + index * 265
        card(draw, (x, 158, x + 252, 276))
        draw.text((x + 18, 175), label, font=font(19), fill=MUTED)
        draw.text((x + 18, 204), value, font=font(47, True), fill=accent)

    card(draw, (44, topic_y, 1356, topic_y + topic_height))
    draw.text((70, topic_y + 23), "Что обсуждали", font=font(30, True), fill=WHITE)
    draw.text((1327 - text_width(draw, "Похожие сообщения объединены", font(17)), topic_y + 32),
              "Похожие сообщения объединены", font=font(17), fill=MUTED)
    if not topic_layout:
        draw.text((72, topic_y + 102), "Пока нет текстовых сообщений для обзора", font=font(24), fill=MUTED)
    else:
        y = topic_y + 73
        for index, (item, lines, row_height) in enumerate(topic_layout):
            card(draw, (70, y, 1330, y + row_height), CARD_ALT)
            draw.ellipse((88, y + 22, 100, y + 34), fill=[CYAN, ORANGE, GREEN, "#C4B7F3"][index % 4])
            title = fit_text(draw, item["title"], font(23, True), 980)
            draw.text((113, y + 12), title, font=font(23, True), fill=WHITE)
            count_label = f"{item['count']} сообщ."
            draw.text((1304 - text_width(draw, count_label, font(18)), y + 16),
                      count_label, font=font(18), fill=MUTED)
            for line_index, line in enumerate(lines):
                draw.text((91, y + 49 + line_index * 29), line, font=font(23), fill="#D9E4F0")
            y += row_height + 12

    boxes = [(44, 468), (485, 923), (940, 1356)]
    for x1, x2 in boxes:
        card(draw, (x1, lower_y, x2, lower_y + lower_height))

    # Activity card.
    draw.text((68, lower_y + 22), "Активность", font=font(27, True), fill=WHITE)
    authors = stats["authors"][:4]
    if not authors:
        draw.text((68, lower_y + 89), "Сообщений пока нет", font=font(21), fill=MUTED)
    top_count = max((item["count"] for item in authors), default=1)
    for i, item in enumerate(authors):
        y = lower_y + 72 + i * 42
        name = fit_text(draw, item["name"], font(19), 280)
        draw.text((68, y), name, font=font(19), fill=WHITE)
        count = str(item["count"])
        draw.text((443 - text_width(draw, count, font(19, True)), y), count,
                  font=font(19, True), fill=CYAN)
        draw.rounded_rectangle((68, y + 28, 444, y + 34), radius=3, fill="#30425D")
        draw.rounded_rectangle((68, y + 28, 68 + int(376 * item["count"] / top_count), y + 34),
                               radius=3, fill=CYAN)
    draw.text((68, lower_y + 253), "По часам", font=font(18, True), fill=MUTED)
    hours = stats["hourly"]
    max_hour = max(max(hours), 1)
    for i, count in enumerate(hours):
        x = 68 + i * 16
        bar_height = max(2, int(count / max_hour * 49))
        draw.rounded_rectangle((x, lower_y + 319 - bar_height, x + 10, lower_y + 319),
                               radius=3, fill=CYAN if count else "#344862")

    # Individual playful IQ scores. No psychometric claim is made.
    draw.text((510, lower_y + 22), "IQ участников*", font=font(27, True), fill=WHITE)
    scores = stats.get("participant_scores", [])[:5]
    if not scores:
        draw.text((511, lower_y + 98), "Мало текста для оценки", font=font(21), fill=MUTED)
        draw.text((511, lower_y + 131), "Нужно ≥2 сообщений и ≥8 слов", font=font(17), fill=MUTED)
    for i, item in enumerate(scores):
        y = lower_y + 77 + i * 42
        draw.text((510, y), fit_text(draw, item["name"], font(21), 282), font=font(21), fill=WHITE)
        score = str(item["score"])
        draw.text((900 - text_width(draw, score, font(23, True)), y - 1), score,
                  font=font(23, True), fill=GREEN if i == 0 else ORANGE)
        if i < 4:
            draw.line((510, y + 35, 899, y + 35), fill="#30425D", width=1)
    explanation = "* Слова, аргументы, диалог; не реальный IQ"
    draw.text((510, lower_y + 306), fit_text(draw, explanation, font(16), 391),
              font=font(16), fill=MUTED)

    # Word cloud as compact chips.
    draw.text((963, lower_y + 22), "Частые слова", font=font(27, True), fill=WHITE)
    if not stats["words"]:
        draw.text((964, lower_y + 98), "Пока нет слов", font=font(21), fill=MUTED)
    else:
        x, y = 964, lower_y + 72
        for index, (word, count) in enumerate(stats["words"][:12]):
            label = f"{word}  {count}"
            chip_width = min(362, text_width(draw, label, font(19, True)) + 27)
            if x + chip_width > 1332:
                x, y = 964, y + 48
            if y + 40 > lower_y + 326:
                break
            draw.rounded_rectangle((x, y, x + chip_width, y + 38), radius=13,
                                   fill=CARD_ALT if index % 2 else "#294561")
            draw.text((x + 13, y + 7), fit_text(draw, label, font(19, True), chip_width - 25),
                      font=font(19, True), fill=WHITE)
            x += chip_width + 8

    if photos:
        card(draw, (44, photo_y, 1356, photo_y + photo_height))
        draw.text((70, photo_y + 20), "Фотографии за сутки", font=font(29, True), fill=WHITE)
        for i, info in enumerate(photos):
            column, row = i % photo_cols, i // photo_cols
            row_count = min(photo_cols, len(photos) - row * photo_cols)
            tile_width = 620 if photo_cols == 2 else 400
            gap = 20 if photo_cols == 2 else 30
            row_width = row_count * tile_width + (row_count - 1) * gap
            row_start = 70 + (1260 - row_width) // 2
            x1 = row_start + column * (tile_width + gap)
            y1 = photo_y + 69 + row * 230
            photo_tile(canvas, draw, (x1, y1, x1 + tile_width, y1 + 218), info,
                       photo_bytes[i] if i < len(photo_bytes) else None)

    draw.text((45, footer_y + 12), "Темы сгруппированы локально · IQ* — игровой показатель текстов · учтены только полученные сообщения",
              font=font(17), fill=MUTED)
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()
