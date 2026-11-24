import asyncio
from datetime import date, datetime, timezone
import json
import logging
from pathlib import Path

from aiohttp import web
from aiogram.utils.web_app import safe_parse_webapp_init_data

from interview import STORES, MEALS, clean_patch, next_question, period_budget
from planner import basket, names, recipe_details, report

LOG = logging.getLogger(__name__)


def create_webapp(bot, service, public_url):
    app = web.Application(client_max_size=16 * 1024)
    jobs = {}

    def user_id(request):
        value = request.headers.get("Authorization", "")
        if not value.startswith("tma "):
            raise web.HTTPUnauthorized(text="Откройте приложение через Telegram.")
        try:
            data = safe_parse_webapp_init_data(bot.token, value[4:])
            age = (datetime.now(timezone.utc) - data.auth_date).total_seconds()
            if not data.user or not 0 <= age <= 86400:
                raise ValueError()
            return data.user.id
        except (ValueError, TypeError):
            raise web.HTTPUnauthorized(text="Откройте приложение заново через Telegram.") from None

    def state(user):
        record = service.sessions.load(user)
        key, prompt = service.question(record)
        billing = service.billing
        result = {
            "answers": record["answers"], "question": key, "prompt": prompt,
            "dirty": record.get("dirty", False),
            "subscription": {"active": billing.active_until(user) > datetime.now().timestamp(),
                             "expires": billing.active_until(user), "rubles": 299, "stars": billing.stars,
                             "canceled": billing.canceled(user)},
            "allowed": billing.allowed(user, record["answers"]),
            "job": jobs.get(user, {}).get("status", "idle"),
            "error": jobs.get(user, {}).get("error", ""),
        }
        if record.get("plan") and not record.get("dirty") and result["allowed"]:
            plan = record["plan"]
            people = names(record["answers"])
            result["days"] = []
            for day in plan["schedule"]:
                meals = []
                for meal_id, recipe_id in zip(record["answers"]["meals"], day):
                    recipe = plan["recipes"][recipe_id]
                    totals, portions = recipe_details(recipe, plan["products"])
                    meals.append({
                        "id": recipe_id, "name": recipe["name"], "meal": MEALS[meal_id - 1],
                        "instructions": recipe["instructions"],
                        "ingredients": [f'{plan["products"][p]["name"]} — {float(g):g} г' for p, g in totals.items()],
                        "portions": [{"person": people[i - 1], "grams": weight, "kcal": round(kcal),
                                      "serve": note} for i, (weight, kcal, note) in sorted(portions.items())]
                    })
                result["days"].append(meals)
            rows, total = basket(plan)
            _, limit, formula = period_budget(record["answers"], date.fromisoformat(record["plan_date"]))
            result["basket"] = {
                "total": float(total), "limit": limit, "formula": formula,
                "items": [{"id": str(i), "name": row.product["name"], "store": row.product["store"],
                           "packs": row.count, "grams": row.product["pack_g"],
                           "price": row.product["price"], "subtotal": float(row.subtotal),
                           "leftover": float(row.leftover)} for i, row in enumerate(rows)]
            }
            result["report"] = report(plan, record["answers"], date.fromisoformat(record["plan_date"]))
            result["plan_date"] = record["plan_date"]
        return result

    async def get_state(request):
        return web.json_response(state(user_id(request)))

    async def run_generation(user):
        try:
            await service.generate(user)
            jobs[user] = {"status": "done"}
        except Exception as error:
            LOG.warning("Generation failed: %s", type(error).__name__)
            jobs[user] = {"status": "error", "error": "Не удалось составить меню. Попробуйте ещё раз."}

    async def action(request):
        user = user_id(request)
        data = await request.json()
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="Неверный запрос.")
        kind = data.get("action")
        if jobs.get(user, {}).get("status") == "running":
            raise web.HTTPConflict(text="Дождитесь готовности меню.")
        if kind == "answer":
            message = data.get("text")
            if not isinstance(message, str) or not 1 <= len(message.strip()) <= 3500:
                raise web.HTTPBadRequest(text="Введите ответ до 3500 символов.")
            await service.answer(user, message.strip())
        elif kind == "stores":
            raw = data.get("stores")
            patch = clean_patch({"stores": raw})
            if not patch or next_question(service.sessions.load(user)["answers"]) != "stores":
                raise web.HTTPBadRequest(text="Выберите магазины в анкете.")
            await service.answer(user, patch=patch)
        elif kind == "generate":
            record = service.sessions.load(user)
            if next_question(record["answers"]) or record.get("clarification"):
                raise web.HTTPBadRequest(text="Сначала завершите анкету.")
            if not service.billing.allowed(user, record["answers"]):
                raise web.HTTPPaymentRequired(text="Нужен семейный доступ.")
            jobs[user] = {"status": "running"}
            task = asyncio.create_task(run_generation(user))
            jobs[user]["task"] = task
        elif kind in {"reset", "delete"}:
            await service.reset(user, delete=kind == "delete")
            jobs.pop(user, None)
        else:
            raise web.HTTPBadRequest(text="Неизвестное действие.")
        return web.json_response(state(user))

    async def invoice(request):
        user = user_id(request)
        try:
            payload = service.billing.invoice(user)
            from aiogram.types import LabeledPrice
            link = await bot.create_invoice_link(
                title="Семейное меню", description="Меню для нескольких человек. Подписка на 30 дней с автопродлением.",
                payload=payload, currency="XTR",
                prices=[LabeledPrice(label="Семейный доступ на 30 дней", amount=service.billing.stars)],
                subscription_period=2592000,
            )
            return web.json_response({"url": link})
        except ValueError as error:
            raise web.HTTPConflict(text=str(error)) from None

    async def cancel(request):
        user = user_id(request)
        charge = service.billing.latest_charge(user)
        if not charge:
            raise web.HTTPBadRequest(text="Активная подписка не найдена.")
        await bot.edit_user_star_subscription(user_id=user, telegram_payment_charge_id=charge, is_canceled=True)
        service.billing.set_canceled(user)
        return web.json_response({"ok": True})

    @web.middleware
    async def errors(request, handler):
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="Неверный запрос.") from None
        except Exception as error:
            LOG.warning("API failed: %s", type(error).__name__)
            raise web.HTTPServiceUnavailable(text="Не удалось выполнить запрос. Попробуйте ещё раз.") from None
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def cleanup(app):
        tasks = [job["task"] for job in jobs.values() if job.get("task")]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def index(request):
        return web.FileResponse(Path(__file__).parent / "web" / "index.html")

    app.middlewares.append(errors)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/action", action)
    app.router.add_post("/api/invoice", invoice)
    app.router.add_post("/api/cancel-subscription", cancel)
    app.router.add_get("/", index)
    app.router.add_static("/assets/", Path(__file__).parent / "web")
    app.on_cleanup.append(cleanup)
    return app
