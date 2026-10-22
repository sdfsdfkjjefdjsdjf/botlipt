"""Short Telegram captions and paged details; all user text is escaped after clipping."""

from __future__ import annotations

from html import escape, unescape
import re
from typing import Any

from analytics import message_count_label


CAPTION_LIMIT = 900  # Leave room below Telegram's 1024 UTF-16-unit caption limit.
BRIEF_TOPICS = 3
POINT_LABELS = {"context": "Суть", "question": "Вопрос", "outcome": "Итог"}


def visible_length(value: str) -> int:
    plain = unescape(re.sub(r"<[^>]*>", "", value))
    return len(plain.encode("utf-16-le")) // 2


def short(value: str, limit: int) -> str:
    value = " ".join(str(value).split())
    if len(value.encode("utf-16-le")) // 2 <= limit:
        return value
    head = value.encode("utf-16-le")[:(limit - 1) * 2].decode("utf-16-le", errors="ignore")
    boundary = head.rfind(" ")
    if boundary >= len(head) // 2:
        head = head[:boundary]
    return head.rstrip(" ,;:") + "…"


def caption(lines: list[str], footer: list[str] | None = None) -> str:
    """Keep complete HTML lines; an extreme input cannot split an entity or link."""
    footer = footer or []
    while lines and visible_length("\n".join(lines + footer)) > CAPTION_LIMIT:
        lines.pop()
    return "\n".join(lines + footer).strip()


def message_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    if message_id <= 0:
        return None
    if username and re.fullmatch(r"[A-Za-z0-9_]+", username):
        return f"https://t.me/{username}/{message_id}"
    if str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
    return None


def source_text(text: str, message_id: int, chat_id: int,
                username: str | None, limit: int) -> str:
    label = escape(short(text, limit))
    link = message_link(chat_id, username, message_id)
    return f'<a href="{link}">{label}</a>' if link else label


def topic_total(topics: list[dict[str, Any]]) -> int:
    return sum(topic.get("topic_count", 1) for topic in topics)


def format_text_report(stats: dict[str, Any], chat_id: int,
                       username: str | None, *, daily: bool = False) -> str:
    title = "📊 Ежедневная сводка · 24 часа" if daily else "📊 Сводка · 24 часа"
    lines = [f"<b>{title}</b>",
             f"{escape(stats['start'])}–{escape(stats['end'])}",
             f"{stats['messages']} сообщ. · {stats.get('participants', 0)} участн."
             f" · {stats.get('photo_count', len(stats.get('photos', [])))} фото", ""]
    topics = stats.get("topics", [])
    main = [topic for topic in topics if not topic.get("is_other")][:BRIEF_TOPICS]
    if not main:
        lines.append("Пока нет содержательных тем для обзора.")
    for index, topic in enumerate(main, 1):
        ids = topic.get("message_ids", [])
        heading = source_text(topic["title"], ids[0] if ids else 0, chat_id, username, 50)
        lines.append(f"<b>{index}. {heading}</b> · {topic['count']} сообщ.")
        point = topic.get("brief") or next(iter(topic.get("highlights", [])), None)
        if point:
            label = "Итог" if point.get("kind") == "outcome" else "Суть"
            lines.append(f"{label}: " + source_text(point["text"], point.get("message_id") or 0,
                                                   chat_id, username, 100))
    remaining = topic_total(topics) - len(main)
    if remaining:
        lines.append(f"Ещё тем: {remaining} · кнопка «Темы»")
    photos = []
    for index, photo in enumerate(stats.get("photos", [])[:2], 1):
        link = message_link(chat_id, username, photo["message_id"])
        if link:
            photos.append(f'<a href="{link}">фото {index}</a>'
                          f" (♥ {photo.get('reaction_count', 0)}, ответов {photo.get('reply_count', 0)})")
    if photos:
        lines.append("📷 " + " · ".join(photos))
    scores = stats.get("participant_scores", [])
    footer = []
    if stats.get("average_score") is not None:
        top = ", ".join(f"{escape(short(item['name'], 18))} {item['score']}" for item in scores[:3])
        footer = ["", f"<b>IQ* {stats['average_score']}</b>" + (f" · {top}" if top else ""),
                  "*Игровой балл текста, не оценка интеллекта."]
    return caption(lines, footer)


