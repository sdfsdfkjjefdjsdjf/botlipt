"""Local analytics for a Telegram group's most recent 24 hours."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import re
from statistics import mean
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
будет будем будут такой такая такие этом потому поэтому спасибо привет
пожалуйста ок окей нету щас типо типа либо вроде вообще там тут нас вам них
the and for are was were you your this that with from have has not but can
will just about what when where how who why into over then than they them
иә жоқ мен сен біз сіз олар бұл сол үшін бар менің сенің оның осы болып
""".split())
RUSSIAN_ENDINGS = tuple(sorted("""
иями ями ами ость остей иями ениями ение ения ание ания
овать ировать лись лась лось лить ать ять еть ить ют ят ет ит
ыми ими ого его ому ему ами ями ах ях ам ям ом ем ой ей
ия ие ые ый ий ая яя ое ее ы и а я у ю е о ов ев ь
""".split(), key=len, reverse=True))


def tokens(text: str) -> list[str]:
    cleaned = URL_RE.sub(" ", text.casefold())
    return [word for word in WORD_RE.findall(cleaned) if word not in STOPWORDS]


def stem(word: str) -> str:
    """A small stem used only to group similar chat messages."""
    if not re.search("[а-яё]", word):
        return word
    for ending in RUSSIAN_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_topics(text_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge nearby messages with shared meaningful words into visible themes."""
    clusters: list[dict[str, Any]] = []
    for msg in text_messages[-350:]:
        body = " ".join((msg.get("text") or "").split())
        if len(body) < 7:
            continue
        terms = {stem(word) for word in tokens(body)}
        if not terms:
            continue
        best_index, best_score = None, 0.0
        for index, cluster in enumerate(clusters):
            if msg["date"] - cluster["last_date"] > 6 * 3600:
                continue
            shared = terms & cluster["terms"]
            if not shared:
                continue
            score = len(shared) / min(len(terms), len(cluster["terms"]))
            if (score >= 0.28 or len(shared) >= 2) and score > best_score:
                best_index, best_score = index, score
        if best_index is None:
            clusters.append({"items": [msg], "terms": terms, "last_date": msg["date"]})
        else:
            cluster = clusters[best_index]
            cluster["items"].append(msg)
            cluster["terms"].update(terms)
            cluster["last_date"] = msg["date"]

    clusters.sort(key=lambda c: (-len(c["items"]), -c["last_date"]))
    if len(clusters) > 3:
        overflow = clusters[3:]
        rest = sorted((msg for group in overflow for msg in group["items"]), key=lambda m: m["date"])
        clusters = clusters[:3] + [{"items": rest, "terms": set(), "last_date": rest[-1]["date"],
                                    "other": True}]

    topics = []
    for cluster in clusters:
        items = cluster["items"]
        if cluster.get("other"):
            title = "Другие темы"
        else:
            counts = Counter(word for item in items for word in tokens(item.get("text") or ""))
            title = " · ".join(word.capitalize() if i == 0 else word
                               for i, (word, _) in enumerate(counts.most_common(3))) or "Обсуждение"
        chosen = []
        seen_texts = set()
        for item in sorted(items, key=lambda m: m["date"]):
            body = " ".join((item.get("text") or "").split()).casefold()
            if body not in seen_texts:
                chosen.append(item)
                seen_texts.add(body)
        if len(chosen) > 4:
            chosen = [chosen[0], *chosen[-3:]]
        combined = " • ".join(clip(item.get("text") or "", 240) for item in chosen)
        topics.append({"title": title, "summary": clip(combined, 640), "count": len(items)})
    return topics


def estimate_participant_scores(text_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return playful text-quality scores; these are not psychometric IQ estimates."""
    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for msg in text_messages:
        by_person[str(msg.get("sender_id") or "unknown")].append(msg)
    result = []
    for sender_id, items in by_person.items():
        all_tokens = [word for msg in items for word in tokens(msg.get("text") or "")]
        if len(items) < 2 or len(all_tokens) < 8:
            continue
        vocabulary = min(1.0, len({stem(word) for word in all_tokens}) / (0.65 * len(all_tokens)))
        elaboration = min(1.0, mean(min(len(tokens(msg.get("text") or "")), 30) for msg in items) / 14)
        questions = min(1.0, sum("?" in (msg.get("text") or "") for msg in items) / (0.25 * len(items)))
        score = round(72 + 58 * (0.35 * vocabulary + 0.55 * elaboration + 0.10 * questions))
        result.append({"sender_id": sender_id, "name": items[-1].get("sender_name") or "Участник",
                       "score": score, "messages": len(items)})
    return sorted(result, key=lambda item: (-item["score"], -item["messages"], item["name"]))


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
    word_counts: Counter[str] = Counter()
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
            word_counts.update(tokens(body))
        if msg.get("photo_file_id"):
            photos.append(msg)

    scores = estimate_participant_scores(text_messages)
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
        "words": word_counts.most_common(18),
        "topics": build_topics(text_messages),
        "participant_scores": scores,
        "average_score": round(mean(item["score"] for item in scores)) if scores else None,
        "photos": photos[-6:][::-1],
        "hourly": hourly,
    }
