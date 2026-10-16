"""Local analytics for a Telegram group's most recent 24 hours."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from scoring import estimate_participant_scores
from text_analysis import meaningful_words, tokens
from topics import build_topics


WINDOW_SECONDS = 24 * 60 * 60


def message_count_label(count: int) -> str:
    ending = "сообщение" if count % 10 == 1 and count % 100 != 11 else (
        "сообщения" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14)
        else "сообщений")
    return f"{count} {ending}"


def analyze(messages: list[dict[str, Any]], now: int, tz_name: str = "UTC") -> dict[str, Any]:
    try:
        timezone = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        timezone = ZoneInfo("UTC")
        tz_name = "UTC"

    recent = [msg for msg in messages if now - WINDOW_SECONDS <= int(msg["date"]) <= now]
    recent.sort(key=lambda msg: (msg["date"], msg.get("message_id", 0)))
    authored: Counter[str] = Counter()
    names: dict[str, str] = {}
    hourly = [0] * 24
    photos: list[dict[str, Any]] = []
    text_messages: list[dict[str, Any]] = []

    for msg in recent:
        sender_id = str(msg.get("sender_id") or "unknown")
        names[sender_id] = msg.get("sender_name") or "Неизвестный"
        authored[sender_id] += 1
        age_hour = min(23, max(0, (now - int(msg["date"])) // 3600))
        hourly[23 - age_hour] += 1
        body = (msg.get("text") or "").strip()
        if body and not body.startswith("/"):
            text_messages.append(msg)
        if msg.get("photo_file_id"):
            photos.append(msg)

    scores = estimate_participant_scores(text_messages)
    topics = build_topics(text_messages)
    start = datetime.fromtimestamp(now - WINDOW_SECONDS, timezone)
    end = datetime.fromtimestamp(now, timezone)
    return {
        "start": start.strftime("%d.%m %H:%M"),
        "end": end.strftime("%d.%m %H:%M"),
        "timezone": tz_name,
        "messages": len(recent),
        "participants": len(authored),
        "photo_count": len(photos),
        "text_count": len(text_messages),
        "authors": [{"name": names[sender_id], "count": count, "sender_id": sender_id}
                    for sender_id, count in authored.most_common(6)],
        "words": meaningful_words(text_messages),
        "word_count_mode": "messages",
        "topics": topics,
        "topic_message_count": sum(topic["count"] for topic in topics),
        "unclassified_count": len(text_messages) - sum(topic["count"] for topic in topics),
        "participant_scores": scores,
        "score_version": 2,
        "average_score": round(mean(item["score"] for item in scores)) if scores else None,
        "photos": sorted(
            (item for item in photos
             if int(item.get("reaction_count") or 0) + int(item.get("reply_count") or 0) > 0),
            key=lambda item: (int(item.get("reaction_count") or 0) + int(item.get("reply_count") or 0),
                              int(item.get("reaction_count") or 0), item["date"], item["message_id"]),
            reverse=True,
        )[:6],
        "hourly": hourly,
    }
