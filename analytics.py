"""Local, deterministic analytics for the last 24 hours of a Telegram group."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo


WINDOW_SECONDS = 24 * 60 * 60
WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
URL_RE = re.compile(r"https?://\S+|t\.me/\S+", re.IGNORECASE)
STOPWORDS = set("""
а абы аж ай без более бы был была были было быть в вам вас ваш ваша ваши ведь весь
во вот все всего всей всех где да даже для до его ее ещё же за зачем здесь и из
или им их к как какая какие какой когда кто ли либо мне мной мы на над нам нас
наш не него неё нет ни них но ну о об он она они оно от по под после при про с
сам сама себе себя со так также там те тебе тем то того тоже той только тот тут
ты у уже что чтобы чьё эта эти это этот я ага давай давайте есть если очень
просто потом сейчас сегодня вчера завтра можно надо нужно ещё уже всем всем
будет будем будут такой такая такие этом этом потому поэтому спасибо привет
пожалуйста ок окей нету щас типо типа либо вроде вообще там тут нас вам них
the and for are was were you your this that with from have has not but can
will just about what when where how who why into over then than they them
иә жоқ мен сен біз сіз олар бұл сол үшін бар менің сенің оның осы болып
""".split())


def tokens(text: str) -> list[str]:
    cleaned = URL_RE.sub(" ", text.casefold())
    return [w for w in WORD_RE.findall(cleaned) if w not in STOPWORDS]


def clip(text: str, limit: int = 155) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def analyze(messages: list[dict[str, Any]], now: int, tz_name: str = "UTC") -> dict[str, Any]:
    """Return aggregate statistics and extractive discussion highlights."""
    try:
        timezone = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        timezone = ZoneInfo("UTC")
        tz_name = "UTC"

    recent = [m for m in messages if now - WINDOW_SECONDS <= int(m["date"]) <= now]
    recent.sort(key=lambda m: (m["date"], m.get("message_id", 0)))
    authored = Counter()
    names: dict[str, str] = {}
    word_counts: Counter[str] = Counter()
    hourly = [0] * 24
    photos: list[dict[str, Any]] = []
    text_messages: list[dict[str, Any]] = []
    users = set()

    for msg in recent:
        sender_id = str(msg.get("sender_id") or "unknown")
        name = msg.get("sender_name") or "Неизвестный"
        names[sender_id] = name
        users.add(sender_id)
        authored[sender_id] += 1
        age_hour = min(23, max(0, (now - int(msg["date"])) // 3600))
        hourly[23 - age_hour] += 1

        body = (msg.get("text") or "").strip()
        if body and not body.startswith("/"):
            text_messages.append(msg)
            word_counts.update(tokens(body))
        if msg.get("photo_file_id"):
            photos.append(msg)

    ranked_authors = [
        {"name": names[sender_id], "count": count, "sender_id": sender_id}
        for sender_id, count in authored.most_common(8)
    ]

    # Pick different representative messages. These are excerpts, not AI summaries.
    candidates = []
    for msg in text_messages:
        body = " ".join((msg.get("text") or "").split())
        ts = set(tokens(body))
        if len(ts) < 2 or len(body) < 14:
            continue
        score = sum(min(word_counts[w], 8) for w in ts) / max(len(ts), 1)
        score += min(len(body), 180) / 180
        candidates.append((score, msg, ts))
    candidates.sort(key=lambda item: (-item[0], -item[1]["date"]))
    highlights = []
    selected_sets: list[set[str]] = []
    for _, msg, ts in candidates:
        if any(len(ts & old) / max(len(ts | old), 1) > 0.45 for old in selected_sets):
            continue
        keywords = sorted(ts, key=lambda w: (-word_counts[w], w))[:3]
        highlights.append({
            "label": ", ".join(keywords),
            "excerpt": clip(msg.get("text") or ""),
            "sender": msg.get("sender_name") or "Неизвестный",
            "time": datetime.fromtimestamp(msg["date"], timezone).strftime("%H:%M"),
        })
        selected_sets.append(ts)
        if len(highlights) == 3:
            break

    start = datetime.fromtimestamp(now - WINDOW_SECONDS, timezone)
    end = datetime.fromtimestamp(now, timezone)
    return {
        "start": start.strftime("%d.%m %H:%M"),
        "end": end.strftime("%d.%m %H:%M"),
        "timezone": tz_name,
        "messages": len(recent),
        "participants": len(users),
        "photo_count": len(photos),
        "text_count": len(text_messages),
        "authors": ranked_authors,
        "words": word_counts.most_common(18),
        "highlights": highlights,
        "photos": photos[-6:][::-1],
        "hourly": hourly,
    }