def format_topic_page(stats: dict[str, Any], chat_id: int,
                      username: str | None, page: int) -> str:
    topics = stats.get("topics", [])
    topic = topics[page]
    ids = topic.get("message_ids", [])
    heading = source_text(topic["title"], ids[0] if ids else 0, chat_id, username, 72)
    lines = [f"<b>💬 Темы · {page + 1}/{len(topics)}</b>", f"<b>{heading}</b>",
             f"{message_count_label(topic['count'])} · участников: {topic.get('participants', 0)}", ""]
    if topic.get("is_other"):
        lines.append(escape(short(topic.get("summary", ""), 180)))
    else:
        points = topic.get("points") or [{**item, "kind": "context"}
                                         for item in topic.get("highlights", [])[:2]]
        for point in points[:3]:
            label = POINT_LABELS.get(point.get("kind"), "Суть")
            lines.append(f"<b>{label}:</b> " + source_text(
                point["text"], point.get("message_id") or 0, chat_id, username, 170))
        if topic.get("keywords"):
            lines.extend(["", "Ключевые слова: " + escape(short(
                ", ".join(topic["keywords"][:5]), 110))])
    if not username and not str(chat_id).startswith("-100"):
        lines.append("Ссылки на реплики доступны в супергруппах.")
    return caption(lines)


def format_score_page(stats: dict[str, Any], page: int) -> str:
    scores = stats.get("participant_scores", [])
    item = scores[page]
    lines = [f"<b>🧠 IQ* текста · {page + 1}/{len(scores)}</b>",
             f"<b>{escape(short(item['name'], 48))} — {item['score']}</b>",
             f"Достаточность данных: {escape(item.get('confidence', 'низкая'))}",
             f"{message_count_label(item['messages'])}"
             f" · неповторяющихся: {item.get('unique_messages', item['messages'])}", ""]
    labels = {"vocabulary": "Словарь", "depth": "Развёрнутость", "reasoning": "Аргументация",
              "dialogue": "Диалог", "clarity": "Структура"}
    components = item.get("components", {})
    lines.extend(f"{label}: <b>{components[key]}/100</b>" for key, label in labels.items() if key in components)
    if item.get("explanation"):
        lines.extend(["", escape(short(item["explanation"], 180))])
    if item.get("improvements"):
        lines.append("Можно улучшить: " + escape(short(", ".join(item["improvements"]), 100)))
    lines.extend(["", "*Игровой балл текста, не оценка интеллекта. При малой выборке — предварительный."])
    return caption(lines)


def report_page(stats: dict[str, Any], chat_id: int, username: str | None,
                view: str = "brief", page: int = 0) -> str:
    if view == "topics":
        return format_topic_page(stats, chat_id, username, page)
    if view == "scores":
        return format_score_page(stats, page)
    return format_text_report(stats, chat_id, username, daily=stats.get("daily", False))


def report_keyboard(stats: dict[str, Any], report_id: str,
                    view: str = "brief", page: int = 0) -> dict[str, Any]:
    def button(label: str, target: str, index: int = 0) -> dict[str, str]:
        return {"text": label, "callback_data": f"report:{report_id}:{target}:{index}"}

    rows = []
    if view != "brief":
        total = len(stats.get("topics" if view == "topics" else "participant_scores", []))
        nav = []
        if page > 0:
            nav.append(button("‹ Назад", view, page - 1))
        nav.append(button("Кратко", "brief"))
        if page + 1 < total:
            nav.append(button("Далее ›", view, page + 1))
        rows.append(nav)
    tabs = []
    if stats.get("topics") and view != "topics":
        tabs.append(button("💬 Темы", "topics"))
    if stats.get("participant_scores") and view != "scores":
        tabs.append(button("🧠 IQ*", "scores"))
    if tabs:
        rows.append(tabs)
    return {"inline_keyboard": rows}
