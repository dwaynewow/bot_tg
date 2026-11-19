from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from math import isfinite
import re

from interview import MEALS, names_of_stores, period_budget


class InvalidPlan(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidPlan(message)


def number(value, minimum, maximum):
    return type(value) in (int, float) and isfinite(value) and minimum <= value <= maximum


def names(state):
    family = state["family"]
    people = [f"Взрослый {i}" for i in range(1, family["adults"] + 1)]
    people.extend(f"Ребёнок {i} ({months} мес.)" for i, months in enumerate(family["ages_months"], 1))
    return people


def validate(plan, state, days):
    require(isinstance(plan, dict), "нужен объект plan")
    products, recipes, schedule = (plan.get(key) for key in ("products", "recipes", "schedule"))
    require(isinstance(products, dict) and 0 < len(products) <= 180, "нужно 1–180 products")
    require(isinstance(recipes, dict) and 0 < len(recipes) <= 124, "нужно 1–124 recipes")
    require(isinstance(schedule, list) and len(schedule) == days, f"в schedule должно быть {days} дней")
    available_stores = names_of_stores(state)
    kitchen_water = "Вода питьевая из дома"
    for key, item in products.items():
        require(isinstance(item, dict), f"{key}: нужен объект")
        require(all(isinstance(item.get(field), str) and item[field].strip()
                    for field in ("name", "form")), f"{key}: нужны название и состояние")
        require(number(item.get("kcal100"), 0, 1000), f"{key}: неверный kcal100")
        require(number(item.get("pantry_g"), 0, 100000), f"{key}: неверный pantry_g")
        require(number(item.get("edible_pct"), 1, 100), f"{key}: неверный edible_pct")
        if item["name"] == kitchen_water:
            require(item["kcal100"] == item["pantry_g"] == item.get("price") == item.get("pack_g") == 0
                    and item["edible_pct"] == 100, f"{key}: неверные параметры питьевой воды")
        else:
            require(item.get("store") in available_stores, f"{key}: магазин должен совпадать с выбранным")
            require(number(item.get("pack_g"), 1, 30000), f"{key}: неверный pack_g")
            require(number(item.get("price"), 0.01, 50000), f"{key}: неверный price")
    for day in schedule:
        require(isinstance(day, list) and len(day) == len(state["meals"]), "в дне нужны все выбранные приёмы")
        require(all(isinstance(key, str) and key in recipes for key in day), "в расписании неизвестный рецепт")

    family = state["family"]
    ages = [None] * family["adults"] + family["ages_months"]
    for key, recipe in recipes.items():
        require(isinstance(recipe, dict) and all(isinstance(recipe.get(field), str)
                and recipe[field].strip() for field in ("name", "instructions")), f"{key}: нужно название и приготовление")
        groups = recipe.get("portions")
        members = set()
        require(isinstance(groups, list) and groups, f"{key}: нужны порции")
        for portion in groups:
            require(isinstance(portion, dict), f"{key}: неверная порция")
            indexes, ingredients = portion.get("people"), portion.get("ingredients")
            require(isinstance(indexes, list) and indexes, f"{key}: укажите получателей порции")
            require(isinstance(ingredients, dict), f"{key}: укажите ingredients")
            note = portion.get("serve")
            require(isinstance(note, str), f"{key}: нужны инструкции к подаче")
            for index in indexes:
                require(type(index) is int and 1 <= index <= len(ages) and index not in members,
                        f"{key}: человек повторяется или отсутствует в семье")
                age = ages[index - 1]
                if age is not None and age < 36:
                    require(bool(note.strip()), f"{key}: для ребёнка до 3 лет нужна подсказка")
                if not ingredients:
                    require(age is not None and age < 36 and portion.get("ready_g") == 0,
                            f"{key}: пустая порция допустима для ребёнка до 3 лет по указанному режиму")
                elif age is not None and age < 6:
                    raise InvalidPlan(f"{key}: в меню для ребёнка младше 6 месяцев оставьте указанный режим кормления")
                elif age is not None and age < 36:
                    for product_id in ingredients.keys() & products.keys():
                        name = products[product_id]["name"].casefold().replace("ё", "е")
                        if age < 12 and re.search(r"\bмед(?:а|ом|овый|овая|овые)?\b", name):
                            raise InvalidPlan(f"{key}: мёд нельзя включать в порцию ребёнка до 12 месяцев")
                        if re.search(r"\b[а-я]*(?:виноград|орех)[а-я]*\b", name):
                            require(bool(re.search(r"разрез|четвертин|измельч|молот|перемол|паст|пюре", note.casefold())),
                                    f"{key}: уточните безопасную текстуру детской порции")
                members.add(index)
            if ingredients:
                require(number(portion.get("ready_g"), 1, 4000), f"{key}: неверный готовый вес")
                require(all(product in products and number(grams, 0.01, 5000)
                            for product, grams in ingredients.items()), f"{key}: неверные ингредиенты")
        require(len(members) == len(ages), f"{key}: нужна порция для каждого человека")
    return plan


def cash(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def grams(value):
    result = f"{value:.1f}".removesuffix(".0").replace(".", ",")
    return result + " г"


def decimal(value):
    return Decimal(str(value))


@dataclass
class Purchase:
    product: dict
    count: int
    subtotal: Decimal
    leftover: Decimal
    pantry_used: Decimal


def recipe_details(recipe, products):
    totals = Counter()
    servings = {}
    for portion in recipe["portions"]:
        energy = Decimal(0)
        for key, amount in portion["ingredients"].items():
            amount = decimal(amount)
            energy += amount * decimal(products[key]["kcal100"]) / 100
            totals[key] += amount * len(portion["people"])
        for member in portion["people"]:
            servings[member] = (portion["ready_g"], energy, portion["serve"])
    return totals, servings


def basket(plan):
    used = Counter()
    for key, count in Counter(key for day in plan["schedule"] for key in day).items():
        totals, _ = recipe_details(plan["recipes"][key], plan["products"])
        for product, weight in totals.items():
            used[product] += weight * count
    rows = []
    for key, weight in used.items():
        product = plan["products"][key]
        if product["pack_g"] == 0:
            continue
        need = weight * 100 / decimal(product["edible_pct"])
        home = decimal(product["pantry_g"])
        size = decimal(product["pack_g"])
        count = int((max(Decimal(0), need - home) / size).to_integral_value(rounding=ROUND_CEILING))
        rows.append(Purchase(product, count, count * decimal(product["price"]), home + count * size - need,
                             min(need, home)))
    return rows, sum((row.subtotal for row in rows), Decimal(0))


def report(plan, state, today):
    days, limit, formula = period_budget(state, today)
    people = names(state)
    products = plan["products"]
    recipes = plan["recipes"]
    rows, total = basket(plan)
    intro = [
        f"Семейное меню на {days} дн.",
        "Семья: " + ", ".join(people),
        f'Город: {state["city"]}. Магазины: {", ".join(names_of_stores(state))}.',
        f"Бюджет на покупки: {cash(limit) if limit is not None else 'сумма не задана'}. {formula}",
        "Цены ориентировочные. Наличие и скидки не проверены. Калорийность и выход готовых блюд справочные.",
    ]
    if state.get("restriction") != "no":
        intro.append(
            "Перед покупкой проверьте состав по этикетке конкретного продукта и учтите указанные "
            "семьёй ограничения по следам аллергенов. Выбирайте продукты с доступной маркировкой. "
            "Для отдельных порций используйте чистую посуду и поверхности."
        )
    if state.get("goals") == "yes":
        intro.append("Заданные цели взрослых: " + state["goal_details"])
    if any(age < 36 for age in state["family"]["ages_months"]):
        intro.append("Для детей до 3 лет сохраняйте привычное кормление и текстуры. Указанные граммы — для расчёта рецепта. Калорийность детских порций справочная.")
    sections = ["\n".join(intro)]
    week = []
    for day, slots in enumerate(plan["schedule"], 1):
        if (day - 1) % 7 == 0:
            week = [f"Неделя {(day - 1) // 7 + 1}"]
        lines = [f"День {day}"]
        daily = defaultdict(Decimal)
        for meal_id, key in zip(state["meals"], slots):
            recipe = recipes[key]
            lines.append(f'{MEALS[meal_id - 1]} — {recipe["name"]}')
            _, servings = recipe_details(recipe, products)
            for member, (weight, energy, note) in sorted(servings.items()):
                line = f"  {people[member - 1]}: "
                if weight:
                    line += f"{grams(weight)}, {round(energy)} ккал"
                    daily[member] += energy
                else:
                    line += "кормление по вашему режиму"
                lines.append(line + (f". {note}" if note else ""))
        if daily:
            lines.append("За день по этому меню: " + "; ".join(
                f"{people[member - 1]} — {round(energy)} ккал" for member, energy in daily.items()
            ))
        week.append("\n".join(lines))
        if day % 7 == 0 or day == days:
            sections.append("\n\n".join(week))

    blocks = ["Рецепты на всю семью"]
    for key in dict.fromkeys(key for day in plan["schedule"] for key in day):
        recipe = recipes[key]
        amounts, _ = recipe_details(recipe, products)
        lines = [recipe["name"]]
        for item, amount in amounts.items():
            product = products[item]
            lines.append(f'• {product["name"]}, {product["form"]} — {grams(amount)}')
        lines.append(recipe["instructions"])
        blocks.append("\n".join(lines))
    sections.append("\n\n".join(blocks))

    shops = ["Покупки по магазинам"]
    for store in names_of_stores(state):
        store_rows = [row for row in rows if row.product["store"] == store and row.count]
        if not store_rows:
            continue
        shops.append(store)
        for row in store_rows:
            p = row.product
            shops.append(f'• {p["name"]} — {row.count} уп. × {grams(p["pack_g"])} по {cash(p["price"])} = {cash(row.subtotal)}')
        shops.append(f"Итого в магазине — {cash(sum(row.subtotal for row in store_rows))}\n")
    shops.append(f"Вся корзина — {cash(total)}")
    sections.append("\n".join(shops))

    summary = ["Итог по бюджету", f"Покупки — {cash(total)}"]
    if limit is not None:
        summary.append(f"Лимит — {cash(limit)}")
        difference = decimal(limit) - total
        summary.append(f"{'Остаток бюджета' if difference >= 0 else 'Выше лимита на'} — {cash(abs(difference))}")
    home = [f'{row.product["name"]} — {grams(row.pantry_used)}' for row in rows if row.pantry_used]
    if home:
        summary.append("Из запасов: " + ", ".join(home))
    leftovers = [f'{row.product["name"]} — {grams(row.leftover)}' for row in rows if row.leftover >= Decimal("0.1")]
    summary.append("После меню останется: " + (", ".join(leftovers) if leftovers else "ничего из учтённых продуктов"))
    sections.append("\n".join(summary))
    return sections
