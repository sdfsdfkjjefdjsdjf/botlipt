"""Render a Telegram-ready PNG dashboard with Pillow."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


WIDTH, HEIGHT = 1400, 1840
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
    for word in words:
        candidate = f"{current} {word}".strip()
        if text_width(draw, candidate, face) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words:
        consumed = sum(len(line.split()) for line in lines)
        if consumed < len(words):
            lines[-1] = fit_text(draw, lines[-1] + "…", face, max_width)
    return lines


def card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str = CARD) -> None:
    draw.rounded_rectangle(box, radius=25, fill=fill)


def photo_tile(canvas: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
               info: dict[str, Any], raw: bytes | None) -> None:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=18, fill=255)
    if raw:
        try:
            with Image.open(BytesIO(raw)) as original:
                tile = ImageOps.fit(original.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
            canvas.paste(tile, (x1, y1), mask)
        except (OSError, ValueError):
            draw.rounded_rectangle(box, radius=18, fill=CARD_ALT)
            draw.text((x1 + 24, y1 + 75), "Фото недоступно", font=font(24), fill=MUTED)
    else:
        draw.rounded_rectangle(box, radius=18, fill=CARD_ALT)
        draw.text((x1 + 24, y1 + 75), "Фото недоступно", font=font(24), fill=MUTED)

    overlay = Image.new("RGBA", (w, 64), (8, 17, 31, 215))
    canvas.paste(overlay, (x1, y2 - 64), overlay)
    label = info.get("sender_name") or "Участник"
    caption = (info.get("text") or "").strip()
    if caption:
        label += " · " + caption
    draw.text((x1 + 16, y2 - 51), fit_text(draw, label, font(20, True), w - 32),
              font=font(20, True), fill=WHITE)


def render_dashboard(stats: dict[str, Any], photo_bytes: list[bytes | None] | None = None) -> bytes:
    """Return PNG bytes. photo_bytes matches stats['photos'] in order."""
    photo_bytes = photo_bytes or []
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)

    draw.rounded_rectangle((48, 47, 115, 114), radius=17, fill=CYAN)
    draw.text((68, 62), "24", font=font(31, True), fill=BG)
    draw.text((136, 48), "Дайджест чата", font=font(48, True), fill=WHITE)
    draw.text((137, 111), f"Последние 24 часа · {stats['start']} — {stats['end']} · {stats['timezone']}",
              font=font(23), fill=MUTED)

    metrics = [
        ("Сообщений", stats["messages"], CYAN),
        ("Участников", stats["participants"], GREEN),
        ("Фотографий", stats["photo_count"], ORANGE),
        ("Текстовых", stats["text_count"], WHITE),
    ]
    metric_w = 310
    for index, (label, value, accent) in enumerate(metrics):
        x = 48 + index * 330
        card(draw, (x, 179, x + metric_w, 307))
        draw.text((x + 24, 198), label, font=font(21), fill=MUTED)
        draw.text((x + 24, 230), str(value), font=font(53, True), fill=accent)

    card(draw, (48, 331, 882, 841))
    draw.text((77, 355), "Что обсуждали", font=font(30, True), fill=WHITE)
    draw.text((78, 395), "Характерные сообщения и ключевые слова", font=font(18), fill=MUTED)
    if not stats["highlights"]:
        draw.text((78, 482), "Пока мало текстовых сообщений для обзора", font=font(25), fill=MUTED)
    else:
        for i, item in enumerate(stats["highlights"]):
            y = 435 + i * 129
            card(draw, (77, y, 852, y + 112), CARD_ALT)
            draw.ellipse((95, y + 21, 108, y + 34), fill=[CYAN, ORANGE, GREEN][i])
            label = fit_text(draw, item["label"] or "Обсуждение", font(23, True), 560)
            draw.text((121, y + 12), label, font=font(23, True), fill=WHITE)
            attribution = f"{item['sender']} · {item['time']}"
            attribution = fit_text(draw, attribution, font(17), 155)
            draw.text((835 - text_width(draw, attribution, font(17)), y + 16),
                      attribution, font=font(17), fill=MUTED)
            for line_no, line in enumerate(wrap_lines(draw, item["excerpt"], font(20), 730, 2)):
                draw.text((97, y + 48 + line_no * 27), line, font=font(20), fill="#DAE5F2")

    card(draw, (902, 331, 1352, 841))
    draw.text((929, 355), "Самые активные", font=font(29, True), fill=WHITE)
    authors = stats["authors"][:5]
    top_count = max((item["count"] for item in authors), default=1)
    if not authors:
        draw.text((929, 440), "Пока нет сообщений", font=font(22), fill=MUTED)
    for i, item in enumerate(authors):
        y = 414 + i * 47
        name = fit_text(draw, item["name"], font(20), 310)
        draw.text((930, y), name, font=font(20), fill=WHITE)
        number = str(item["count"])
        draw.text((1324 - text_width(draw, number, font(20, True)), y), number,
                  font=font(20, True), fill=CYAN)
        draw.rounded_rectangle((930, y + 31, 1324, y + 39), radius=4, fill="#30425D")
        draw.rounded_rectangle((930, y + 31, 930 + int(394 * item["count"] / top_count), y + 39),
                               radius=4, fill=CYAN)
    draw.text((929, 682), "Сообщения по часам", font=font(20, True), fill=MUTED)
    hours = stats["hourly"]
    max_hour = max(max(hours), 1)
    for i, count in enumerate(hours):
        x = 930 + i * 16
        height = max(2, int(count / max_hour * 78))
        draw.rounded_rectangle((x, 788 - height, x + 10, 788), radius=4, fill=CYAN if count else "#344862")
    draw.text((930, 798), "−24 ч", font=font(16), fill=MUTED)
    draw.text((1277, 798), "сейчас", font=font(16), fill=MUTED)

    card(draw, (48, 861, 1352, 1118))
    draw.text((77, 887), "Частые слова", font=font(29, True), fill=WHITE)
    draw.text((78, 927), "Без служебных слов и ссылок", font=font(18), fill=MUTED)
    if not stats["words"]:
        draw.text((78, 1007), "Пока нет слов для подсчёта", font=font(24), fill=MUTED)
    else:
        x, y = 77, 973
        for i, (word, count) in enumerate(stats["words"][:14]):
            label = f"{word}  {count}"
            chip_width = text_width(draw, label, font(22, True)) + 36
            if x + chip_width > 1324:
                x, y = 77, y + 56
            draw.rounded_rectangle((x, y, x + chip_width, y + 43), radius=15,
                                   fill=CARD_ALT if i % 2 else "#294561")
            draw.text((x + 18, y + 7), label, font=font(22, True), fill=WHITE)
            x += chip_width + 10

    card(draw, (48, 1138, 1352, 1797))
    draw.text((77, 1164), "Фотографии за сутки", font=font(30, True), fill=WHITE)
    draw.text((1278, 1170), str(stats["photo_count"]), font=font(24, True), fill=ORANGE)
    photos = stats["photos"]
    if not photos:
        draw.text((78, 1302), "Фотографии пока не присылали", font=font(27), fill=MUTED)
    for i, info in enumerate(photos[:6]):
        column, row = i % 3, i // 3
        x1, y1 = 77 + column * 422, 1221 + row * 278
        photo_tile(canvas, draw, (x1, y1, x1 + 400, y1 + 258), info,
                   photo_bytes[i] if i < len(photo_bytes) else None)

    draw.text((48, 1810), "Темы составлены из фрагментов сообщений · бот учитывает только полученные сообщения",
              font=font(17), fill=MUTED)
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()
