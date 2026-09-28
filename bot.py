"""Telegram group dashboard bot. Run with BOT_TOKEN in the environment."""

from __future__ import annotations

import json
from html import escape
import logging
import os
import re
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
from zoneinfo import ZoneInfo

from analytics import WINDOW_SECONDS, analyze, message_count_label
from render import render_dashboard


LOG = logging.getLogger("chat_dashboard")
HELP = (
    "📊 /dashboard — картинка и текстовая сводка за последние 24 часа со ссылками на реплики.\n"
    "📷 Фото выбираются по сумме реакций и ответов; для учёта реакций бот должен быть администратором.\n"
    "🕘 Ежедневный отчёт приходит автоматически в настроенное время.\n"
    "Бот учитывает сообщения, полученные после добавления в группу. "
    "Для полного обзора сделайте его администратором либо отключите режим приватности "
    "в @BotFather и добавьте в группу заново. Данные старше 24 часов удаляются."
)

# Telegram also delivers membership changes and other service events as Message
# objects. They are not chat content and must not inflate activity statistics.
CONTENT_KEYS = {
    "text", "photo", "video", "animation", "document", "audio", "voice",
    "video_note", "sticker", "contact", "location", "venue", "poll",
    "dice", "game", "invoice", "paid_media", "story",
}


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
                reply_to_message_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS messages_date_idx ON messages(date)")
        message_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(messages)")}
        if "reply_to_message_id" not in message_columns:
            self.conn.execute("ALTER TABLE messages ADD COLUMN reply_to_message_id INTEGER")
        self.conn.execute("""CREATE INDEX IF NOT EXISTS messages_reply_idx
                             ON messages(chat_id, reply_to_message_id)""")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_reactions (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                actor_key TEXT NOT NULL,
                reaction_count INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id, actor_key)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_reaction_totals (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                reaction_count INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                last_seen INTEGER NOT NULL,
                username TEXT,
                chat_type TEXT
            )
        """)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(chats)")}
        if "username" not in columns:
            self.conn.execute("ALTER TABLE chats ADD COLUMN username TEXT")
        if "chat_type" not in columns:
            self.conn.execute("ALTER TABLE chats ADD COLUMN chat_type TEXT")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                chat_id INTEGER NOT NULL,
                local_day TEXT NOT NULL,
                sent_at INTEGER NOT NULL,
                PRIMARY KEY (chat_id, local_day)
            )
        """)
        self.conn.execute("""
            INSERT OR IGNORE INTO chats (chat_id, last_seen)
            SELECT chat_id, MAX(date) FROM messages GROUP BY chat_id
        """)
        # Earlier releases stored Telegram service events as empty messages.
        # Clean those legacy rows once; new content-only ingestion follows below.
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            self.conn.execute("DELETE FROM messages WHERE text = '' AND photo_file_id IS NULL")
            self.conn.execute("PRAGMA user_version = 1")
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
        reply_to_message_id = (msg.get("reply_to_message") or {}).get("message_id")
        self.conn.execute("""
            INSERT OR REPLACE INTO messages
                (chat_id, message_id, date, sender_id, sender_name, text,
                 photo_file_id, reply_to_message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (chat["id"], msg["message_id"], msg["date"], sender_id,
              sender_name, body, photo.get("file_id") if photo else None,
              reply_to_message_id))
        self.conn.commit()

    def set_actor_reactions(self, chat_id: int, message_id: int, actor_key: str,
                            count: int) -> None:
        if not self._has_photo(chat_id, message_id):
            return
        if count:
            self.conn.execute("""
                INSERT INTO photo_reactions VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id, actor_key)
                DO UPDATE SET reaction_count = excluded.reaction_count
            """, (chat_id, message_id, actor_key, count))
        else:
            self.conn.execute("""
                DELETE FROM photo_reactions
                WHERE chat_id = ? AND message_id = ? AND actor_key = ?
            """, (chat_id, message_id, actor_key))
        self.conn.commit()

    def set_anonymous_reactions(self, chat_id: int, message_id: int, count: int) -> None:
        if not self._has_photo(chat_id, message_id):
            return
        self.conn.execute("""
            INSERT INTO photo_reaction_totals VALUES (?, ?, ?)
            ON CONFLICT(chat_id, message_id)
            DO UPDATE SET reaction_count = excluded.reaction_count
        """, (chat_id, message_id, count))
        self.conn.commit()

    def _has_photo(self, chat_id: int, message_id: int) -> bool:
        return self.conn.execute("""
            SELECT 1 FROM messages WHERE chat_id = ? AND message_id = ?
            AND photo_file_id IS NOT NULL
        """, (chat_id, message_id)).fetchone() is not None

    def prune(self, now: int) -> None:
        self.conn.execute("DELETE FROM messages WHERE date < ?", (now - WINDOW_SECONDS,))
        for table in ("photo_reactions", "photo_reaction_totals"):
            self.conn.execute(f"""
                DELETE FROM {table} WHERE NOT EXISTS (
                    SELECT 1 FROM messages
                    WHERE messages.chat_id = {table}.chat_id
                    AND messages.message_id = {table}.message_id
                )
            """)
        self.conn.execute("DELETE FROM daily_reports WHERE sent_at < ?", (now - 8 * WINDOW_SECONDS,))
        self.conn.commit()

    def register_chat(self, chat_id: int, now: int, username: str | None = None,
                      chat_type: str | None = None) -> None:
        self.conn.execute("""
            INSERT INTO chats (chat_id, last_seen, username, chat_type) VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET last_seen = excluded.last_seen,
                username = CASE WHEN excluded.chat_type IS NOT NULL
                                THEN excluded.username ELSE chats.username END,
                chat_type = COALESCE(excluded.chat_type, chats.chat_type)
        """, (chat_id, now, username, chat_type))
        self.conn.commit()

    def chat_info(self, chat_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT username, chat_type FROM chats WHERE chat_id = ?",
                                (chat_id,)).fetchone()
        return dict(row) if row else {}

    def chat_ids(self) -> list[int]:
        return [row[0] for row in self.conn.execute("SELECT chat_id FROM chats")]

    def daily_sent(self, chat_id: int, local_day: str) -> bool:
        return self.conn.execute("""
            SELECT 1 FROM daily_reports WHERE chat_id = ? AND local_day = ?
        """, (chat_id, local_day)).fetchone() is not None

    def mark_daily_sent(self, chat_id: int, local_day: str, now: int) -> None:
        self.conn.execute("""
            INSERT OR IGNORE INTO daily_reports (chat_id, local_day, sent_at) VALUES (?, ?, ?)
        """, (chat_id, local_day, now))
        self.conn.commit()

    def recent(self, chat_id: int, now: int) -> list[dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT * FROM messages WHERE chat_id = ? AND date BETWEEN ? AND ?
            ORDER BY date, message_id
        """, (chat_id, now - WINDOW_SECONDS, now)).fetchall()
        messages = [dict(row) for row in rows]
        replies = dict(self.conn.execute("""
            SELECT reply_to_message_id, COUNT(*) FROM messages
            WHERE chat_id = ? AND date BETWEEN ? AND ?
            AND reply_to_message_id IS NOT NULL GROUP BY reply_to_message_id
        """, (chat_id, now - WINDOW_SECONDS, now)))
        individual = dict(self.conn.execute("""
            SELECT message_id, SUM(reaction_count) FROM photo_reactions
            WHERE chat_id = ? GROUP BY message_id
        """, (chat_id,)))
        anonymous = dict(self.conn.execute("""
            SELECT message_id, reaction_count FROM photo_reaction_totals WHERE chat_id = ?
        """, (chat_id,)))
        for msg in messages:
            if msg["photo_file_id"]:
                msg["reply_count"] = replies.get(msg["message_id"], 0)
                msg["reaction_count"] = max(individual.get(msg["message_id"], 0),
                                            anonymous.get(msg["message_id"], 0))
        return messages


