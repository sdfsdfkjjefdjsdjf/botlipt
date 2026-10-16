"""Source-grounded topic detection with TF-IDF, reply context and extractive summaries."""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from math import log, sqrt
import re
from typing import Any

from text_analysis import (GENERIC_ADJECTIVES, NOMINAL_POS, URL_RE, WORD_RE,
                           clean_text, lexemes, morphology, tokens, word_info)


MAX_TOPICS = 9
TOPIC_GAP = 6 * 3600
# Conservative verb/noun families improve matching without broad labels such as
# "technology" that would combine unrelated conversations.
CONCEPT_FAMILIES = {
    "бронь": "бронирование", "бронировать": "бронирование", "забронировать": "бронирование",
    "встречаться": "встреча", "встретиться": "встреча",
    "фотографировать": "фотография", "фотка": "фотография", "фото": "фотография",
    "обновлять": "обновление", "обновить": "обновление",
    "установить": "установка", "устанавливать": "установка",
    "настроить": "настройка", "настраивать": "настройка",
    "ремонтировать": "ремонт", "починить": "ремонт",
}
GENERIC_TERMS = {"тема", "план", "проект", "вопрос", "проблема", "человек", "дело", "вещь",
                 "ситуация", "информация", "обсуждение", "участник"} | GENERIC_ADJECTIVES
TITLE_ACTIONS = {"обновление", "бронирование", "выбор", "настройка", "установка", "ремонт",
                 "перенос", "проверка", "разработка", "планирование", "поездка", "встреча"}
TITLE_PREPOSITIONS = {"в", "на", "для", "с", "о", "по", "из"}
PREFIX_RE = re.compile(
    r"^(?:(?:ну|короче|кстати|слушайте|ребята|друзья|согласна|согласен|"
    r"да|ага|точно|окей|мне кажется|я думаю|я считаю|думаю|хочу обсудить|"
    r"давайте обсудим|говорили о)\b[\s,:—–-]*)+", re.I)


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    norm = sqrt(sum(v * v for v in left.values()) * sum(v * v for v in right.values()))
    return sum(value * right.get(term, 0) for term, value in left.items()) / norm if norm else 0.0


def topic_terms(text: str) -> set[str]:
    return {CONCEPT_FAMILIES.get(term, term) for term in tokens(text)}


def sentences(text: str) -> list[str]:
    body = URL_RE.sub("", text[:12_000])
    return [" ".join(part.split()).strip(" •—–-")
            for part in re.split(r"(?<=[.!?])\s+|[\n\r]+", body) if part.strip()]


def heading_form(phrase: str) -> str:
    """Put the head of a source noun phrase in the nominative: 'дайджеста' -> 'дайджест'."""
    words = phrase.split()
    adjectives = []
    for index, word in enumerate(words):
        if not re.fullmatch(r"[А-Яа-яЁё-]+", word):
            break
        parsed = morphology().parse(word)[0]
        if parsed.tag.POS == "ADJF":
            adjectives.append((index, parsed))
            continue
        if parsed.tag.POS == "NOUN":
            nominative = parsed.inflect({"nomn"})
            if nominative:
                words[index] = nominative.word
                agreement = {"nomn", nominative.tag.number}
                if nominative.tag.number == "sing" and nominative.tag.gender:
                    agreement.add(nominative.tag.gender)
                for adjective_index, adjective in adjectives:
                    adjusted = adjective.inflect(agreement)
                    if adjusted:
                        words[adjective_index] = adjusted.word
            break
        break
    return " ".join(words)


