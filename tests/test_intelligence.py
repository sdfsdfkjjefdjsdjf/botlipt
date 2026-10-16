"""Behavioural checks for topics, vocabulary, scoring and Telegram report delivery."""

from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from analytics import analyze
from bot import MessageStore, format_text_report, send_dashboard, split_text_report
from render import render_dashboard
from scoring import estimate_participant_scores, reasoning_signals
from text_analysis import meaningful_words, tokens
from topics import build_topics


NOW = 2_000_000_000


def messages(bodies, sender="1"):
    return [{"date": NOW - 6000 + index * 10, "message_id": index + 1,
             "sender_id": sender, "sender_name": "Аня", "text": body}
            for index, body in enumerate(bodies)]


class BalancedHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError("Unbalanced Telegram HTML")


class LanguageTests(unittest.TestCase):
    def test_grammatical_and_chat_fillers_do_not_become_keywords(self):
        self.assertEqual(tokens("как когда почему сколько сегодня завтра я думаю ну типа "
                                "вообще ахахаха как-то что-то который пожалуйста уже"), [])

    def test_links_mentions_emails_and_commands_do_not_become_topics(self):
        self.assertEqual(tokens("https://example.org/драйверы www.example.net "
                                "t.me/канал/42 @username mail@example.org /dashboard@OurBot"), [])

    def test_russian_inflections_merge_and_mentions_are_not_occurrences(self):
        items = messages(["Видеокарта видеокарты видеокарту", "Выбор видеокарты", "Видеокартой довольны"])
        self.assertIn(("видеокарта", 3), meaningful_words(items))
        self.assertEqual([word for word, _ in meaningful_words(items)].count("видеокарта"), 1)

    def test_brand_aliases_and_short_technical_terms_survive(self):
        terms = tokens("Линукс linux телеграм Telegram осу osu UI UX ИИ AI")
        self.assertEqual(terms.count("linux"), 2)
        self.assertEqual(terms.count("telegram"), 2)
        self.assertEqual(terms.count("osu"), 2)
        self.assertIn("ui", terms)
        self.assertIn("ux", terms)
        self.assertEqual(terms.count("ии"), 2)

    def test_one_word_spam_cannot_outrank_a_discussed_word(self):
        words = meaningful_words(messages(["банан " * 100, "Видеокарта NVIDIA", "Новая видеокарта"]))
        self.assertLess(words.index(("видеокарта", 2)), words.index(("банан", 1)))

    def test_no_truncated_fragments_from_overlong_words(self):
        self.assertEqual(tokens("а" * 1000), [])


class TopicTests(unittest.TestCase):
    def test_related_messages_merge_with_inflection_and_reply_context(self):
        items = messages(["Обновление драйверов NVIDIA на Fedora.",
                          "Драйвер NVIDIA исправил сон монитора после обновления.",
                          "Да, теперь работает!", "Бронь столика в кафе на субботу."])
        items[1]["sender_id"] = "2"
        items[2]["reply_to_message_id"] = 2
        topics = build_topics(items)
        self.assertEqual(sorted(topic["count"] for topic in topics), [1, 3])
        main = topics[0]
        self.assertEqual(main["participants"], 2)
        self.assertIn("драйвер", main["keywords"])
        self.assertTrue(main["summary"])
        self.assertTrue(main["message_ids"])

    def test_fillers_alone_do_not_create_topics(self):
        self.assertEqual(build_topics(messages(["ну как когда", "ага да", "😂😂😂", "новая хорошая тема"])), [])

    def test_source_noun_heading_uses_nominative_case(self):
        topics = build_topics(messages(["Сравнили два дайджеста ChatGPT для рабочих задач."]))
        self.assertTrue(topics[0]["title"].startswith("Дайджест ChatGPT"))

    def test_filler_reply_stays_in_thread_but_unrelated_long_reply_can_change_topic(self):
        items = messages(["Установка драйверов NVIDIA на Fedora.", "ага да",
                          "Забронировали столик ресторана рядом с парком на субботний вечер."])
        items[1]["reply_to_message_id"] = 1
        items[2]["reply_to_message_id"] = 1
        topics = build_topics(items)
        self.assertEqual(sorted(topic["count"] for topic in topics), [1, 2])

    def test_cafe_conversation_is_not_split_into_identical_topics(self):
        topics = build_topics(messages(["Бронь столика в кафе на субботу?",
                                        "Кафе возле парка подходит, забронируем столик к четырём."]))
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["count"], 2)

    def test_actions_and_nouns_can_describe_the_same_topic(self):
        topics = build_topics(messages(["Бронирование столика в ресторане.",
                                        "Забронируем столик на субботний вечер."]))
        self.assertEqual(len(topics), 1)

    def test_shared_generic_noun_does_not_merge_unrelated_discussions(self):
        topics = build_topics(messages(["Проект ремонта ванной: плитка, сантехника, трубы.",
                                        "Проект космической ракеты: двигатель, орбита, топливо."]))
        self.assertEqual(len(topics), 2)

    def test_generic_adjective_does_not_merge_unrelated_short_messages(self):
        topics = build_topics(messages(["Новая видеокарта NVIDIA", "Новая квартира возле парка"]))
        self.assertEqual(len(topics), 2)

    def test_topics_include_messages_older_than_the_last_350(self):
        items = messages(["Обновление драйвера видеокарты NVIDIA."] * 80
                         + ["Бронирование столика в ресторане на субботу."] * 350)
        topics = build_topics(items)
        self.assertEqual(sum(topic["count"] for topic in topics), 430)
        self.assertEqual(sorted(topic["count"] for topic in topics), [80, 350])

    def test_summaries_are_source_grounded_and_titles_do_not_join_across_stopwords(self):
        items = messages(["Видеокарта работает, а ужин приготовили.",
                          "Видеокарта перегревается, поэтому вентилятор сильно шумит."])
        for topic in build_topics(items):
            for highlight in topic["highlights"]:
                source = next(item["text"] for item in items if item["message_id"] == highlight["message_id"])
                self.assertIn(highlight["text"], source)
            self.assertNotIn("работает ужин", topic["title"])

    def test_order_is_stable_for_unsorted_input(self):
        items = messages(["Бронирование столика в ресторане.", "Новый драйвер NVIDIA.",
                          "Драйвер NVIDIA исправил сон компьютера."])
        self.assertEqual(build_topics(items), build_topics(list(reversed(items))))

    def test_distant_unthreaded_messages_are_separate(self):
        items = messages(["Драйвер видеокарты NVIDIA.", "Драйвер видеокарты NVIDIA."])
        items[1]["date"] = items[0]["date"] + 7 * 3600
        self.assertEqual(len(build_topics(items)), 2)
        items[1]["reply_to_message_id"] = 1
        self.assertEqual(len(build_topics(items)), 1)

    def test_overflow_keeps_counts_without_inventing_one_common_topic(self):
        bodies = ["Бронирование ресторана", "Ремонт сантехники", "Драйвер видеокарты",
                  "Полёт самолёта", "Древнеримская история", "Зимняя рыбалка", "Оперный театр",
                  "Алгоритм сортировки", "Пересадка цветов", "Горный велосипед", "Кофейные зёрна"]
        topics = build_topics(messages(bodies))
        self.assertEqual(len(topics), 10)
        self.assertTrue(topics[-1]["is_other"])
        self.assertEqual(sum(topic["count"] for topic in topics), len(bodies))