def parse_command(text: str, bot_username: str) -> str | None:
    first = text.split(maxsplit=1)[0] if text else ""
    if not first.startswith("/"):
        return None
    command, _, mention = first[1:].partition("@")
    if mention and mention.casefold() != bot_username.casefold():
        return None
    return command.casefold()


def parse_report_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if not match:
        raise ValueError("AUTO_REPORT_TIME must have HH:MM format, e.g. 09:00")
    return int(match.group(1)), int(match.group(2))


def message_link(chat_id: int, username: str | None, message_id: int) -> str | None:
    if message_id <= 0:
        return None
    if username and re.fullmatch(r"[A-Za-z0-9_]+", username):
        return f"https://t.me/{username}/{message_id}"
    # Telegram's /c/ links address private supergroups by ID without the -100 prefix.
    if str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
    return None


def format_text_report(stats: dict[str, Any], chat_id: int,
                       username: str | None) -> str:
    lines = ["<b>📅 Что обсуждали за последние 24 часа</b>",
             f"{stats['start']}–{stats['end']} · сообщений: {stats['messages']}"
             f" · фото: {stats.get('photo_count', len(stats['photos']))}", ""]
    if not stats["topics"]:
        lines.append("Пока нет текстовых сообщений для обзора.")
    for index, topic in enumerate(stats["topics"], 1):
        ids = topic.get("message_ids", [])
        link = message_link(chat_id, username, ids[0]) if ids else None
        title = escape(topic["title"])
        heading = f'<a href="{link}"><b>{title}</b></a>' if link else f"<b>{title}</b>"
        lines.append(f"{index}. {heading} ({message_count_label(topic['count'])})")
    if stats["topics"]:
        lines.extend(["", "<b>Подробнее о главных темах</b>"])
    for topic in stats["topics"][:3]:
        ids = topic.get("message_ids", [])[:3]
        lines.append(f"<b>{escape(topic['title'])}</b>")
        lines.append(escape(topic["summary"]))
        source_links = [f'<a href="{url}">↗ реплика {number}</a>'
                        for number, message_id in enumerate(ids, 1)
                        if (url := message_link(chat_id, username, message_id))]
        if source_links:
            lines.append(" · ".join(source_links))
        lines.append("")
    photo_links = [f'<a href="{url}">фото {number}</a>'
                   f" (♥ {photo.get('reaction_count', 0)}, ответов {photo.get('reply_count', 0)})"
                   for number, photo in enumerate(stats["photos"][:3], 1)
                   if (url := message_link(chat_id, username, photo["message_id"]))]
    if photo_links:
        lines.append("📷 " + " · ".join(photo_links))
    authors = stats.get("authors", [])[:3]
    if authors:
        lines.append("<b>Активные:</b> " + ", ".join(
            f"{escape(item['name'])} — {item['count']}" for item in authors))
    words = stats.get("words", [])[:8]
    if words:
        lines.append("<b>Частые слова:</b> " + ", ".join(
            f"{escape(word)} ({count})" for word, count in words))
    if stats.get("average_score") is not None:
        lines.append(f"<b>Игровой балл текста:</b> {stats['average_score']}")
    if username is None and not str(chat_id).startswith("-100") and stats["topics"]:
        lines.append("Ссылки на реплики появятся после перехода группы в супергруппу.")
    return "\n".join(lines).strip()


