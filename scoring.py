"""Explainable, deterministic chat-text scores. These are NOT psychometric IQ."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import sqrt
import re
from statistics import mean
from typing import Any

from text_analysis import WORD_RE, clean_text, normalized_body, tokens


COMPONENT_LABELS = {
    "vocabulary": "словарь", "depth": "развёрнутость", "reasoning": "аргументация",
    "dialogue": "диалог", "clarity": "структура текста",
}
REASON_PATTERNS = {
    "reasons": re.compile(r"\b(?:потому\s+что|поскольку|поэтому|следовательно|так\s+как|because|therefore)\b", re.I),
    "examples": re.compile(r"\b(?:например|к\s+примеру|for\s+example|for\s+instance)\b", re.I),
    "comparisons": re.compile(r"\b(?:в\s+отличие\s+от|по\s+сравнению\s+с|с\s+одной\s+стороны|whereas|compared\s+to)\b", re.I),
    "conditions": re.compile(r"\b(?:если|if)\b.+?[,;].+", re.I),
}


def reasoning_signals(text: str) -> set[str]:
    """Markers count once per message, only when accompanied by meaningful content."""
    text = clean_text(text)
    if len(set(tokens(text))) < 5:
        return set()
    signals = set()
    for category, pattern in REASON_PATTERNS.items():
        for match in pattern.finditer(text):
            # Conditions include their consequent inside the matched span.
            evidence = text[match.start():] if category == "conditions" else text[match.end():]
            if len(set(tokens(evidence))) >= 3:
                signals.add(category)
                break
    return signals


def unique_samples(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """Ignore exact and near duplicates, including copies with a changed counter/order."""
    unique = []
    fingerprints = set()
    recent: list[set[str]] = []
    for item in items:
        body = normalized_body(item.get("text") or "")
        terms = set(tokens(item.get("text") or ""))
        if body in fingerprints:
            continue
        fingerprints.add(body)
        if len(terms) >= 4 and any(len(terms & old) / max(1, len(terms | old)) >= 0.86
                                   for old in recent[-60:]):
            continue
        unique.append(item)
        if len(terms) >= 4:
            recent.append(terms)
    return unique, 1 - len(unique) / max(1, len(items))


def vocabulary_diversity(words: list[str]) -> float:
    """Moving windows avoid the usual type/token penalty for a longer chat history."""
    if not words:
        return 0.0
    window = min(40, len(words))
    offsets = range(0, len(words) - window + 1, max(1, window // 4))
    diversity = mean(len(set(words[start:start + window])) / window for start in offsets)
    # A small collection of fancy words cannot earn the maximum by itself.
    breadth = min(1, len(set(words)) / 45)
    return 0.70 * diversity + 0.30 * breadth


def estimate_participant_scores(text_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = sorted(text_messages, key=lambda m: (int(m["date"]), m.get("message_id") or 0))
    by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {m["message_id"]: m for m in messages if m.get("message_id") is not None}
    term_sets = {id(m): set(tokens(m.get("text") or "")) for m in messages}
    engaged_ids: set[int] = set()
    for index, msg in enumerate(messages):
        sender_id = str(msg.get("sender_id") or "unknown")
        by_person[sender_id].append(msg)
        terms = term_sets[id(msg)]
        if len(terms) < 2:
            continue
        parent = by_id.get(msg.get("reply_to_message_id"))
        if (parent and str(parent.get("sender_id") or "unknown") != sender_id
                and 0 <= int(msg["date"]) - int(parent["date"]) <= 24 * 3600
                and (terms & term_sets[id(parent)] or "?" in (parent.get("text") or ""))):
            engaged_ids.add(id(msg))
            continue
        for previous in reversed(messages[max(0, index - 6):index]):
            if int(msg["date"]) - int(previous["date"]) > 1800:
                break
            if str(previous.get("sender_id") or "unknown") == sender_id:
                continue
            old = term_sets[id(previous)]
            overlap = len(terms & old)
            if overlap >= 2 or (overlap and overlap / max(1, min(len(terms), len(old))) >= 0.5):
                engaged_ids.add(id(msg))
                break

    result = []
    for sender_id, items in by_person.items():
        total_words = sum(len(tokens(m.get("text") or "")) for m in items)
        if len(items) < 2 or total_words < 8:
            continue
        samples, duplicates = unique_samples(items)
        bodies = [m.get("text") or "" for m in samples]
        word_lists = [tokens(body) for body in bodies]
        # Within-message repetition is bounded for length/vocabulary features.
        bounded_words = []
        for words in word_lists:
            counts: Counter[str] = Counter()
            for word in words[:160]:
                counts[word] += 1
                if counts[word] <= 2:
                    bounded_words.append(word)
        vocabulary = vocabulary_diversity(bounded_words)
        depth = mean(min(1, len(set(words)) / 18) for words in word_lists)
        signals = [reasoning_signals(body) for body in bodies]
        evidence = Counter(signal for categories in signals for signal in categories)
        reasoned = sum(bool(categories) for categories in signals)
        reasoning = min(1, reasoned / max(2, 0.55 * len(samples)))
        reasoning *= 0.7 + 0.3 * min(1, len(evidence) / 3)
        engaged = sum(id(m) in engaged_ids for m in samples)
        dialogue = min(1, engaged / max(2, 0.5 * len(samples)))
        clarity_values = []
        repetitions = []
        for body, words in zip(bodies, word_lists):
            all_words = WORD_RE.findall(clean_text(body))
            density = min(1, len(set(words)) / max(1, 0.6 * len(all_words)))
            structured = bool(re.search(r"[.!?;:]|\n", body)) and len(words) >= 4
            # Punctuation is only a small signal, not a spelling/grammar grade.
            clarity_values.append(0.8 * density + 0.2 * structured)
            repetitions.append(max(0, 1 - len(set(words)) / max(1, len(words)) - 0.25))
        clarity = mean(clarity_values)
        repeated_words = mean(repetitions)
        components = {"vocabulary": vocabulary, "depth": depth, "reasoning": reasoning,
                      "dialogue": dialogue, "clarity": clarity}
        quality = (0.18 * vocabulary + 0.24 * depth + 0.28 * reasoning
                   + 0.18 * dialogue + 0.12 * clarity)
        reliability = sqrt(min(1, len(samples) / 16) * min(1, len(bounded_words) / 180))
        confidence = "высокая" if reliability >= 0.80 else "средняя" if reliability >= 0.45 else "низкая"
        penalty = 18 * duplicates + 16 * repeated_words
        # Small samples shrink toward 100 instead of producing extreme numbers.
        score = max(70, min(130, round(100 + (70 + 60 * quality - 100)
                                       * (0.30 + 0.70 * reliability) - penalty)))
        strongest = sorted(components, key=lambda key: (-components[key], key))
        strengths = [COMPONENT_LABELS[key] for key in strongest[:2] if components[key] >= 0.5]
        improvements = [COMPONENT_LABELS[key] for key in reversed(strongest) if components[key] < 0.5][:2]
        reasons = ["сильнее: " + ", ".join(strengths)] if strengths else []
        if duplicates >= 0.2:
            reasons.append(f"повторы сообщений: {duplicates:.0%}")
        if repeated_words >= 0.2:
            reasons.append("много повторов слов")
        result.append({
            "sender_id": sender_id, "name": items[-1].get("sender_name") or "Участник",
            "score": score, "messages": len(items), "unique_messages": len(samples),
            "word_count": total_words, "unique_words": len(set(bounded_words)),
            "reasoning": reasoned, "dialogue": engaged, "evidence": dict(evidence),
            "components": {key: round(value * 100) for key, value in components.items()},
            "confidence": confidence, "confidence_value": round(100 * reliability),
            "duplicate_ratio": round(duplicates, 3), "strengths": strengths,
            "improvements": improvements, "explanation": "; ".join(reasons),
        })
    return sorted(result, key=lambda item: (-item["score"], -item["messages"], item["name"], item["sender_id"]))
