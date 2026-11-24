"""aiogram 3 bot and same-origin Telegram Mini App server."""
import asyncio
from datetime import date
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, PreCheckoutQuery, WebAppInfo)

from billing import Billing
from interview import next_question, store_markup
from model import Api
from planner import report
from service import MealService
from storage import Sessions, chunks
from webapp import create_webapp

LOG = logging.getLogger(__name__)


def create_router(bot, service, url, support):
    router = Router()

    def launch():
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть «Вкусно дома»", web_app=WebAppInfo(url=url))
        ]])

    async def reply_state(message, record):
        key, prompt = service.question(record)
        if key or record.get("clarification"):
            markup = InlineKeyboardMarkup.model_validate(store_markup([])) if key == "stores" else None
            await message.answer(prompt, reply_markup=markup)
        elif not service.billing.allowed(message.from_user.id, record["answers"]):
            await message.answer("Для одного человека меню бесплатно. Для нескольких — семейный доступ "
                                 "299 ₽ в месяц. Оплата в Telegram Stars, точная сумма видна в приложении.",
                                 reply_markup=launch())
        else:
            await message.answer("Составляю меню. Это может занять несколько минут.")
            result = await service.generate(message.from_user.id)
            for section in report(result["plan"], result["answers"], date.fromisoformat(result["plan_date"])):
                for part in chunks(section):
                    await message.answer(part)
            await message.answer("Меню и покупки также доступны в приложении.", reply_markup=launch())

    @router.message(Command("start", "app"))
    async def start(message: Message):
        if message.chat.type == "private":
            await message.answer(
                "Вкусно дома\nМеню, которое собирает семью за столом.\n\n"
                "Откройте приложение или начните анкету в чате командой /menu. "
                "Для одного человека бесплатно, семейный доступ — 299 ₽ в месяц.",
                reply_markup=launch())

    @router.message(Command("help", "paysupport"))
    async def help_message(message: Message):
        await message.answer("/menu — анкета и меню\n/app — приложение\n/new — новая анкета\n"
                             "/delete — удалить анкету и меню\n"
                             "Управление автопродлением — в разделе «Семья» приложения.\n"
                             f"По вопросам оплаты и возврата — {support}")

    @router.message(Command("new", "delete"))
    async def reset(message: Message):
        if message.chat.type != "private":
            return
        delete = message.text.split("@")[0] == "/delete"
        await service.reset(message.from_user.id, delete=delete)
        await message.answer("Анкета и меню удалены. Подпиской можно управлять в приложении."
                             if delete else "Начнём новую анкету.", reply_markup=launch())

    @router.pre_checkout_query()
    async def checkout(query: PreCheckoutQuery):
        valid = service.billing.valid(query.from_user.id, query.invoice_payload, query.currency, query.total_amount)
        await query.answer(ok=valid, error_message=None if valid else "Счёт истёк. Откройте новый в приложении.")

    @router.message(F.successful_payment)
    async def payment(message: Message):
        service.billing.payment(message.from_user.id, message.successful_payment)
        await message.answer("Семейный доступ активирован. Можно составлять меню для всей семьи.",
                             reply_markup=launch())

    @router.message(F.refunded_payment)
    async def refund(message: Message):
        service.billing.refund(message.from_user.id, message.refunded_payment.telegram_payment_charge_id)

    @router.callback_query(F.data.startswith("shop:"))
    async def shops(query):
        user = query.from_user.id
        async with service.lock(user):
            record = service.sessions.load(user)
            if next_question(record["answers"]) != "stores":
                await query.answer("Выбор уже завершён.")
                return
            selected = set(record.get("shops_pending", []))
            value = query.data[5:]
            if value == "done":
                if not selected:
                    await query.answer("Выберите магазин.")
                    return
                record["answers"]["stores"] = sorted(selected)
                record["dirty"] = True
                record.pop("shops_pending", None)
            elif value == "all":
                selected.update(range(1, 9))
                record["shops_pending"] = sorted(selected)
            elif value.isdigit() and 1 <= int(value) <= 9:
                selected ^= {int(value)}
                record["shops_pending"] = sorted(selected)
            service.sessions.save(user, record)
        await query.answer()
        await query.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup.model_validate(store_markup(selected)) if value != "done" else None)
        if value == "done":
            await reply_state(query.message.model_copy(update={"from_user": query.from_user}), record)

    @router.message(F.text)
    async def text(message: Message):
        if message.chat.type != "private":
            return
        try:
            if message.text.split("@")[0] == "/menu":
                record = service.sessions.load(message.from_user.id)
            elif message.text.startswith("/"):
                await message.answer("Команды доступны через /help.")
                return
            else:
                if len(message.text) > 3500:
                    await message.answer("Отправьте ответ до 3500 символов.")
                    return
                record = await service.answer(message.from_user.id, message.text)
            await reply_state(message, record)
        except Exception as error:
            LOG.warning("Chat request failed: %s", type(error).__name__)
            await message.answer("Не удалось обработать запрос. Попробуйте ещё раз или откройте приложение.",
                                 reply_markup=launch())
    return router


async def main():
    logging.basicConfig(level=logging.INFO)
    os.umask(0o077)
    token, key, url = (os.getenv(name, "") for name in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "WEBAPP_URL"))
    if not token or not key or urlparse(url).scheme != "https":
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN, OPENAI_API_KEY и публичный HTTPS WEBAPP_URL.")
    legacy = "family-menu.sqlite3" if Path("family-menu.sqlite3").exists() else "data/family-menu.sqlite3"
    path = Path(os.getenv("BOT_DB", legacy))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stars = int(os.getenv("FAMILY_PRICE_STARS", "0"))
    except ValueError:
        raise SystemExit("FAMILY_PRICE_STARS должен быть целым числом.") from None
    billing = Billing(path, stars)
    service = MealService(Sessions(path), Api(key), billing, os.getenv("BOT_TZ", "Europe/Moscow"))
    bot = Bot(token)
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(bot, service, url, os.getenv("SUPPORT_CONTACT", "@dwaynewow")))
    runner = web.AppRunner(create_webapp(bot, service, url))
    try:
        if (await bot.get_webhook_info()).url:
            raise SystemExit("Для polling отключите существующий webhook или используйте отдельного бота.")
        await bot.set_my_commands([BotCommand(command=name, description=description) for name, description in
                                   (("app", "Открыть приложение"), ("menu", "Анкета и меню"),
                                    ("help", "Помощь"), ("paysupport", "Вопросы оплаты"))])
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Вкусно дома", web_app=WebAppInfo(url=url)))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8090"))).start()
        await dispatcher.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
