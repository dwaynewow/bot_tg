from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from interview import QUESTIONS, apply_answers, local_answer, next_question, store_markup
from model import Api, extract, make_plan
from planner import report

LOG = logging.getLogger("family_bot")


class TelegramError(RuntimeError):
    def __init__(self, code):
        super().__init__(f"Telegram HTTP {code}")
        self.code = code


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, **data):
        request = Request(self.base + method, json.dumps(data).encode(),
                          {"Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=45) as response:
                    result = json.load(response)
                if not result.get("ok"):
                    raise TelegramError(result.get("error_code", 500))
                return result["result"]
            except HTTPError as error:
                if error.code == 429 and attempt < 2:
                    retry = json.load(error).get("parameters", {}).get("retry_after", 1)
                    time.sleep(min(60, max(1, retry)))
                else:
                    raise TelegramError(error.code) from None

    def send(self, chat, text, **options):
        return self.call("sendMessage", chat_id=chat, text=text, **options)


class Sessions:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (chat INTEGER PRIMARY KEY, data TEXT NOT NULL)")

    def load(self, chat):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT data FROM sessions WHERE chat=?", (chat,)).fetchone()
        return json.loads(row[0]) if row else {"answers": {}}

    def save(self, chat, record):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO sessions VALUES(?, ?) ON CONFLICT(chat) DO UPDATE SET data=excluded.data",
                       (chat, json.dumps(record, ensure_ascii=False)))

    def delete(self, chat):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM sessions WHERE chat=?", (chat,))


def chunks(text, limit=3900):
    # Telegram counts UTF-16 units. Splitting by Unicode characters preserves emojis.
    pending = []
    size = 0
    for line in text.split('\n'):
        while len(line.encode("utf-16-le")) // 2 > limit - 1:
            end = (limit - 1) // 2
            if pending:
                yield "\n".join(pending)
            pending, size = [], 0
            yield line[:end]
            line = line[end:]
        length = len(line.encode("utf-16-le")) // 2 + 1
        if pending and size + length > limit:
            yield "\n".join(pending)
            pending, size = [], 0
        pending.append(line)
        size += length
    if pending:
        yield "\n".join(pending)


class Bot:
    def __init__(self, telegram, model, sessions, timezone):
        self.telegram = telegram
        self.model = model
        self.sessions = sessions
        self.timezone = timezone

    def ask(self, chat, record, hint=""):
        question = next_question(record["answers"])
        record.pop("clarification", None)
        if question == "stores":
            selected = record.get("shops_pending", [])
            msg = self.telegram.send(chat, QUESTIONS[question], reply_markup=store_markup(selected))
            record["shop_message"] = msg["message_id"]
        else:
            record.pop("shop_message", None)
            prompt = hint or QUESTIONS[question]
            if hint:
                record["clarification"] = hint
            self.telegram.send(chat, prompt)
        self.sessions.save(chat, record)

    def send_plan(self, chat, record):
        for section in report(record["plan"], record["answers"], date.fromisoformat(record["plan_date"])):
            for part in chunks(section):
                if part.strip():
                    self.telegram.send(chat, part)

    def finish(self, chat, record, hint=""):
        if next_question(record["answers"]):
            self.ask(chat, record, hint)
            return
        today = datetime.now(self.timezone).date()
        self.telegram.send(chat, "Составляю меню и список покупок. Это может занять несколько минут.")
        plan = make_plan(self.model, record["answers"], today, record.get("plan"),
                         record.get("revision", ""))
        record.update(plan=plan, plan_date=today.isoformat(), dirty=False)
        for key in ("revision", "clarification"):
            record.pop(key, None)
        self.sessions.save(chat, record)
        self.send_plan(chat, record)

    def shop(self, chat, record, callback):
        telegram = self.telegram
        data = callback.get("data", "")
        message_id = callback["message"]["message_id"]
        target = data.removeprefix("shop:")
        if (next_question(record["answers"]) != "stores" or message_id != record.get("shop_message")
                or not data.startswith("shop:") or target not in [*map(str, range(1, 10)), "all", "done"]):
            telegram.call("answerCallbackQuery", callback_query_id=callback["id"], text="Этот выбор уже закрыт.")
            return
        selected = set(record.get("shops_pending", []))
        if target == "done":
            if not selected:
                telegram.call("answerCallbackQuery", callback_query_id=callback["id"],
                              text="Выберите хотя бы один магазин.")
                return
            telegram.call("answerCallbackQuery", callback_query_id=callback["id"])
            record["answers"]["stores"] = sorted(selected)
            record["dirty"] = True
            record.pop("shop_message", None)
            record.pop("shops_pending", None)
            self.sessions.save(chat, record)
            try:
                telegram.call("editMessageReplyMarkup", chat_id=chat, message_id=message_id,
                              reply_markup={"inline_keyboard": []})
            except TelegramError as error:
                if error.code != 400:
                    raise
            self.finish(chat, record)
            return
        telegram.call("answerCallbackQuery", callback_query_id=callback["id"])
        updated = selected | set(range(1, 9)) if target == "all" else selected ^ {int(target)}
        if updated != selected:
            telegram.call("editMessageReplyMarkup", chat_id=chat, message_id=message_id,
                          reply_markup=store_markup(updated))
            record["shops_pending"] = sorted(updated)
            self.sessions.save(chat, record)

    def handle(self, chat, update):
        record = self.sessions.load(chat)
        if "callback_query" in update:
            self.shop(chat, record, update["callback_query"])
            return
        message = update["message"].get("text", "").strip()
        command = message.split(maxsplit=1)[0].split("@", 1)[0].lower() if message else ""
        if command == "/delete":
            self.sessions.delete(chat)
            self.telegram.send(chat, "Анкета и меню удалены из базы бота. Начать заново можно командой /start.")
            return
        if command == "/help":
            self.telegram.send(chat, "/start — продолжить или начать анкету\n/menu — показать или пересчитать меню\n"
                               "/new — заменить старую анкету новой\n/delete — удалить анкету и меню из базы\n\n"
                               "Чтобы исправить ответ или заменить блюдо, напишите об этом в чате.")
            return
        if command == "/new":
            record = {"answers": {}}
            self.sessions.save(chat, record)
        if command in {"/new", "/start", "/menu"}:
            if next_question(record["answers"]):
                if command != "/menu" and not record["answers"]:
                    self.telegram.send(chat, "Ответы сохраняются в этом боте и передаются OpenAI для подбора меню. "
                                       "Удалить их из базы бота можно командой /delete.")
                self.ask(chat, record, record.get("clarification", ""))
            elif record.get("plan") and not record.get("dirty"):
                self.send_plan(chat, record)
            else:
                self.finish(chat, record)
            return
        if not message or message.startswith("/"):
            self.telegram.send(chat, "Пришлите ответ текстом. Команды доступны через /help.")
            return
        if len(message) > 3500:
            self.telegram.send(chat, "Сократите сообщение до 3500 символов и отправьте снова.")
            return

        state = record["answers"]
        question = next_question(state)
        patch = local_answer(question, message) if not record.get("clarification") else None
        if patch is None:
            patch, revision, hint = extract(self.model, state, question, message,
                                            bool(record.get("plan")), record.get("clarification", ""))
        else:
            revision, hint = "", ""
        if patch:
            apply_answers(state, patch)
            if question == "stores" and state.get("stores"):
                record.pop("shops_pending", None)
                record.pop("shop_message", None)
        if revision:
            record["revision"] = revision
        if patch or revision:
            record["dirty"] = True
            self.sessions.save(chat, record)
        if not next_question(state) and hint:
            record["clarification"] = hint
            self.sessions.save(chat, record)
            self.telegram.send(chat, hint)
        elif patch or revision or question:
            self.finish(chat, record, hint)
        else:
            self.telegram.send(chat, "Напишите, какой ответ исправить или какое блюдо заменить. /menu покажет текущее меню.")

    def safe_handle(self, chat, update):
        try:
            self.handle(chat, update)
        except Exception as error:
            # The error class is safe to log. Network exceptions may contain credentials.
            LOG.warning("Update failed: %s", type(error).__name__)
            try:
                self.telegram.send(chat, "Не удалось обработать запрос. Если анкета заполнена, попробуйте /menu. "
                                   "Если отвечали на вопрос, отправьте ответ ещё раз.")
            except Exception:
                LOG.warning("Could not send the error message")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    key = os.getenv("OPENAI_API_KEY")
    if not token or not key:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN и OPENAI_API_KEY в окружении.")
    os.umask(0o077)
    db_path = Path(os.getenv("BOT_DB", "family-menu.sqlite3"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    telegram = Telegram(token)
    try:
        me = telegram.call("getMe")
        webhook = telegram.call("getWebhookInfo")
        if webhook.get("url"):
            raise SystemExit("У этого бота настроен webhook. Для запуска polling используйте нового бота или отключите webhook.")
        telegram.call("setMyCommands", commands=[
            {"command": command, "description": description} for command, description in
            (("start", "Начать или продолжить анкету"), ("menu", "Показать меню"), ("new", "Новая анкета"),
             ("delete", "Удалить мои ответы"), ("help", "Помощь"))
        ])
    except TelegramError as error:
        raise SystemExit(f"Не удалось запустить бот. {error}.") from None
    LOG.info("Бот @%s подключён", me["username"])
    bot = Bot(telegram, Api(key), Sessions(db_path), ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow")))
    pools = [ThreadPoolExecutor(max_workers=1) for _ in range(4)]
    offset = 0
    backoff = 1
    try:
        while True:
            try:
                updates = telegram.call("getUpdates", offset=offset, timeout=25,
                                        allowed_updates=["message", "callback_query"])
                backoff = 1
                for update in updates:
                    offset = update["update_id"] + 1
                    envelope = update.get("callback_query", update)
                    chat = envelope.get("message", {}).get("chat", {})
                    if chat.get("type") == "private":
                        pools[chat["id"] % len(pools)].submit(bot.safe_handle, chat["id"], update)
            except (OSError, TelegramError, ValueError) as error:
                if isinstance(error, TelegramError) and error.code in {401, 409}:
                    raise SystemExit(f"Polling остановлен. {error}. Проверьте токен и другие запуски этого бота.") from None
                LOG.warning("Polling failed: %s", type(error).__name__)
                time.sleep(backoff)
                backoff = min(30, backoff * 2)
    except KeyboardInterrupt:
        LOG.info("Останавливаю бота")
    finally:
        for pool in pools:
            pool.shutdown(cancel_futures=True)


if __name__ == "__main__":
    main()