def describe_topic(items: list[dict[str, Any]], idf: dict[str, float]) -> dict[str, Any]:
    support: Counter[str] = Counter()
    nominal_terms: set[str] = set()
    for item in items:
        support.update(sorted(set(tokens(item.get("text") or ""))))
        nominal_terms.update(lemma for _, lemma, pos in lexemes(item.get("text") or "")
                             if (pos in NOMINAL_POS or pos is None) and lemma not in GENERIC_ADJECTIVES)
    importance = {term: count * idf.get(term, 1) for term, count in support.items()}
    keywords = sorted(nominal_terms, key=lambda term: (-importance[term], term))[:6]
    candidates = []
    phrases: dict[tuple[str, ...], list] = {}
    for item_index, item in enumerate(items):
        for sentence_index, sentence in enumerate(sentences(item.get("text") or "")):
            sentence = PREFIX_RE.sub("", sentence).strip()
            features = lexemes(sentence)
            terms = {lemma for _, lemma, _ in features}
            if not terms or len(sentence) < 8:
                continue
            relevance = sum(importance.get(term, 0) for term in sorted(terms)) / sqrt(len(terms))
            # Prefer an explanatory sentence to a one-word reaction or giant paste.
            relevance *= min(1, len(terms) / 4) * min(1, 240 / max(1, len(sentence)))
            candidates.append((relevance, item_index, sentence_index, sentence, terms, item.get("message_id")))

            # Noun/adjective phrases must be adjacent in the ORIGINAL sentence.
            # Removing stop words before extracting phrases would invent combinations.
            phrase_source = clean_text(sentence)
            matches = list(WORD_RE.finditer(phrase_source))
            run: list[tuple[str, str, str | None]] = []
            previous_end = 0
            meaningful = {lemma for _, lemma, _ in features}
            for match in matches + [None]:
                if match is not None:
                    original = match.group()
                    lemma, pos = word_info(original)
                    valid = lemma in meaningful and pos in NOMINAL_POS
                    connector = (original.casefold() in TITLE_PREPOSITIONS and bool(run)
                                 and run[-1][2] != "PREP")
                    if connector:
                        valid, pos = True, "PREP"
                    gap = phrase_source[previous_end:match.start()]
                    if gap.strip():
                        run = []
                else:
                    valid = False
                if valid:
                    run.append((original, lemma, pos))
                    for length in range(2, min(6, len(run)) + 1):
                        piece = run[-length:]
                        if piece[0][2] == "PREP" or piece[-1][2] == "PREP":
                            continue
                        if not any(pos in {"NOUN", "LATN"} for _, _, pos in piece):
                            continue
                        key = tuple(term for _, term, pos in piece if pos != "PREP")
                        if len(set(key)) < 2:
                            continue
                        phrase = " ".join(word for word, _, _ in piece)
                        if len(phrase) <= 74:
                            # Store the earliest source form and distinct-message support.
                            entry = phrases.setdefault(key, [phrase, item.get("message_id"), set()])
                            entry[2].add(item_index)
                else:
                    run = []
                if match is not None:
                    previous_end = match.end()

    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    highlights = []
    selected_terms: list[set[str]] = []
    selected_items: set[int] = set()
    for _, item_index, _, sentence, terms, message_id in candidates:
        if item_index in selected_items:
            continue
        if any(len(terms & chosen) / max(1, len(terms | chosen)) > 0.65 for chosen in selected_terms):
            continue
        highlights.append({"text": clip(sentence, 210), "message_id": message_id})
        selected_terms.append(terms)
        selected_items.add(item_index)
        if len(highlights) == 2:
            break

    title, anchor_id = "Обсуждение", items[0].get("message_id")
    if phrases:
        def phrase_rank(entry):
            key, (_, _, sources) = entry
            score = sum(importance.get(term, 0) for term in sorted(set(key))) / sqrt(len(key))
            # Repeated phrases beat a one-off longer phrase of comparable relevance.
            action = 1.25 if key[0] in TITLE_ACTIONS else 1
            return score * action * (1 + 0.10 * min(3, len(sources))), -len(key)
        _, (title, anchor_id, _) = max(phrases.items(), key=phrase_rank)
        title = heading_form(title)
    elif highlights:
        title = clip(highlights[0]["text"].rstrip(".!?"), 78)
        anchor_id = highlights[0]["message_id"]
    title = title[0].upper() + title[1:] if title else "Обсуждение"
    ids = list(dict.fromkeys(mid for mid in [anchor_id] + [h["message_id"] for h in highlights] if mid))
    summary = " ".join(h["text"] if h["text"].endswith((".", "!", "?", "…")) else h["text"] + "."
                       for h in highlights)
    return {"title": title, "summary": summary,
            "highlights": highlights, "keywords": keywords, "message_ids": ids,
            "count": len(items), "participants": len({str(m.get('sender_id')) for m in items}),
            "start_date": items[0]["date"], "end_date": items[-1]["date"]}


