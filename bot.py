import logging
import aiohttp
import sqlite3
import time
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from datetime import datetime
import os
import asyncio

# === Settings ===
BOT_TOKEN = "8449359446:AAFdrzGXh_45uPMuljM5IR59AmdXhimAM_k"
USERSBOX_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
CRYPTOPAY_TOKEN = "441337:AAqgjWaWbcDpk07XquOwGL3ki4fZNXzJtyL"
OWNER_ID = 8069798171
BASE_CURRENCY = "USDT"

TARIFFS = {
    "month": {"price": 49, "days": 29},
    "quarter": {"price": 120, "days": 89},
    "lifetime": {"price": 299, "days": 9999},
    "hide_data": {"price": 100, "days": 0}
}

TRIAL_LIMIT = 3
FLOOD_LIMIT = 10
FLOOD_WINDOW = 15
FLOOD_INTERVAL = 3

# === Init ===
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
DB_PATH = os.path.join(os.getcwd(), "n3l0x_users.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()

# Создание таблиц
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    subs_until INTEGER,
    free_used INTEGER,
    trial_expired INTEGER DEFAULT 0,
    last_queries TEXT DEFAULT '',
    hidden_data INTEGER DEFAULT 0
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
)
""")
conn.commit()

def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 29 days - $49", callback_data="buy_month")],
        [InlineKeyboardButton(text="🔒 89 days - $120", callback_data="buy_quarter")],
        [InlineKeyboardButton(text="🔒 Lifetime - $299", callback_data="buy_lifetime")],
        [InlineKeyboardButton(text="🧊 Hide my data - $100", callback_data="buy_hide_data")]
    ])

@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    now = int(time.time())
    c.execute("SELECT subs_until, free_used FROM users WHERE id=?", (uid,))
    row = c.fetchone()

    if not row:
        subs_until = now + 100 * 365 * 24 * 60 * 60 if is_admin(uid) else 0
        c.execute("INSERT INTO users (id, subs_until, free_used, trial_expired) VALUES (?, ?, ?, ?)", (uid, subs_until, 0, 0))
        conn.commit()

    if is_admin(uid):
        trial_msg = "<b>As admin, you have unlimited free searches.</b>"
    else:
        c.execute("SELECT free_used FROM users WHERE id=?", (uid,))
        free_used = c.fetchone()[0]
        remaining = TRIAL_LIMIT - free_used
        trial_msg = f"<b>You have {remaining} free searches remaining.</b>" if remaining > 0 else "<b>Your free trial has ended.</b>"

    await message.answer(
        "👾 Welcome to n3l0x 👾\n"
        "n3l0x — a service for searching various information.\n"
        "n3l0x — has a database of 20 billion records.\n"
        "n3l0x — can find your mom.\n"
        "n3l0x — and you’re a loser if you’re still not using n3l0x.\n\n"
        f"{trial_msg}",
        reply_markup=sub_keyboard()
    )

def is_subscribed(uid: int) -> (bool, int):
    if is_admin(uid):
        return True, 0
    c.execute("SELECT subs_until, free_used FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    now = int(time.time())
    if not row:
        c.execute("INSERT INTO users (id, subs_until, free_used) VALUES (?, ?, ?)", (uid, 0, 0))
        conn.commit()
        return False, 0
    subs_until, free_used = row
    return subs_until > now, free_used

def check_flood(uid: int) -> bool:
    c.execute("SELECT last_queries FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    now = int(time.time())
    if not row:
        return False
    times = [int(t) for t in row[0].split(',') if t]
    times.append(now)
    times = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute("UPDATE users SET last_queries=? WHERE id=?", (','.join(map(str, times)), uid))
    conn.commit()
    return len(times) > FLOOD_LIMIT or (len(times) >= 2 and times[-1] - times[-2] < FLOOD_INTERVAL)

@dp.message()
async def search_handler(message: Message):
    uid = message.from_user.id
    query = message.text.strip()

    if not is_admin(uid) and check_flood(uid):
        return await message.answer("⛔ Flood detected. Try again later.")

    c.execute("SELECT 1 FROM blacklist WHERE value = ?", (query,))
    if c.fetchone():
        return await message.answer("🔒 Access denied. Data is encrypted.")

    subscribed, free_used = is_subscribed(uid)
    if not is_admin(uid) and not subscribed and free_used >= TRIAL_LIMIT:
        c.execute("UPDATE users SET trial_expired=1 WHERE id=?", (uid,))
        conn.commit()
        return await message.answer("🔐 Your trial is over. Please purchase a subscription to continue:", reply_markup=sub_keyboard())

    url = f"https://api.usersbox.ru/v1/search?q={query}"
    headers = {"Authorization": USERSBOX_API_KEY}
    await message.answer(f"🕷️ Connecting to nodes...\n🧬 Running recon on <code>{query}</code>")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return await message.answer(f"⚠️ Error: {response.status}")
            result = await response.json()

    if result.get("status") != "success" or result.get("data", {}).get("count", 0) == 0:
        return await message.answer("📡 No match found in any database.")

    def beautify_key(key):
        key_map = {
            "full_name": "Имя", "phone": "Телефон", "inn": "ИНН",
            "email": "Email", "first_name": "Имя", "last_name": "Фамилия",
            "middle_name": "Отчество", "birth_date": "Дата рождения",
            "gender": "Пол", "passport_series": "Серия паспорта",
            "passport_number": "Номер паспорта", "passport_date": "Дата выдачи паспорта"
        }
        return key_map.get(key, key)

    blocks = []
    for item in result["data"]["items"]:
        source = item.get("source", {})
        header = f"☠ {source.get('database','?')}"
        lines = []
        for hit in item.get("hits", {}).get("items", []):
            for k, v in hit.items():
                if not v:
                    continue
                val = ", ".join(str(i) for i in v) if isinstance(v, list) else str(v)
                lines.append(f"<div class='row'><span class='key'>{beautify_key(k)}:</span><span class='val'>{val}</span></div>")
        if lines:
            blocks.append(f"<div class='block'><div class='header'>{header}</div>{''.join(lines)}</div>")

    # Сохраняем во временную папку
    filename = f"/tmp/{query}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"""
        <html><head><meta charset='UTF-8'><title>n3l0x OSINT Report</title><style>
        body {{ background:#000;color:#0f0;font-family:monospace;padding:20px }}
        .block {{ border:1px solid #0f0;padding:10px;margin:10px }}
        .key {{ color:#6f6; }} .val {{ color:#f6f; float:right }}
        </style></head><body>
        <h1>n3l0x Intelligence Report</h1>
        {''.join(blocks)}
        </body></html>
        """)

    await message.answer("Нашел кента. Держи файл:")
    await message.answer_document(FSInputFile(filename))
    os.remove(filename)

    if not is_admin(uid) and not subscribed:
        c.execute("UPDATE users SET free_used = free_used + 1 WHERE id=?", (uid,))
        conn.commit()

@dp.callback_query()
async def handle_callback(call: CallbackQuery):
    plan = call.data.replace("buy_", "")
    if plan in TARIFFS:
        tariff = TARIFFS[plan]
        payload = f"n3l0x_{call.from_user.id}_{plan}_{int(time.time())}"
        pay_url = f"https://t.me/CryptoBot?start=merchant_{CRYPTOPAY_TOKEN}_{tariff['price']}_{BASE_CURRENCY}_{payload}"
        await call.message.answer(
            f"💳 You chose <b>{plan}</b> plan for <b>${tariff['price']}</b>.\n"
            f"Click below to proceed with payment in {BASE_CURRENCY}:\n"
            f"<a href='{pay_url}'>Pay via CryptoBot</a>",
            disable_web_page_preview=True
        )

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
