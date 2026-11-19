import calendar
from datetime import date
import re
from unicodedata import normalize

STORES = (
    ("🧲", "Магнит"), ("🛒", "Пятёрочка"), ("🥬", "Перекрёсток"),
    ("🧺", "Дикси"), ("🎗", "Лента"), ("🐦", "Ашан"),
    ("🍏", "ВкусВилл"), ("🐤", "Чижик"), ("🏪", "Другой магазин"),
)
MEALS = ("Завтрак", "Обед", "Ужин", "Перекус")
START = (
    "Давайте подберём меню для вашей семьи. Я задам несколько вопросов, а потом пришлю "
    "блюда с калорийностью порций и список покупок.\n\n"
    "Сколько взрослых и детей в семье? Укажите возраст каждого ребёнка.\n"
    "Например: 2 взрослых и 2 ребёнка — 1 год 8 месяцев и 7 лет."
)
QUESTIONS = {
    "family": START,
    "restriction": "Есть ли у кого-то пищевая аллергия, непереносимость или ограничения от врача?\n1 — Нет\n2 — Да\n3 — Есть подозрение на реакцию",
    "avoid": "Кого это касается и какие продукты вы исключаете? Укажите отдельно для каждого человека.",
    "traces": "Нужно ли исключить продукты с маркировкой «может содержать следы» этих аллергенов?\n1 — Да\n2 — Нет\n3 — Не уверены",
    "young": "Какие продукты и текстуры уже ест каждый ребёнок до 3 лет и какие рекомендации педиатра или режим кормления нужно учесть?",
    "budget": "Какой у вас бюджет на продукты на месяц? Назовите сумму в рублях. Если нужен план на остаток месяца, укажите остаток. Если бюджет пока неизвестен, напишите «не знаю».",
    "baby_budget": "Покупаете ли вы смесь или готовое детское питание? Если да, входят ли эти расходы в названный бюджет и какую сумму оставить на них на период меню?",
    "city": "В каком городе вы покупаете продукты?",
    "stores": "Нажмите на карточки магазинов, где вам удобно покупать продукты, затем нажмите «Готово». Можно также отправить номера или названия. Доступность сетей в вашем городе не проверена.",
    "other_store": "Как называется другой магазин?",
    "period": "На какой срок нужно меню?\n1 — На 3 дня\n2 — На 7 дней\n3 — На месяц\n4 — Свой вариант",
    "custom_days": "На сколько дней нужно меню?",
    "meals": "Какие приёмы пищи включить?\n1 — Завтрак\n2 — Обед\n3 — Ужин\n4 — Перекусы\nМожно выбрать несколько или написать «пропустить».",
    "goals": "Есть ли у взрослых готовые цели по калориям на день?\n1 — Да, укажу для кого и сколько\n2 — Нет, достаточно калорийности порций",
    "goal_details": "Для кого из взрослых и сколько килокалорий в день? Например, взрослый 1 — 1900 ккал.",
    "pantry": "Что уже есть дома и какие продукты или блюда семья не любит? Для запасов укажите примерное количество. Можно написать «пропустить».",
}


def next_question(state):
    family = state.get("family")
    if not family or len(family["ages_months"]) != family["children"] or None in family["ages_months"]:
        return "family"
    if "restriction" not in state:
        return "restriction"
    if state["restriction"] != "no":
        if not state.get("avoid"):
            return "avoid"
        if state.get("traces") is None:
            return "traces"
    if any(age < 36 for age in family["ages_months"]) and not state.get("young"):
        return "young"
    if "budget" not in state:
        return "budget"
    if (state["budget"]["type"] != "unknown"
            and any(age < 36 for age in family["ages_months"]) and "baby_budget" not in state):
        return "baby_budget"
    for key in ("city", "stores"):
        if not state.get(key):
            return key
    if 9 in state["stores"] and not state.get("other_store"):
        return "other_store"
    if "period" not in state:
        return "period"
    if state["period"] == "custom" and not state.get("custom_days"):
        return "custom_days"
    if not state.get("meals"):
        return "meals"
    if "goals" not in state:
        return "goals"
    if state["goals"] == "yes" and not state.get("goal_details"):
        return "goal_details"
    return "pantry" if "pantry" not in state else None


def period_budget(state, today=None):
    today = today or date.today()
    month_days = calendar.monthrange(today.year, today.month)[1]
    remaining = month_days - today.day + 1
    days = {"three": 3, "week": 7, "month": month_days, "remaining": remaining,
            "custom": state.get("custom_days")}.get(state["period"])
    budget = state["budget"]
    if budget["type"] == "unknown":
        return days, None, "Сумма не задана. Подбираем экономное меню."
    base_days = remaining if budget["type"] == "remaining" else month_days
    covered = min(days, remaining) if budget["type"] == "remaining" else days
    reserve = state.get("baby_budget") or 0
    rubles = budget["rubles"]
    gross = round(rubles * covered / base_days, 2)
    limit = round(max(0, gross - reserve), 2)
    details = f"{rubles:g} ₽ ÷ {base_days} дн. × {covered} дн. = {gross:g} ₽"
    if reserve:
        details += f". Резерв на детское питание — {reserve:g} ₽"
    if covered < days:
        details += f". Известный остаток относится к первым {covered} из {days} дней"
    return days, limit, details


