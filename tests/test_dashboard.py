import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from analytics import analyze
from bot import (MessageStore, format_text_report, handle_update, message_link,
                 parse_command, parse_report_time, send_due_daily_reports)
from render import render_dashboard


class DashboardTests(unittest.TestCase):
    def test_24_hour_window_and_counts(self):
        now = 2_000_000_000
        messages = [
            {"date": now - 86_401, "sender_id": "1", "sender_name": "Аня",
             "text": "старый старый", "message_id": 1, "photo_file_id": None},
            {"date": now - 5000, "sender_id": "1", "sender_name": "Аня",
             "text": "Встреча завтра в парке", "message_id": 2, "photo_file_id": "file"},
            {"date": now - 1000, "sender_id": "2", "sender_name": "Борис",
             "text": "Встреча завтра около парка", "message_id": 3, "photo_file_id": None},
        ]
        stats = analyze(messages, now)
        self.assertEqual(stats["messages"], 2)
        self.assertEqual(stats["participants"], 2)
        self.assertEqual(stats["photo_count"], 1)
        self.assertEqual(stats["authors"][0]["name"], "Аня")
        self.assertIn(("встреча", 2), stats["words"])
        self.assertEqual(sum(stats["hourly"]), 2)
        rendered = render_dashboard(stats)
        with Image.open(BytesIO(rendered)) as image:
            self.assertEqual(image.width, 1400)
            self.assertLess(image.height, 1840)

    def test_database_retention_and_command_routing(self):
        now = 2_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            store = MessageStore(Path(folder) / "test.sqlite3")
            msg = {"chat": {"id": -1001}, "message_id": 1, "date": now - 200,
                   "from": {"id": 5, "first_name": "Ира"}, "text": "Привет группе"}
            store.upsert(msg)
            self.assertEqual(len(store.recent(-1001, now)), 1)
            store.prune(now + 86_201)
            self.assertEqual(len(store.recent(-1001, now + 86_201)), 0)
            store.conn.close()
        self.assertEqual(parse_command("/dashboard@OurBot", "ourbot"), "dashboard")
        self.assertIsNone(parse_command("/dashboard@OtherBot", "ourbot"))

    def test_group_message_then_dashboard_command(self):
        class FakeAPI:
            def __init__(self):
                self.calls = []
                self.photos = []

            def call(self, method, payload):
                self.calls.append((method, payload))

            def download_photo(self, file_id):
                return None

            def send_photo(self, chat_id, png, caption):
                self.photos.append((chat_id, png, caption))

        with tempfile.TemporaryDirectory() as folder:
            store = MessageStore(Path(folder) / "test.sqlite3")
            api = FakeAPI()
            import time
            now = int(time.time())
            shared = {"chat": {"id": -1001, "type": "supergroup"},
                      "from": {"id": 5, "first_name": "Ира"}, "date": now}
            handle_update({"message": {**shared, "message_id": 0,
                                       "new_chat_members": [{"id": 9, "first_name": "Бот"}]}},
                          api, store, "OurBot", "UTC")
            handle_update({"message": {**shared, "message_id": 1, "text": "Планируем встречу завтра"}},
                          api, store, "OurBot", "UTC")
            handle_update({"message": {**shared, "message_id": 2, "text": "/dashboard"}},
                          api, store, "OurBot", "UTC")
            self.assertEqual(len(api.photos), 1)
            reports = [payload for method, payload in api.calls if method == "sendMessage"]
            self.assertEqual(len(reports), 1)
            self.assertIn("https://t.me/c/1/1", reports[0]["text"])
            self.assertEqual(reports[0]["parse_mode"], "HTML")
            self.assertEqual(api.photos[0][0], -1001)
            with Image.open(BytesIO(api.photos[0][1])) as image:
                self.assertEqual(image.width, 1400)
            self.assertEqual(len(store.recent(-1001, now)), 1)
            store.conn.close()

    def test_old_service_rows_removed_on_upgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            store = MessageStore(path)
            store.conn.execute("""
                INSERT INTO messages VALUES (-1001, 1, 2000000000, '1', 'Бот', '', NULL)
            """)
            store.conn.execute("PRAGMA user_version = 0")
            store.conn.commit()
            store.conn.close()
            upgraded = MessageStore(path)
            count = upgraded.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(count, 0)
            upgraded.conn.close()

    def test_chat_schema_upgrade_keeps_daily_link_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE chats (chat_id INTEGER PRIMARY KEY, last_seen INTEGER NOT NULL)")
            conn.execute("INSERT INTO chats VALUES (-100123, 1)")
            conn.commit()
            conn.close()
            store = MessageStore(path)
            store.register_chat(-100123, 2, "mygroup", "supergroup")
            self.assertEqual(store.chat_info(-100123)["username"], "mygroup")
            store.conn.close()

    def test_text_report_links_and_html_escaping(self):
        stats = {"start": "01.01 09:00", "end": "02.01 09:00", "messages": 2,
                 "topics": [{"title": "Планы <завтра>", "summary": "Аня сказала: A&B < C",
                             "count": 2, "message_ids": [12, 13]}], "photos": []}
        public = format_text_report(stats, -100123, "mygroup")
        self.assertIn('href="https://t.me/mygroup/12"', public)
        self.assertIn('href="https://t.me/mygroup/13"', public)
        self.assertIn("Планы &lt;завтра&gt;", public)
        self.assertIn("A&amp;B &lt; C", public)
        self.assertNotIn("A&B < C", public)
        private = format_text_report(stats, -100123, None)
        self.assertIn('href="https://t.me/c/123/12"', private)
        basic = format_text_report(stats, -123, None)
        self.assertNotIn("href=", basic)
        self.assertIsNone(message_link(-123, None, 12))

    def test_topics_merge_and_personal_scores(self):
        now = 2_000_000_000
        texts = [
            ("1", "Аня", "Бот добавляет план и называет темы дискуссии"),
            ("1", "Аня", "Бот сделает темы и обсудит план встречи"),
            ("2", "Борис", "Месяц будем использовать GPT для всех задач"),
            ("2", "Борис", "Использовать GPT весь месяц для разных задач"),
        ]
        messages = [
            {"date": now - (len(texts) - i) * 300, "sender_id": sender_id,
             "sender_name": name, "text": body, "message_id": i,
             "photo_file_id": None}
            for i, (sender_id, name, body) in enumerate(texts)
        ]
        stats = analyze(messages, now)
        self.assertEqual(len(stats["topics"]), 2)
        self.assertTrue(any("Бот сделает" in item["summary"] and "Бот добавляет" in item["summary"]
                            for item in stats["topics"]))
        self.assertTrue(all("«" in item["summary"] and "»" in item["summary"]
                            for item in stats["topics"]))
        self.assertEqual(len(stats["participant_scores"]), 2)
        self.assertIsNotNone(stats["average_score"])

    def test_reasoned_dialogue_scores_above_repetition(self):
        now = 2_000_000_000
        entries = [
            ("2", "Борис", "Как лучше организовать встречу и почему текущий план неудобен?"),
            ("1", "Аня", "Потому что место далеко от метро, предлагаю встретиться у парка рядом с кафе."),
            ("3", "Спам", "Наш чудесный бот сделает чудесные вещи для чата."),
            ("1", "Аня", "Например, там можно заранее выбрать время и записать пожелания каждого участника."),
            ("3", "Спам", "Наш чудесный бот сделает чудесные вещи для чата."),
            ("1", "Аня", "Если погода испортится, перенесём встречу в кафе и сообщим об этом всем заранее."),
            ("3", "Спам", "Наш чудесный бот сделает чудесные вещи для чата."),
        ]
        messages = [{"date": now - (len(entries) - i) * 180, "message_id": i,
                     "sender_id": person, "sender_name": name, "text": body,
                     "photo_file_id": None}
                    for i, (person, name, body) in enumerate(entries)]
        scores = {item["name"]: item["score"] for item in analyze(messages, now)["participant_scores"]}
        self.assertGreater(scores["Аня"], scores["Спам"])

    def test_daily_report_sent_once_per_local_day(self):
        class FakeAPI:
            def __init__(self):
                self.photos = []
                self.messages = []

            def call(self, method, payload):
                if method == "sendMessage":
                    self.messages.append(payload)
                return None

            def download_photo(self, file_id):
                return None

            def send_photo(self, chat_id, png, caption):
                self.photos.append((chat_id, caption))

        before = int(datetime(2033, 5, 18, 8, 59, tzinfo=timezone.utc).timestamp())
        now = int(datetime(2033, 5, 18, 9, 0, tzinfo=timezone.utc).timestamp())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            store = MessageStore(path)
            store.register_chat(-1001, now)
            api = FakeAPI()
            schedule = parse_report_time("09:00")
            send_due_daily_reports(api, store, before, "UTC", schedule)
            self.assertEqual(len(api.photos), 0)
            send_due_daily_reports(api, store, now, "UTC", schedule)
            send_due_daily_reports(api, store, now + 60, "UTC", schedule)
            self.assertEqual(len(api.photos), 1)
            self.assertEqual(len(api.messages), 1)
            store.conn.close()
            store = MessageStore(path)
            send_due_daily_reports(api, store, now + 120, "UTC", schedule)
            self.assertEqual(len(api.photos), 1)
            send_due_daily_reports(api, store, now + 86400, "UTC", schedule)
            self.assertEqual(len(api.photos), 2)
            self.assertEqual(len(api.messages), 2)
            self.assertIn("Ежедневная", api.photos[0][1])
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