class ScoreTests(unittest.TestCase):
    def test_markers_without_content_and_substrings_are_not_arguments(self):
        self.assertEqual(reasoning_signals("потому что потому что например если"), set())
        self.assertEqual(reasoning_signals("Слово незначительный означает маленький размер объекта."), set())
        self.assertEqual(reasoning_signals("Если погода хорошая"), set())
        self.assertIn("reasons", reasoning_signals(
            "Ставлю новый драйвер, потому что старая версия ломает сон видеокарты."))
        self.assertIn("conditions", reasoning_signals(
            "Если видеокарта перегревается, сначала проверяем вентилятор и охлаждение корпуса."))

    def test_score_exposes_components_evidence_and_sample_confidence(self):
        items = messages(["Новая видеокарта поддерживает аппаратное декодирование видео.",
                          "Например, декодирование снижает нагрузку процессора при просмотре трансляций."])
        score = estimate_participant_scores(items)[0]
        self.assertEqual(set(score["components"]), {"vocabulary", "depth", "reasoning", "dialogue", "clarity"})
        self.assertTrue(all(0 <= number <= 100 for number in score["components"].values()))
        self.assertEqual(score["confidence"], "низкая")
        self.assertEqual(score["evidence"]["examples"], 1)
        self.assertTrue(70 <= score["score"] <= 130)

    def test_duplicate_flood_does_not_improve_score_or_confidence(self):
        original = messages(["Видеокарта NVIDIA поддерживает аппаратное декодирование видео.",
                             "Проверяем температуру процессора, охлаждение корпуса и обороты вентиляторов."])
        flooded = messages([item["text"] for item in original] * 12)
        base, flood = estimate_participant_scores(original)[0], estimate_participant_scores(flooded)[0]
        self.assertLess(flood["score"], base["score"])
        self.assertEqual(base["confidence_value"], flood["confidence_value"])
        self.assertEqual(flood["unique_messages"], 2)

    def test_reordering_words_and_changing_numbers_still_counts_as_a_repeat(self):
        items = messages(["Видеокарта NVIDIA драйвер обновление монитор Fedora 1",
                          "Fedora монитор обновление драйвер NVIDIA видеокарта 2"])
        score = estimate_participant_scores(items)[0]
        self.assertEqual(score["unique_messages"], 1)
        self.assertEqual(score["duplicate_ratio"], 0.5)

    def test_repeating_words_inside_message_does_not_raise_depth(self):
        items = messages(["видеокарта " * 100, "видеокарта " * 101])
        score = estimate_participant_scores(items)[0]
        self.assertLess(score["components"]["depth"], 10)
        self.assertLess(score["score"], 100)

    def test_direct_reply_after_half_an_hour_counts_but_self_reply_does_not(self):
        items = messages(["Как организовать встречу, забронировать кафе и выбрать время?",
                          "Ресторан около парка принимает бронирование столиков на вечер.",
                          "Тихая музыка, большие окна и просторный зал подходят нашей компании."])
        items[0]["sender_id"] = "2"
        items[1]["date"] = items[0]["date"] + 3600
        items[2]["date"] = items[1]["date"] + 60
        items[1]["reply_to_message_id"] = 1
        items[2]["reply_to_message_id"] = 2
        score = estimate_participant_scores(items)[0]
        self.assertEqual(score["dialogue"], 1)

    def test_too_little_text_is_not_scored(self):
        self.assertEqual(estimate_participant_scores(messages(["Привет", "да", "ок"])), [])

    def test_more_independent_samples_increase_confidence(self):
        bodies = ["Драйвер видеокарты исправляет сон монитора после установки обновления.",
                  "Кафе принимает бронирование столиков на субботний вечер.",
                  "Процессор перегревается: проверяем вентилятор, радиатор и охлаждение корпуса.",
                  "Ракета вышла на орбиту благодаря дополнительному запасу топлива.",
                  "Шахматный турнир завершился победой юного участника городской команды.",
                  "Зимняя рыбалка требует тёплой одежды, термоса и надёжного оборудования.",
                  "Фотографии заката получились яркими благодаря удачной экспозиции камеры.",
                  "Сортировка массива выполняется алгоритмом слияния с линейной дополнительной памятью.",
                  "Древнеримская архитектура повлияла на строительство европейских соборов.",
                  "Велосипед нуждается в смазке цепи и проверке тормозных колодок.",
                  "Оперный театр приглашает зрителей на премьеру музыкального спектакля.",
                  "Пересадка комнатных растений требует свежего грунта и дренажа.",
                  "Температура выпечки пирога зависит от теста, начинки и размера формы.",
                  "Отдельная ветка проекта позволяет проверить изменение перед публикацией релиза.",
                  "Астрономы измерили скорость вращения далёкой звезды с помощью спектроскопии.",
                  "Облачное хранилище поддерживает восстановление удалённых документов из резервной копии."]
        small = estimate_participant_scores(messages(bodies[:2]))[0]
        large = estimate_participant_scores(messages(bodies))[0]
        self.assertGreater(large["confidence_value"], small["confidence_value"])
        self.assertNotEqual(large["confidence"], "низкая")


