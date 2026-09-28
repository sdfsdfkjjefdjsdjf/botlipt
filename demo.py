"""Generate a dashboard preview using invented sample messages."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import time

from PIL import Image, ImageDraw

from analytics import analyze
from render import render_dashboard


def sample_photo(index: int) -> bytes:
    palettes = [("#527B94", "#E3C793"), ("#8D5F77", "#EDBFA3"), ("#4D887D", "#BCE3BA")]
    a, b = palettes[index % len(palettes)]
    img = Image.new("RGB", (700, 450), a)
    d = ImageDraw.Draw(img)
    d.ellipse((120, 35, 560, 420), fill=b)
    d.rounded_rectangle((265, 85, 510, 350), radius=26, fill=a)
    stream = BytesIO()
    img.save(stream, format="JPEG", quality=85)
    return stream.getvalue()


def main() -> None:
    now = int(time.time())
    messages = []
    people = ["Аня", "Максим", "Ирина", "Даниил", "Саша"]
    texts = [
        "Давайте обсудим встречу в субботу и выберем удобное время.",
        "Я могу прийти в субботу после обеда, примерно к четырём часам.",
        "Место встречи у парка подходит? Там рядом есть кафе.",
        "В кафе лучше заранее забронировать столик на нашу компанию.",
        "Кто сможет заказать столик в кафе на субботу к четырём?",
        "Я нашла фотографии с прошлой прогулки, сейчас отправлю.",
        "Фотографии получились отличные, особенно у озера.",
        "Кстати, прогноз на субботу обещает хорошую погоду для прогулки.",
        "Если погода изменится, можно перенести встречу в кафе.",
        "Давайте окончательно решим время встречи вечером.",
    ]
    for i in range(46):
        messages.append({
            "date": now - (46 - i) * 1750,
            "message_id": i + 1,
            "sender_id": str(i % 5 + 1),
            "sender_name": people[i % 5],
            "text": texts[(i * 7) % len(texts)],
            "photo_file_id": f"sample-{i}" if i in {8, 20, 33, 42} else None,
        })
    stats = analyze(messages, now, "Asia/Qyzylorda")
    pictures = [sample_photo(i) for i, _ in enumerate(stats["photos"])]
    target = Path(__file__).with_name("demo_dashboard.png")
    target.write_bytes(render_dashboard(stats, pictures))
    print(target)


if __name__ == "__main__":
    main()
