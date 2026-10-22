"""Report navigation, snapshot isolation and the Telegram upload contract."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analytics import analyze
from bot import MessageStore, TelegramAPI, TelegramAPIError, handle_update
from reports import report_keyboard, report_page, visible_length
from topics import build_topics


NOW = 2_000_000_000
CHAT = {"id": -100123, "type": "supergroup"}


def sample_messages(bodies):
    return [{"date": NOW - 500 + index * 10, "message_id": index + 1,
             "sender_id": "1", "sender_name": "Аня", "text": body}
            for index, body in enumerate(bodies)]


class API:
    def __init__(self):
        self.calls = []

    def call(self, method, payload):
        self.calls.append((method, payload))


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "test.sqlite3"
        self.store = MessageStore(self.path)
        self.stats = analyze(sample_messages([
            "Обновление драйвера NVIDIA исправило сон монитора.",
            "Драйвер NVIDIA установили после проверки версии на Fedora.",
            "Бронирование столика в кафе на субботний вечер.",
        ]), NOW)
        self.report_id = self.store.save_report(CHAT["id"], NOW, self.stats, "mygroup")
        self.api = API()

    def tearDown(self):
        self.store.conn.close()
        self.folder.cleanup()

    def click(self, data, chat=None, now=NOW):
        query = {"id": "query-1", "data": data,
                 "message": {"chat": chat or CHAT, "message_id": 55}}
        with patch("bot.time.time", return_value=now):
            handle_update({"callback_query": query}, self.api, self.store, "OurBot", "UTC")

    def test_topic_score_and_back_edit_same_caption_without_new_messages(self):
        for view, page in (("topics", 0), ("topics", 1), ("scores", 0), ("brief", 0)):
            self.click(f"report:{self.report_id}:{view}:{page}")
            ack, edit = self.api.calls[-2:]
            self.assertEqual(ack[0], "answerCallbackQuery")
            self.assertEqual(edit[0], "editMessageCaption")
            self.assertEqual(edit[1]["message_id"], 55)
            self.assertEqual(edit[1]["caption"], report_page(self.stats, CHAT["id"], "mygroup", view, page))
            self.assertLessEqual(visible_length(edit[1]["caption"]), 900)
            self.assertIn("inline_keyboard", json.loads(edit[1]["reply_markup"]))
        self.assertFalse(any(method in {"sendMessage", "sendPhoto"} for method, _ in self.api.calls))

    def test_buttons_keep_original_snapshot_after_restart_and_new_messages(self):
        original = report_page(self.stats, CHAT["id"], "mygroup", "topics", 0)
        self.store.conn.close()
        self.store = MessageStore(self.path)
        self.store.upsert({"chat": CHAT, "message_id": 88, "date": NOW,
                           "from": {"id": 2, "first_name": "Борис"}, "text": "Совершенно другая тема"})
        self.click(f"report:{self.report_id}:topics:0")
        self.assertEqual(self.api.calls[-1][1]["caption"], original)

    def test_foreign_chat_private_chat_expired_or_invalid_page_cannot_open_snapshot(self):
        cases = [
            (f"report:{self.report_id}:topics:0", {**CHAT, "id": -100999}, NOW),
            (f"report:{self.report_id}:topics:0", {"id": 1, "type": "private"}, NOW),
            (f"report:{self.report_id}:topics:0", CHAT, NOW + 86401),
            (f"report:{self.report_id}:topics:999999", CHAT, NOW),
            (f"report:{self.report_id}:brief:1", CHAT, NOW),
            (f"report:{self.report_id}:scores:-1", CHAT, NOW),
            ("report:000000000000:topics:0", CHAT, NOW),
            ("invalid-data", CHAT, NOW),
        ]
        for data, chat, now in cases:
            with self.subTest(data=data, chat=chat, now=now):
                self.api.calls.clear()
                self.click(data, chat, now)
                self.assertEqual(len(self.api.calls), 1)
                self.assertEqual(self.api.calls[0][0], "answerCallbackQuery")
                self.assertIn("text", self.api.calls[0][1])

    def test_prune_removes_snapshots_after_24_hours(self):
        self.store.prune(NOW + 86401)
        self.assertIsNone(self.store.load_report(self.report_id, CHAT["id"], NOW + 86401))
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM report_snapshots").fetchone()[0], 0)

    def test_repeated_edit_is_harmless_but_other_telegram_errors_propagate(self):
        for reason, raises in (("Bad Request: message is not modified", False), ("Forbidden", True)):
            def call(method, payload):
                if method == "editMessageCaption":
                    raise TelegramAPIError(reason)
            with patch.object(self.api, "call", side_effect=call):
                if raises:
                    with self.assertRaises(TelegramAPIError):
                        self.click(f"report:{self.report_id}:topics:0")
                else:
                    self.click(f"report:{self.report_id}:topics:0")

    def test_keyboard_bounds_and_callback_byte_limit(self):
        for view, total in (("topics", len(self.stats["topics"])),
                             ("scores", len(self.stats["participant_scores"]))):
            for page in range(total):
                rows = report_keyboard(self.stats, self.report_id, view, page)["inline_keyboard"]
                for row in rows:
                    for button in row:
                        self.assertLessEqual(len(button["callback_data"].encode()), 64)
                labels = [button["text"] for button in rows[0]]
                self.assertEqual("‹ Назад" in labels, page > 0)
                self.assertEqual("Далее ›" in labels, page + 1 < total)

    def test_photo_upload_encodes_html_and_json_keyboard(self):
        keyboard = report_keyboard(self.stats, self.report_id)
        with patch("bot.urlopen") as open_url:
            open_url.return_value.__enter__.return_value.read.return_value = b'{"ok": true, "result": {}}'
            TelegramAPI("test-token").send_photo(CHAT["id"], b"image", "<b>Сводка</b>", keyboard)
        request = open_url.call_args.args[0]
        body = request.data.decode()
        self.assertIn('name="parse_mode"\r\n\r\nHTML', body)
        self.assertIn('name="caption"\r\n\r\n<b>Сводка</b>', body)
        self.assertIn(json.dumps(keyboard, ensure_ascii=False), body)


class StructuredTopicTests(unittest.TestCase):
    def test_heading_does_not_end_in_a_dangling_adjective_or_numeral(self):
        topic = build_topics(sample_messages([
            "Да, бот сможет объединять похожие сообщения в одну тему."]))[0]
        self.assertFalse(topic["title"].endswith(("одну", "похожие", "для")))
        self.assertIn("объединять", topic["title"].lower())

    def test_questions_and_latest_explicit_outcome_have_source_links(self):
        bodies = ["Установка драйвера NVIDIA на Fedora: монитор не просыпается.",
                  "Как сохранить память видеокарты при переходе в сон?",
                  "Решили установить новый драйвер NVIDIA и проверить сон монитора.",
                  "Итог проверки: сон монитора заработал после установки драйвера NVIDIA."]
        items = sample_messages(bodies)
        for index, item in enumerate(items[1:], 1):
            item["reply_to_message_id"] = index
        topics = build_topics(items)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["count"], 4)
        points = topics[0]["points"]
        self.assertEqual({point["kind"] for point in points}, {"context", "question", "outcome"})
        self.assertEqual(topics[0]["brief"]["message_id"], 4)
        for point in points:
            self.assertIn(point["text"], bodies[point["message_id"] - 1])

    def test_hypothetical_negative_and_question_are_not_outcomes(self):
        for body in ("Если решили обновить драйвер NVIDIA, сохраните старую версию.",
                     "Мы не решили, какой драйвер NVIDIA установить на Fedora.",
                     "Договорились обновить драйвер NVIDIA?"):
            with self.subTest(body=body):
                points = build_topics(sample_messages([body]))[0]["points"]
                self.assertFalse(any(point["kind"] == "outcome" for point in points))

    def test_a_later_outcome_is_kept_even_when_it_reverses_earlier_statement(self):
        items = sample_messages(["Драйвер NVIDIA не заработал после обновления.",
                                 "Драйвер NVIDIA заработал после обновления."])
        topic = build_topics(items)[0]
        self.assertEqual(topic["brief"]["kind"], "outcome")
        self.assertEqual(topic["brief"]["message_id"], 2)
        self.assertEqual({point["kind"] for point in topic["points"]}, {"context", "outcome"})

    def test_repeated_paste_cannot_hide_a_real_discussion(self):
        items = sample_messages(["Банановый десерт с шоколадным сиропом."] * 30 + [
            "Ремонт велосипеда: замена цепи и тормозных колодок.",
            "Ремонт велосипеда завершили, цепь и тормозные колодки заменены."])
        topics = build_topics(items)
        self.assertEqual(topics[0]["count"], 2)
        self.assertEqual(topics[1]["count"], 30)


if __name__ == "__main__":
    unittest.main()