def names_of_stores(state):
    return [state["other_store"] if index == 9 else STORES[index - 1][1]
            for index in state["stores"]]


def store_markup(selected):
    buttons = [
        {"text": f"{'✓ ' if i in selected else ''}{icon} {i}. {name}", "callback_data": f"shop:{i}"}
        for i, (icon, name) in enumerate(STORES, 1)
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return {"inline_keyboard": rows + [[{"text": "Все доступные", "callback_data": "shop:all"},
                                        {"text": "Готово", "callback_data": "shop:done"}]]}


def store_ids(message):
    words = normalize("NFKC", message).casefold().replace("ё", "е")
    if re.fullmatch(r"[1-9](?:[\s,;]+[1-9])*", words.strip()):
        return sorted(set(int(digit) for digit in re.findall(r"[1-9]", words)))
    if words.strip() in {"все доступные", "все магазины", "все"}:
        return list(range(1, 9))
    return [
        index for index, (_, name) in enumerate(STORES, 1)
        if name.casefold().replace("ё", "е") in words
    ]


def clean_patch(raw):
    """Return only well-formed answers. Missing fields leave earlier answers intact."""
    patch = {}
    family = raw.get("family")
    if isinstance(family, dict):
        adults, children, ages = (family.get(key) for key in ("adults", "children", "ages_months"))
        if (type(adults) is int and 0 <= adults <= 30 and type(children) is int
                and 0 <= children <= 30 and adults + children > 0 and isinstance(ages, list)
                and len(ages) == children
                and all(x is None or type(x) is int and 0 <= x <= 215 for x in ages)):
            patch["family"] = family
    for key, options in {
        "restriction": ("no", "yes", "suspected"),
        "traces": ("yes", "no", "unsure"),
        "period": ("three", "week", "month", "remaining", "custom"),
        "goals": ("yes", "no"),
    }.items():
        if raw.get(key) in options:
            patch[key] = raw[key]
    for key in ("avoid", "young", "city", "other_store", "goal_details", "pantry"):
        value = raw.get(key)
        if isinstance(value, str) and (value.strip() or key == "pantry"):
            patch[key] = value.strip()[:2500]
    budget = raw.get("budget")
    if isinstance(budget, dict):
        value = budget.get("rubles")
        if budget.get("type") == "unknown":
            patch["budget"] = {"type": "unknown"}
        elif budget.get("type") in ("monthly", "remaining") and type(value) in (int, float) and 0 <= value <= 100000000:
            patch["budget"] = {"type": budget["type"], "rubles": value}
    reserve = raw.get("baby_budget")
    if type(reserve) in (int, float) and 0 <= reserve <= 100000000:
        patch["baby_budget"] = reserve
    days = raw.get("custom_days")
    if type(days) is int and 1 <= days <= 31:
        patch["custom_days"] = days
    for key, allowed in (("stores", range(1, 10)), ("meals", range(1, 5))):
        value = raw.get(key)
        if isinstance(value, list) and value and all(type(item) is int and item in allowed for item in value):
            patch[key] = sorted(set(value))
    return patch


def local_answer(question, message):
    """Resolve numbered answers before asking the model to interpret the text."""
    message = message.strip().casefold()
    options = {
        "restriction": ("no", "yes", "suspected"),
        "traces": ("yes", "no", "unsure"),
        "period": ("three", "week", "month", "custom"),
        "goals": ("yes", "no"),
    }.get(question, ())
    if message in [str(number) for number in range(1, len(options) + 1)]:
        return {question: options[int(message) - 1]}
    if question == "stores" and (stores := store_ids(message)):
        return {"stores": stores}
    if question == "meals":
        if message in {"пропустить", "обычные", "по умолчанию", "без перекусов"}:
            return {"meals": [1, 2, 3]}
        if re.fullmatch(r"[1-4](?:[\s,;]+[1-4])*", message):
            return {"meals": sorted(set(map(int, re.findall(r"[1-4]", message))))}
    if question == "pantry" and message in {"пропустить", "нет", "ничего"}:
        return {"pantry": ""}
    if question == "custom_days" and re.fullmatch(r"(?:[1-9]|[12][0-9]|3[01])", message):
        return {"custom_days": int(message)}
    return None


def apply_answers(state, patch):
    if "family" in patch and patch["family"] != state.get("family") and state.get("family"):
        for field in ("young", "baby_budget"):
            if field not in patch:
                state.pop(field, None)
    if patch.get("restriction") != state.get("restriction") and "restriction" in patch:
        for field in ("avoid", "traces"):
            if field not in patch:
                state.pop(field, None)
    if "avoid" in patch and patch["avoid"] != state.get("avoid") and "traces" not in patch:
        state.pop("traces", None)
    if "budget" in patch and patch["budget"]["type"] == "remaining" and "period" not in patch:
        if "period" not in state:
            patch = {**patch, "period": "remaining"}
    state.update(patch)
