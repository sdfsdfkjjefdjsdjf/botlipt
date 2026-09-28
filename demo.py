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

    short_texts = [
        ("Алексей", "Прикольный бот может собирать план обсуждений и находить повторяющиеся темы."),
        ("Алексей", "Да, бот сможет объединять похожие сообщения в одну тему."),
        ("Мария", "Давайте месяц использовать GPT для заметок и рабочих задач."),
        ("Мария", "Согласна, GPT поможет составлять ежедневную сводку по задачам."),
        ("Алексей", "Сделаем первый тест сегодня вечером и обсудим результаты?"),
    ]
    compact_messages = [
        {"date": now - (5 - i) * 600, "message_id": i + 1,
         "sender_id": str(1 if name == "Алексей" else 2), "sender_name": name,
         "text": body, "photo_file_id": None}
        for i, (name, body) in enumerate(short_texts)
    ]
    compact = Path(__file__).with_name("demo_compact.png")
    compact.write_bytes(render_dashboard(analyze(compact_messages, now, "Asia/Qyzylorda")))
    print(compact)


if __name__ == "__main__":
    main()