def build_topics(text_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Process the whole day; bounded inverted-index candidates prevent all-pairs work."""
    messages = sorted(text_messages, key=lambda m: (int(m["date"]), m.get("message_id") or 0))
    features = [topic_terms(message.get("text") or "") for message in messages]
    document_frequency: Counter[str] = Counter(term for terms in features for term in terms)
    idf = {term: (1 + log((1 + len(messages)) / (1 + count)))
                 * (0.55 if term in GENERIC_TERMS else 1)
           for term, count in document_frequency.items()}
    clusters: list[dict[str, Any]] = []
    inverted: dict[str, OrderedDict[int, None]] = defaultdict(OrderedDict)
    assignments: dict[int, int] = {}
    for message, terms in zip(messages, features):
        parent = assignments.get(message.get("reply_to_message_id"))
        if not terms - GENERIC_TERMS:
            # A short reply belongs to a known thread but cannot create its own topic.
            if parent is not None:
                clusters[parent]["items"].append(message)
                clusters[parent]["last_date"] = int(message["date"])
                if message.get("message_id"):
                    assignments[message["message_id"]] = parent
            continue
        vector = {term: idf[term] for term in sorted(terms)}
        votes: Counter[int] = Counter()
        for term in sorted(terms):
            votes.update(inverted[term].keys())
        candidates = sorted(votes, key=lambda index: (-votes[index], index))[:48]
        if parent is not None and parent not in candidates:
            candidates.append(parent)
        best_index, best_score = None, 0.0
        for index in candidates:
            cluster = clusters[index]
            if int(message["date"]) - cluster["last_date"] > TOPIC_GAP and index != parent:
                continue
            core = dict(cluster["support"].most_common(32))
            centroid = {term: (count / len(cluster["vectors"])) * idf[term]
                        for term, count in core.items()}
            recent = cluster["vectors"][-5:]
            similarity = max([cosine(vector, centroid)] + [cosine(vector, old) for old in recent])
            shared = terms & core.keys()
            # A brief explicit answer can carry its meaning through the reply relation.
            reply_match = index == parent and (len(terms) <= 3 or bool(shared))
            short_followup = (len(terms) <= 4 and bool(shared - GENERIC_TERMS)
                              and int(message["date"]) - cluster["last_date"] <= 180
                              and similarity >= 0.20)
            if reply_match:
                similarity = max(0.66, similarity + 0.18)
            if not (reply_match or short_followup or (similarity >= 0.24 and
                                   (len(shared) >= 2 or similarity >= 0.51))):
                continue
            if similarity > best_score:
                best_index, best_score = index, similarity
        if best_index is None:
            best_index = len(clusters)
            clusters.append({"items": [], "support": Counter(), "vectors": [],
                             "last_date": int(message["date"])})
        cluster = clusters[best_index]
        cluster["items"].append(message)
        cluster["support"].update(sorted(terms))
        cluster["vectors"].append(vector)
        cluster["last_date"] = int(message["date"])
        for term in terms:
            posting = inverted[term]
            posting.pop(best_index, None)
            posting[best_index] = None
            if len(posting) > 96:
                posting.popitem(last=False)
        if message.get("message_id"):
            assignments[message["message_id"]] = best_index

    clusters.sort(key=lambda group: (-len(group["items"]), -group["last_date"]))
    topics = [describe_topic(group["items"], idf) for group in clusters[:MAX_TOPICS]]
    if len(clusters) > MAX_TOPICS:
        rest = sorted((m for group in clusters[MAX_TOPICS:] for m in group["items"]),
                      key=lambda m: (m["date"], m.get("message_id") or 0))
        topics.append({"title": "Другие обсуждения", "count": len(rest),
                       "summary": f"Ещё отдельных тем: {len(clusters) - MAX_TOPICS}.",
                       "keywords": [], "highlights": [], "message_ids": [], "is_other": True,
                       "participants": len({str(m.get('sender_id')) for m in rest})})
    return topics
