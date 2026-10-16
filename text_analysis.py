"""Bounded, cached Russian/English text features. No network calls or model downloads."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
import re
import unicodedata

import pymorphy3


MAX_TEXT_LENGTH = 12_000
WORD_RE = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", re.UNICODE)
URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"(?<!\w)(?:@[\w]+|/[\w]+(?:@[\w]+)?)|[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)
STOPWORDS = set("""
а абы аж ай без более больше бы был была были было быть в вам вас ваш ваша ваши ведь весь
во вот все всё всего всей всех где да даже для до его ее её ещё еще же за зачем здесь и из
или им их к как какая какие какой когда кто ли либо мне мной мы на над нам нас
наш не него неё нет ни них но ну о об он она они оно от по под после при про с
сам сама себе себя со так также там те тебе тем то того тоже той только тот тут
ты у уже что чтоб чтобы чьё эта эти это этот я ага давай давайте есть если очень
просто потом сейчас сегодня вчера завтра можно надо нужно всем будет будем будут
такой такая такие этом потому поэтому спасибо привет пожалуйста ок окей нету щас
типо типа вроде вообще короче кстати слушай слушайте ребята друзья ладно блин
который которая которое которые которых которому которым которую почему насколько
сколько скок чё че чо чего чём чем ниче ничего нечто неужели разве почти лишь
такого такому таких какой-то что-то кто-то где-то как-то всё-таки всё-равно
это-то этот-то всё-то ещё-то делать сделать знать сказать говорить писать
думать считать хотеть мочь становиться стать казаться иметь давать получать
идти приходить смотреть видеть понимать использовать нужный разный всякий
некоторый любой весь такой свой наш ваш мой твой сам самый другой каждый
прям реально получается кароче ваще плиз лол кек ахах ахаха ахахах хаха хахаха
the and for are was were you your this that with from have has not but can
will just about what when where how who why into over then than they them
its it's i'm ive i've don't doesnt doesn't didn't isn't arent aren't you're
would should could here there their our ours also only really very some any
all more much many been being does did doing done get got make made think
know say said want need use using thing things something everything nothing
yeah yes nope okay hello thanks lol lmao haha btw well like actually
иә жоқ мен сен біз сіз олар бұл сол үшін бар менің сенің оның осы болып
қалай қашан не және да де ма ме бірақ ғана енді бүгін ертең кеше керек
""".split())
STOPWORDS = {word.replace("ё", "е") for word in STOPWORDS}
ALIASES = {
    "телега": "telegram", "телеграм": "telegram", "телеграмм": "telegram",
    "линух": "linux", "линукс": "linux", "федора": "fedora",
    "винда": "windows", "виндовс": "windows", "виндовз": "windows",
    "осу": "osu", "дискорд": "discord", "стим": "steam",
    "гпт": "chatgpt", "gpt": "chatgpt", "чатгпт": "chatgpt",
    "ии": "ии", "ai": "ии", "гпу": "видеокарта", "gpu": "видеокарта",
}
SHORT_TERMS = {"ии", "вк", "кв", "тх", "ai", "ui", "ux", "vr", "pc", "c", "go", "cs", "os"}
GRAMMAR_POS = {"NPRO", "PREP", "CONJ", "PRCL", "INTJ", "ADVB", "PRED"}
NOMINAL_POS = {"NOUN", "ADJF", "ADJS", "LATN"}
GENERIC_ADJECTIVES = {"новый", "старый", "хороший", "плохой", "большой", "маленький",
                      "интересный", "прикольный", "крутой", "классный", "нормальный", "обычный", "важный"}
FILLER_RE = re.compile(r"(?:(?:а?ха)+х?|(?:ha|he){2,}|(.)\1{3,})$", re.I)


@lru_cache(maxsize=1)
def morphology() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text[:MAX_TEXT_LENGTH])
    return IDENTIFIER_RE.sub(" ", URL_RE.sub(" ", text))


@lru_cache(maxsize=32_768)
def word_info(word: str) -> tuple[str, str | None]:
    word = word.casefold().replace("ё", "е")
    if word in ALIASES:
        return ALIASES[word], "NOUN"
    if not re.search(r"[а-я]", word):
        return word, "LATN"
    parsed = morphology().parse(word)[0]
    lemma = parsed.normal_form.replace("ё", "е")
    return ALIASES.get(lemma, lemma), parsed.tag.POS


def lexemes(text: str) -> list[tuple[str, str, str | None]]:
    """Return (source spelling, lemma, POS), excluding grammatical/filler words."""
    result = []
    for original in WORD_RE.findall(clean_text(text)):
        word = original.casefold().replace("ё", "е")
        if (word in STOPWORDS or len(word) > 36 or (len(word) < 2 and word not in SHORT_TERMS)
                or FILLER_RE.fullmatch(word)):
            continue
        lemma, pos = word_info(word)
        if lemma in STOPWORDS or pos in GRAMMAR_POS:
            continue
        if len(lemma) < 3 and lemma not in SHORT_TERMS:
            continue
        result.append((original, lemma, pos))
    return result


def tokens(text: str) -> list[str]:
    return [lemma for _, lemma, _ in lexemes(text)]


def normalized_body(text: str) -> str:
    return " ".join(WORD_RE.findall(clean_text(text).casefold().replace("ё", "е")))


def meaningful_words(messages: list[dict], limit: int = 18) -> list[tuple[str, int]]:
    """Count messages mentioning a concept, so a pasted word cannot flood the list."""
    mentions: Counter[str] = Counter()
    for message in messages:
        mentions.update({lemma for _, lemma, pos in lexemes(message.get("text") or "")
                         if (pos in NOMINAL_POS or pos is None) and lemma not in GENERIC_ADJECTIVES})
    return sorted(mentions.items(), key=lambda item: (-item[1], item[0]))[:limit]
