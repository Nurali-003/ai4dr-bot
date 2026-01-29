import os
import telebot
from telebot import types
import json, re
from datetime import date
from openai import OpenAI

# ====== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (Railway) ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN not set")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set")

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True)
ai = OpenAI(api_key=OPENAI_API_KEY)

DATA_FILE = "data.json"
state = {}

# ====== УТИЛИТЫ ======
def load():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def ensure_user(d, uid):
    if uid not in d:
        d[uid] = {"routines": [], "history": {}}

def to_min(t):
    h, m = map(int, t.split(":"))
    return h * 60 + m

def to_time(m):
    m %= 1440
    return f"{m//60:02d}:{m%60:02d}"

def overlap(a1, a2, b1, b2):
    return not (a2 <= b1 or b2 <= a1)

# ====== МЕНЮ ======
def main_menu(chat):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📅 Дневные рутины")
    kb.add("🤖 Чат с ИИ")
    kb.add("📊 Моя активность")
    bot.send_message(chat, "🏠 Главное меню\nВыбери действие 👇", reply_markup=kb)

# ====== START ======
@bot.message_handler(commands=["start"])
def start(msg):
    uid = str(msg.chat.id)
    d = load()
    ensure_user(d, uid)
    save(d)
    state.pop(uid, None)

    bot.send_message(
        msg.chat.id,
        "👋 Привет!\n\n"
        "Я — AI4DR 🤖\n"
        "Умный ассистент для дневных рутин.\n\n"
        "Я помогу тебе:\n"
        "• планировать день 📅\n"
        "• отмечать выполнение ☑\n"
        "• анализировать прогресс 📊\n"
        "• получать умные советы 🧠\n\n"
        "С чего начнём?"
    )
    main_menu(msg.chat.id)

# ====== РУТИНЫ ======
def show_routines(chat):
    uid = str(chat)
    today = str(date.today())
    d = load()
    ensure_user(d, uid)
    d[uid]["history"].setdefault(today, {})
    save(d)

    if not d[uid]["routines"]:
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("➕ Добавить рутину", "⬅ Назад")
        bot.send_message(
            chat,
            "📭 Рутин пока нет.\nДобавь первую 👇",
            reply_markup=kb
        )
        return

    kb = types.InlineKeyboardMarkup()
    text = "📅 Твои рутины сегодня:\n\n"

    for r in d[uid]["routines"]:
        rid = r["id"]
        done = d[uid]["history"][today].get(rid, False)
        mark = "☑" if done else "☐"
        time = f"{to_time(r['start'])}-{to_time(r['end'])}"
        text += f"{mark} {r['name']} ({time})\n"
        kb.add(types.InlineKeyboardButton(
            text=f"{mark} {r['name']}",
            callback_data=f"toggle:{rid}"
        ))

    kb.add(types.InlineKeyboardButton("➕ Добавить рутину", callback_data="add"))
    bot.send_message(chat, text, reply_markup=kb)

# ====== CALLBACK ======
@bot.callback_query_handler(func=lambda c: True)
def callbacks(c):
    uid = str(c.message.chat.id)
    today = str(date.today())
    d = load()
    ensure_user(d, uid)

    if c.data == "add":
        state[uid] = {"step": "name"}
        bot.send_message(c.message.chat.id, "✍️ Введи название рутины:")
        return

    if c.data.startswith("toggle:"):
        rid = c.data.split(":")[1]
        d[uid]["history"].setdefault(today, {})
        d[uid]["history"][today][rid] = not d[uid]["history"][today].get(rid, False)
        save(d)
        bot.answer_callback_query(c.id, "Готово ✅")
        bot.delete_message(c.message.chat.id, c.message.message_id)
        show_routines(c.message.chat.id)