class ReportTests(unittest.TestCase):
    def test_topics_and_score_breakdown_are_visible_with_escaped_source_links(self):
        items = messages(["Обновление драйверов NVIDIA исправило <сон> & работу монитора.",
                          "Например, монитор просыпается после установки нового драйвера NVIDIA."])
        stats = analyze(items, NOW)
        report = format_text_report(stats, -100123, None)
        self.assertIn("&lt;сон&gt; &amp;", report)
        self.assertIn("Ключевые слова:", report)
        self.assertIn("Словарь", report)
        self.assertIn("данных: низкая", report)
        self.assertIn('href="https://t.me/c/123/', report)

    def test_long_report_splits_into_independently_valid_telegram_html(self):
        stats = analyze(messages(["Обновление драйверов NVIDIA исправило сон монитора."] * 2), NOW)
        topic = stats["topics"][0]
        stats["topics"] = [{**topic, "title": "Обновление <драйвера> & монитор 🖥 " * 4,
                            "highlights": [{"message_id": 1, "text": "<&> 🖥 " * 100}] * 2}
                           for _ in range(9)]
        report = format_text_report(stats, -100123, None)
        parts = split_text_report(report)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part.encode("utf-16-le")) // 2, 3900)
            parser = BalancedHTML()
            parser.feed(part)
            self.assertEqual(parser.stack, [])

    def test_empty_and_long_topic_dashboards_render(self):
        for items in ([], messages(["Обновление драйвера NVIDIA и проблемы сна монитора. " * 30])):
            stats = analyze(items, NOW)
            with Image.open(BytesIO(render_dashboard(stats))) as image:
                self.assertEqual(image.width, 1400)
                self.assertLess(image.width + image.height, 10_000)

    def test_full_report_delivery_uses_all_text_chunks(self):
        class API:
            def __init__(self):
                self.parts = []
                self.photos = 0

            def call(self, method, payload):
                if method == "sendMessage":
                    self.parts.append(payload["text"])

            def download_photo(self, _):
                return None

            def send_photo(self, *args):
                self.photos += 1

        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "test.sqlite3")
            api = API()
            long_report = "\n".join(f"<b>Тема {index}</b>: " + "подробности " * 20 for index in range(60))
            with patch("bot.format_text_report", return_value=long_report), patch("bot.time.sleep"):
                send_dashboard(api, store, -100123, NOW, "UTC")
            self.assertEqual(api.photos, 1)
            self.assertEqual(api.parts, split_text_report(long_report))
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
