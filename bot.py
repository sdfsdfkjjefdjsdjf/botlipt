"""Telegram group dashboard bot. Run with BOT_TOKEN in the environment."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from analytics import WINDOW_SECONDS, analyze
from render import render_dashboard


LOG = logging.getLogger("chat_dashboard")
HELP = (
    "📊 /dashboard — картинка со сводкой чата за последние 24 часа.\n"
    "Бот учитывает сообщения, полученные после добавления в группу. "
    "Для полного обзора сделайте его администратором либо отключите режим приватности "
    "в @BotFather и добавьте в группу заново. Данные старше 24 часов удаляются."
)


class TelegramAPIError(Exception):
    pass


class TelegramAPI:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"

    def call(self, method: str, payload: dict[str, Any] | None = None,
             timeout: int = 45) -> Any:
        body = urlencode(payload or {}).encode("utf-8")
        request = Request(self.base + method, data=body)
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            description = exc.read().decode("utf-8", errors="replace")[:500]
            raise TelegramAPIError(f"{method}: HTTP {exc.code}: {description}") from exc
        if not result.get("ok"):
            raise TelegramAPIError(f"{method}: {result.get('description', 'unknown error')}")
        return result["result"]

    def send_photo(self, chat_id: int, png: bytes, caption: str) -> None:
        boundary = "codex" + uuid.uuid4().hex
        parts = []
        for key, value in {"chat_id": str(chat_id), "caption": caption}.items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                f"{value}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
            f"filename=\"dashboard.png\"\r\nContent-Type: image/png\r\n\r\n".encode("ascii")
        )
        parts.extend((png, b"\r\n", f"--{boundary}--\r\n".encode("ascii")))
        request = Request(self.base + "sendPhoto", data=b"".join(parts), headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        })
        try:
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
        except HTTPError as exc:
            description = exc.read().decode("utf-8", errors="replace")[:500]
            raise TelegramAPIError(f"sendPhoto: HTTP {exc.code}: {description}") from exc
        if not result.get("ok"):
            raise TelegramAPIError(f"sendPhoto: {result.get('description', 'unknown error')}")

    def download_photo(self, file_id: str) -> bytes | None:
        try:
            result = self.call("getFile", {"file_id": file_id})
            path = result.get("file_path")
            if not path:
                return None
            with urlopen(self.file_base + path, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            return data if len(data) <= 8 * 1024 * 1024 else None
        except (TelegramAPIError, URLError, TimeoutError, OSError) as exc:
            LOG.warning("Could not fetch photo: %s", exc)
            return None


class MessageStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                date INTEGER NOT NULL,
                sender_id TEXT NOT NULL,
                sender_name TEXT NOT NULL,
                text TEXT NOT NULL,
                photo_file_id TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS messages_date_idx ON messages(date)")
        self.conn.commit()

    def upsert(self, msg: dict[str, Any]) -> None:
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        sender_chat = msg.get("sender_chat") or {}
        sender_id = str(sender_chat.get("id") or user.get("id") or "anonymous")
        sender_name = sender_chat.get("title") or " ".join(filter(None, (user.get("first_name"), user.get("last_name"))))
        sender_name = sender_name or user.get("username") or "Аноним"
        photos = msg.get("photo") or []
        photo = min(photos, key=lambda p: abs(max(p.get("width", 0), p.get("height", 0)) - 640)) if photos else None
        body = msg.get("text") or msg.get("caption") or ""
        self.conn.execute("""
            INSERT OR REPLACE INTO messages
                (chat_id, message_id, date, sender_id, sender_name, text, photo_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chat["id"], msg["message_id"], msg["date"], sender_id,
              sender_name, body, photo.get("file_id") if photo else None))
        self.conn.commit()

    def prune(self, now: int) -> None:
        self.conn.execute("DELETE FROM messages WHERE date < ?", (now - WINDOW_SECONDS,))
        self.conn.commit()

    def recent(self, chat_id: int, now: int) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT * FROM messages WHERE chat_id = ? AND date BETWEEN ? AND ?
            ORDER BY date, message_id
        """, (chat_id, now - WINDOW_SECONDS, now)).fetchall()
        return [dict(row) for row in rows]


def parse_command(text: str, bot_username: str) -> str | None:
    first = text.split(maxsplit=1)[0] if text else ""
    if not first.startswith("/"):
        return None
    command, _, mention = first[1:].partition("@")
    if mention and mention.casefold() != bot_username.casefold():
        return None
    return command.casefold()


def handle_update(update: dict[str, Any], api: TelegramAPI, store: MessageStore,
                  bot_username: str, tz_name: str) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat") or {}
    if chat.get("type") not in {"group", "supergroup"}:
        if parse_command(msg.get("text") or "", bot_username) in {"start", "help", "dashboard"}:
            api.call("sendMessage", {"chat_id": chat["id"], "text":
                     "Добавьте меня в группу. Там команда /dashboard покажет сводку за последние 24 часа.\n\n" + HELP})
        return

    now = int(time.time())
    store.prune(now)
    command = parse_command(msg.get("text") or "", bot_username)
    if command in {"dashboard", "report"}:
        api.call("sendChatAction", {"chat_id": chat["id"], "action": "upload_photo"})
        stats = analyze(store.recent(chat["id"], now), now, tz_name)
        photos = [api.download_photo(item["photo_file_id"]) for item in stats["photos"]]
        png = render_dashboard(stats, photos)
        api.send_photo(chat["id"], png, "📊 Сводка за последние 24 часа")
        LOG.info("Sent dashboard to chat %s: %s messages", chat["id"], stats["messages"])
    elif command in {"start", "help"}:
        api.call("sendMessage", {"chat_id": chat["id"], "text": HELP})
    else:
        store.upsert(msg)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        print("Задайте BOT_TOKEN в переменной окружения. Инструкция: README.md", file=sys.stderr)
        return 2
    api = TelegramAPI(token)
    info = api.call("getMe")
    username = info["username"]
    db_path = Path(os.environ.get("DASHBOARD_DB", "dashboard.sqlite3"))
    tz_name = os.environ.get("DASHBOARD_TZ", "UTC")
    store = MessageStore(db_path)
    offset: int | None = None
    LOG.info("Started @%s; timezone=%s; database=%s", username, tz_name, db_path)
    while True:
        try:
            payload: dict[str, Any] = {"timeout": 30, "allowed_updates": '["message","edited_message"]'}
            if offset is not None:
                payload["offset"] = offset
            updates = api.call("getUpdates", payload, timeout=45)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update, api, store, username, tz_name)
                except Exception:
                    LOG.exception("Failed to process update %s", update.get("update_id"))
            if not updates:
                store.prune(int(time.time()))
        except (TelegramAPIError, URLError, TimeoutError, OSError):
            LOG.exception("Telegram connection failed; retrying in 5 seconds")
            time.sleep(5)
        except KeyboardInterrupt:
            LOG.info("Stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