# ====== ОСНОВНОЙ HANDLER ======
@bot.message_handler(func=lambda m: True)
def handle(m):
    uid = str(m.chat.id)
    txt = m.text
    today = str(date.today())
    d = load()
    ensure_user(d, uid)

    # Назад
    if txt == "⬅ Назад":
        state.pop(uid, None)
        main_menu(m.chat.id)
        return

    # ---- СОСТОЯНИЯ ----
    if uid in state:
        st = state[uid]

        if st["step"] == "name":
            state[uid] = {"step": "time", "name": txt}
            bot.send_message(
                m.chat.id,
                "⏰ Введи время\nПример: 17:00-18:00 или 23:00-07:00"
            )
            return

        if st["step"] == "time":
            if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", txt):
                bot.send_message(m.chat.id, "❌ Неверный формат времени")
                return

            a, b = txt.split("-")
            s, e = to_min(a), to_min(b)
            if e <= s:
                e += 1440  # ночная рутина

            for r in d[uid]["routines"]:
                if overlap(s, e, r["start"], r["end"]):
                    bot.send_message(m.chat.id, "❌ Конфликт по времени")
                    return

            rid = str(len(d[uid]["routines"]))
            d[uid]["routines"].append({
                "id": rid,
                "name": st["name"],
                "start": s,
                "end": e
            })

            d[uid]["history"].setdefault(today, {})[rid] = False
            save(d)
            state.pop(uid)
            show_routines(m.chat.id)
            return

        # ---- ИИ ----
        if st["step"] == "ai":
            try:
                routines_context = ""
                for r in d[uid]["routines"]:
                    routines_context += f"- {r['name']} ({to_time(r['start'])}-{to_time(r['end'])})\n"

                system_prompt = (
                    "Ты — AI4DR, умный ассистент по дневным рутинам.\n"
                    "Отвечай кратко и по делу.\n\n"
                    "Если пользователь просит добавить рутину, "
                    "ответь строго в формате:\n"
                    "ADD: название | HH:MM-HH:MM\n\n"
                    "Текущие рутины:\n"
                    f"{routines_context if routines_context else 'Рутин пока нет.'}"
                )

                resp = ai.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": txt}
                    ],
                    timeout=20
                )

                answer = resp.choices[0].message.content.strip()

                if answer.startswith("ADD:"):
                    _, line = answer.split("ADD:")
                    name, time_range = line.split("|")
                    a, b = time_range.strip().split("-")
                    s, e = to_min(a), to_min(b)
                    if e <= s:
                        e += 1440

                    rid = str(len(d[uid]["routines"]))
                    d[uid]["routines"].append({
                        "id": rid,
                        "name": name.strip(),
                        "start": s,
                        "end": e
                    })
                    d[uid]["history"].setdefault(today, {})[rid] = False
                    save(d)

                    bot.send_message(
                        m.chat.id,
                        f"✅ Я добавил рутину:\n{name.strip()} ({a}-{b})"
                    )
                else:
                    bot.send_message(m.chat.id, answer)

            except:
                bot.send_message(
                    m.chat.id,
                    "⚠️ ИИ временно недоступен.\nПопробуй позже."
                )
            return

    # ---- КНОПКИ ----
    if txt == "📅 Дневные рутины":
        show_routines(m.chat.id)
        return

    if txt == "➕ Добавить рутину":
        state[uid] = {"step": "name"}
        bot.send_message(m.chat.id, "✍️ Введи название рутины:")
        return

    if txt == "🤖 Чат с ИИ":
        state[uid] = {"step": "ai"}
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("⬅ Назад")
        bot.send_message(
            m.chat.id,
            "🤖 Режим ИИ включён.\n\n"
            "Напиши вопрос или просьбу.\n"
            "Пример:\n"
            "• Как лучше планировать день?\n"
            "• Добавь рутину сон 23:00-07:00",
            reply_markup=kb
        )
        return

    if txt == "📊 Моя активность":
        days = sum(1 for h in d[uid]["history"].values() if h and all(h.values()))
        bot.send_message(
            m.chat.id,
            f"📊 Твоя активность\n\n"
            f"🔥 Полностью выполненных дней: {days}"
        )
        return

    bot.send_message(m.chat.id, "Используй меню 👇")

bot.polling(none_stop=True)
