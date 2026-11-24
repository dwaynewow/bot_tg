"""Shared conversation state for aiogram and the Mini App."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from interview import QUESTIONS, apply_answers, local_answer, next_question
from model import extract, make_plan


class MealService:
    def __init__(self, sessions, api, billing, timezone="Europe/Moscow"):
        self.sessions = sessions
        self.api = api
        self.billing = billing
        self.timezone = ZoneInfo(timezone)
        # Bounded lock set serializes API and chat writes for the same user.
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.generations = asyncio.Semaphore(4)

    def lock(self, user):
        return self.locks[user % len(self.locks)]

    async def answer(self, user, message=None, patch=None):
        async with self.lock(user):
            record = self.sessions.load(user)
            state = record["answers"]
            question = next_question(state)
            hint, revision = "", ""
            if patch is None:
                patch = local_answer(question, message) if not record.get("clarification") else None
                if patch is None:
                    patch, revision, hint = await asyncio.to_thread(
                        extract, self.api, state, question, message,
                        bool(record.get("plan")), record.get("clarification", "")
                    )
            if patch:
                apply_answers(state, patch)
                record["dirty"] = True
            if revision:
                record["revision"] = revision
                record["dirty"] = True
            if hint:
                record["clarification"] = hint
            else:
                record.pop("clarification", None)
            self.sessions.save(user, record)
            return record

    async def generate(self, user):
        async with self.lock(user):
            record = self.sessions.load(user)
            if next_question(record["answers"]) or record.get("clarification"):
                raise ValueError("Сначала завершите анкету.")
            if not self.billing.allowed(user, record["answers"]):
                raise PermissionError("Для меню на нескольких человек нужен семейный доступ.")
            if record.get("plan") and not record.get("dirty"):
                return record
            today = datetime.now(self.timezone).date()
            async with self.generations:
                plan = await asyncio.to_thread(
                    make_plan, self.api, record["answers"], today,
                    record.get("plan"), record.get("revision", "")
                )
            record.update(plan=plan, plan_date=today.isoformat(), dirty=False)
            record.pop("revision", None)
            self.sessions.save(user, record)
            return record

    async def reset(self, user, delete=False):
        async with self.lock(user):
            if delete:
                self.sessions.delete(user)
            else:
                self.sessions.save(user, {"answers": {}})

    def question(self, record):
        key = next_question(record["answers"])
        return key, record.get("clarification") or QUESTIONS.get(key, "")
