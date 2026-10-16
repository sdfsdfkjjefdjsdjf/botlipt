"""Generate a dashboard preview using invented sample messages."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import time

from PIL import Image, ImageDraw

from analytics import analyze
from render import render_dashboard


def sample_photo(index: int) -> bytes:
    palettes = [("#58636C", "#C2B7A7"), ("#69656A", "#B9A38D"), ("#4E5A5B", "#B6C0B7")]
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
    people = ["Аня", "Максим", "Ирина"]
    conversations = [
        [
            "Обновление драйвера NVIDIA исправляет сон монитора на Fedora?",
            "Новый драйвер NVIDIA проверила: монитор проснулся без чёрного экрана.",
            "Потому что старая версия драйвера NVIDIA не сохраняла память видеокарты при переходе в сон.",
            "Например, после обновления драйвера монитор нормально проснулся уже пять раз подряд.",
            "Перед установкой драйвера NVIDIA сохраним предыдущую версию на случай отката.",
            "Если сон монитора снова сломается, сравним журналы старой и новой версии драйвера.",
            "Проверила температуру видеокарты: после обновления драйвера она осталась прежней.",
            "Итог проверки: новый драйвер NVIDIA работает, сон монитора восстанавливается.",
        ],
        [
            "Бронирование столика в кафе на субботу: кто сможет прийти?",
            "Столик в кафе возле парка свободен в субботу после четырёх.",
            "Забронируем большой столик в кафе, потому что к нам присоединятся ещё двое друзей.",
            "Например, у окна в кафе есть столик на шесть человек и удобные кресла.",
            "В отличие от прошлой встречи, кафе рядом с парком удобно для тех, кто едет на автобусе.",
            "Бронирование столика подтвердили по телефону; предоплата в кафе не требуется.",
            "Если погода испортится, встреча всё равно состоится внутри кафе за большим столиком.",
            "Фотография столика в кафе: у окна действительно достаточно места для шести человек.",
        ],
        [
            "Рабочие задачи с ChatGPT: попробуем автоматический дайджест чата?",
            "Для дайджеста ChatGPT нужны целые темы обсуждения, а не список случайных фраз.",
            "Потому что отдельные фразы теряют контекст, дайджест должен сохранять ссылки на исходные сообщения.",
            "Например, ChatGPT может объединить выбор места и бронирование кафе в одну тему дайджеста.",
            "В рабочие задачи включим проверку дайджеста по исходным сообщениям, чтобы заметить неточности.",
            "Если ChatGPT пропустит важное обсуждение, добавим его в тестовый набор дайджеста.",
            "Сравнили два дайджеста ChatGPT: вариант с темами и коротким содержанием читается быстрее.",
            "Рабочие задачи согласовали: дайджест содержит темы, ссылки и понятный разбор активности.",
        ],
    ]
    texts = [text for conversation in conversations for text in conversation]
    for i, body in enumerate(texts):
        messages.append({
            "date": now - (len(texts) - i) * 400,
            "message_id": i + 1,
            "sender_id": str(i % 3 + 1),
            "sender_name": people[i % 3],
            "text": body,
            "reply_to_message_id": i if i % 8 else None,
            "photo_file_id": f"sample-{i}" if i in {1, 15} else None,
            "reaction_count": {1: 5, 15: 9}.get(i, 0),
            "reply_count": {1: 2, 15: 3}.get(i, 0),
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