def send_dashboard(api: TelegramAPI, store: MessageStore, chat_id: int,
                   now: int, tz_name: str, daily: bool = False) -> None:
    api.call("sendChatAction", {"chat_id": chat_id, "action": "upload_photo"})
    stats = analyze(store.recent(chat_id, now), now, tz_name)
    photos = [api.download_photo(item["photo_file_id"]) for item in stats["photos"]]
    png = render_dashboard(stats, photos)
    caption = "📊 Ежедневная сводка за 24 часа" if daily else "📊 Сводка за последние 24 часа"
    api.send_photo(chat_id, png, caption)
    chat_info = store.chat_info(chat_id)
    try:
        api.call("sendMessage", {"chat_id": chat_id,
                                 "text": format_text_report(stats, chat_id, chat_info.get("username")),
                                 "parse_mode": "HTML",
                                 "link_preview_options": json.dumps({"is_disabled": True})})
    except Exception:
        # The image is already delivered. Avoid duplicating it on the next daily retry.
        LOG.exception("Could not send text report to chat %s", chat_id)
    LOG.info("Sent %s dashboard to chat %s: %s messages",
             "daily" if daily else "manual", chat_id, stats["messages"])


def send_due_daily_reports(api: TelegramAPI, store: MessageStore, now: int,
                           tz_name: str, report_time: tuple[int, int]) -> None:
    try:
        timezone = ZoneInfo(tz_name)
    except (KeyError, ValueError):
        timezone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(now, timezone)
    if (local.hour, local.minute) < report_time:
        return
    day = local.date().isoformat()
    for chat_id in store.chat_ids():
        if store.daily_sent(chat_id, day):
            continue
        try:
            send_dashboard(api, store, chat_id, now, tz_name, daily=True)
            store.mark_daily_sent(chat_id, day, now)
        except Exception:
            LOG.exception("Could not send daily dashboard to chat %s", chat_id)


