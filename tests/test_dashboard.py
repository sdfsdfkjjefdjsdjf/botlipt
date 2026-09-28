import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from analytics import analyze
from bot import MessageStore, handle_update, parse_command
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
            self.assertEqual(image.size, (1400, 1840))

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
            handle_update({"message": {**shared, "message_id": 1, "text": "Планируем встречу завтра"}},
                          api, store, "OurBot", "UTC")
            handle_update({"message": {**shared, "message_id": 2, "text": "/dashboard"}},
                          api, store, "OurBot", "UTC")
            self.assertEqual(len(api.photos), 1)
            self.assertEqual(api.photos[0][0], -1001)
            with Image.open(BytesIO(api.photos[0][1])) as image:
                self.assertEqual(image.size, (1400, 1840))
            self.assertEqual(len(store.recent(-1001, now)), 1)
            store.conn.close()


if __name__ == "__main__":
    unittest.main()