def handle_update(update: dict[str, Any], api: TelegramAPI, store: MessageStore,
                  bot_username: str, tz_name: str) -> None:
    reaction = update.get("message_reaction")
    if reaction:
        chat = reaction.get("chat") or {}
        if chat.get("type") in {"group", "supergroup"}:
            actor = reaction.get("user") or {}
            actor_chat = reaction.get("actor_chat") or {}
            actor_key = (f"user:{actor['id']}" if actor.get("id") is not None else
                         f"chat:{actor_chat['id']}" if actor_chat.get("id") is not None else None)
            if actor_key:
                store.set_actor_reactions(chat["id"], reaction["message_id"], actor_key,
                                          len(reaction.get("new_reaction") or []))
        return
    reaction_totals = update.get("message_reaction_count")
    if reaction_totals:
        chat = reaction_totals.get("chat") or {}
        if chat.get("type") in {"group", "supergroup"}:
            count = sum(max(0, int(item.get("total_count", 0)))
                        for item in reaction_totals.get("reactions") or [])
            store.set_anonymous_reactions(chat["id"], reaction_totals["message_id"], count)
        return
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
    store.register_chat(chat["id"], now, chat.get("username"), chat.get("type"))
    command = parse_command(msg.get("text") or "", bot_username)
    if command in {"dashboard", "report"}:
        send_dashboard(api, store, chat["id"], now, tz_name)
    elif command in {"start", "help"}:
        api.call("sendMessage", {"chat_id": chat["id"], "text": HELP})
    elif any(key in msg for key in CONTENT_KEYS):
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
    report_time = parse_report_time(os.environ.get("AUTO_REPORT_TIME", "09:00"))
    store = MessageStore(db_path)
    offset: int | None = None
    LOG.info("Started @%s; timezone=%s; daily=%02d:%02d; database=%s",
             username, tz_name, *report_time, db_path)
    while True:
        try:
            payload: dict[str, Any] = {
                "timeout": 30,
                "allowed_updates": json.dumps([
                    "message", "edited_message", "message_reaction", "message_reaction_count"
                ]),
            }
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
            send_due_daily_reports(api, store, int(time.time()), tz_name, report_time)
        except (TelegramAPIError, URLError, TimeoutError, OSError):
            LOG.exception("Telegram connection failed; retrying in 5 seconds")
            time.sleep(5)
        except KeyboardInterrupt:
            LOG.info("Stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
