import logging
import aiohttp
import sqlite3
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from datetime import datetime
import os

# === Settings ===
BOT_TOKEN = "8449359446:AAFdrzGXh_45uPMuljM5IR59AmdXhimAM_k"
USERSBOX_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjcmVhdGVkX2F0IjoxNzU0MzM5MzkxLCJhcHBfaWQiOjE3NTQzMzkzOTF9.v4td4yMijevAt6WE7IUtkLkCfiZ1k-cF3bpUGNedng8"
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

# Admin hidden values
ADMIN_HIDDEN = [
    "Кохан Богдан Олегович 10.07.1999",
    "Кохан Богдан Олегович",
    "10.07.1999",
    "380636659255",
    "0636659255",
    "80636659255",
    "+380636659255",
    "+380683220001",
    "0683220001",
    "380683220001",
    "+380961021426",
    "380961021426",
    "0961021426",
    "+380954106034",
    "380961021426",
    "0961021426",
    "Клюев Максим Олегович 05.10.1998",
    "Клюев Максим Олегович",
    "Kokhan Bohgdan Olehovych",
    "Гапон Сергей Сергеевич 19.10.1997",
    "Гапон Сергей Сергеевич",
    "+380660266270",
    "380660266270",
    "0660266270",
    "80660266270",
]

# === Init ===
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
conn = sqlite3.connect("n3l0x_users.db")
c = conn.cursor()
# Create tables if not exist
c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, subs_until INTEGER, free_used INTEGER, trial_expired INTEGER DEFAULT 0, last_queries TEXT DEFAULT '', hidden_data INTEGER DEFAULT 0, username TEXT DEFAULT '', requests_left INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0)")
c.execute("CREATE TABLE IF NOT EXISTS blacklist (value TEXT PRIMARY KEY)")
conn.commit()

# Helper functions

def is_admin(uid: int) -> bool:
    return uid == OWNER_ID


def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 29 days - $49", callback_data="buy_month")],
        [InlineKeyboardButton(text="🔒 89 days - $120", callback_data="buy_quarter")],
        [InlineKeyboardButton(text="🔒 Lifetime - $299", callback_data="buy_lifetime")],
        [InlineKeyboardButton(text="🧊 Hide my data - $100", callback_data="buy_hide_data")]
    ])


def is_subscribed(uid: int) -> (bool, int):
    if is_admin(uid):
        return True, 0
    c.execute("SELECT subs_until, free_used FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    now = int(time.time())
    if not row:
        c.execute("INSERT INTO users (id, subs_until, free_used) VALUES (?,?,?)", (uid, 0, 0))
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
    timestamps = [int(t) for t in row[0].split(',') if t]
    timestamps.append(now)
    # Keep only within window
    timestamps = [t for t in timestamps if now - t <= FLOOD_WINDOW]
    c.execute("UPDATE users SET last_queries=? WHERE id=?", (','.join(map(str, timestamps)), uid))
    conn.commit()
    return len(timestamps) > FLOOD_LIMIT or (len(timestamps) >= 2 and timestamps[-1] - timestamps[-2] < FLOOD_INTERVAL)

# State definitions
class AdminStates(StatesGroup):
    wait_user_id = State()
    wait_request_amount = State()
    wait_username = State()

# Admin commands
@dp.message(F.text == "/admin322")
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Give Requests", callback_data="give_requests")],
        [InlineKeyboardButton(text="🚫 Block User", callback_data="block_user")]
    ])
    await message.answer("<b>Admin Panel:</b>", reply_markup=kb)

@dp.callback_query(F.data == "give_requests")
async def give_requests(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Access denied.", show_alert=True)
    await callback.message.answer("Enter Telegram user ID:")
    await state.set_state(AdminStates.wait_user_id)
    await callback.answer()

@dp.message(AdminStates.wait_user_id)
async def process_user_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID must be a number.")
    await state.update_data(user_id=int(message.text))
    await message.answer("Enter number of requests (1-10):")
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def process_request_count(message: Message, state: FSMContext):
    if not message.text.isdigit() or not 1 <= int(message.text) <= 10:
        return await message.answer("Enter a number between 1 and 10.")
    data = await state.get_data()
    uid_set, cnt = data["user_id"], int(message.text)
    c.execute("SELECT id FROM users WHERE id=?", (uid_set,))
    if not c.fetchone():
        c.execute("INSERT INTO users (id, subs_until, free_used, trial_expired, last_queries, hidden_data, username, requests_left, is_blocked) VALUES (?,?,?,?,?,?,?,?,?)", (uid_set,0,0,0,'',0,'',cnt,0))
    else:
        c.execute("UPDATE users SET requests_left=? WHERE id=?", (cnt, uid_set))
    conn.commit()
    await message.answer(f"Assigned {cnt} requests to user {uid_set}.")
    await state.clear()

@dp.callback_query(F.data == "block_user")
async def block_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("Access denied.", show_alert=True)
    await callback.message.answer("Enter username to block (without @):")
    await state.set_state(AdminStates.wait_username)
    await callback.answer()

@dp.message(AdminStates.wait_username)
async def process_block_username(message: Message, state: FSMContext):
    uname = message.text.strip().lstrip("@")
    c.execute("UPDATE users SET is_blocked=1 WHERE username=?", (uname,))
    conn.commit()
    await message.answer(f"@{uname} has been blocked.")
    await state.clear()

# /start handler
@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    now = int(time.time())
    c.execute("SELECT subs_until, free_used FROM users WHERE id=?", (uid,))
    row = c.fetchone()
    if not row:
        default_sub = now + 100*365*24*60*60 if is_admin(uid) else 0
        c.execute("INSERT INTO users (id, subs_until, free_used, trial_expired) VALUES (?,?,?,?)", (uid, default_sub, 0, 0))
        conn.commit()
    if is_admin(uid):
        msg = "<b>As admin, you have unlimited free searches.</b>"
    else:
        c.execute("SELECT free_used FROM users WHERE id=?", (uid,))
        used = c.fetchone()[0]
        msg = f"<b>You have {TRIAL_LIMIT - used} free searches left.</b>" if used < TRIAL_LIMIT else "<b>Your trial has ended.</b>"
    await message.answer(
        "👾 Welcome to n3l0x 👾\n"
        "n3l0x — a service for searching various information.\n"
        "n3лoх — has a database of 20 billion records.\n"
        "n3лoх — can find your mom.\n"
        "n3лoх — and you’re a loser if you’re still not using n3лoх.\n\n"
        f"{msg}",
        reply_markup=sub_keyboard()
    )

# Search handler
@dp.message()
async def search_handler(message: Message):
    uid = message.from_user.id
    query = message.text.strip()
    # Block admin data queries
    if query in ADMIN_HIDDEN:
        return await message.answer("Ты че собака, этого кента нельзя пробивать.")
    # Flood control
    if not is_admin(uid) and check_flood(uid):
        return await message.answer("⛔ Flood detected. Try again later.")
    # Blacklist check
    c.execute("SELECT 1 FROM blacklist WHERE value=?", (query,))
    if c.fetchone():
        return await message.answer("🔒 Access denied. Data is encrypted.")
    # Subscription check
    subscribed, used = is_subscribed(uid)
    if not is_admin(uid) and not subscribed and used >= TRIAL_LIMIT:
        c.execute("UPDATE users SET trial_expired=1 WHERE id=?", (uid,))
        conn.commit()
        return await message.answer("🔐 Your trial is over. Please subscribe.", reply_markup=sub_keyboard())
    # Call API
    await message.answer(f"🕷️ Connecting to nodes...\n🧬 Recon on <code>{query}</code>")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.usersbox.ru/v1/search?q={query}", headers={"Authorization":USERSBOX_API_KEY}) as resp:
            if resp.status != 200:
                return await message.answer(f"⚠️ Error: {resp.status}")
            result = await resp.json()
    if result.get("status") != "success" or result.get("data", {}).get("count", 0) == 0:
        return await message.answer("📡 No match found in any database.")
    def beautify_key(key):
        mapping = {"full_name": "Имя", "phone": "Телефон", "inn": "ИНН", "email": "Email", "first_name": "Имя",
                   "last_name": "Фамилия", "middle_name": "Отчество", "birth_date": "Дата рождения",
                   "gender": "Пол", "passport_series": "Серия паспорта", "passport_number": "Номер паспорта",
                   "passport_date": "Дата выдачи паспорта"}
        return mapping.get(key, key)
    blocks = []
    for item in result["data"]["items"]:
        hdr = f"☠ {item.get('source', {}).get('database', '?')}"
        lines = []
        for hit in item.get("hits", {}).get("items", []):
            for k, v in hit.items():
                if not v:
                    continue
                val = ", ".join(str(i) for i in (v if isinstance(v, list) else [v]))
                lines.append(f"<div class='row'><span class='key'>{beautify_key(k)}:</span><span class='val'>{val}</span></div>")
        if lines:
            blocks.append(f"<div class='block'><div class='header'>{hdr}</div>{''.join(lines)}</div>")
    html = f"""
<html>
<head>
<meta charset='UTF-8'>
<title>n3лoх Intelligence Report</title>
<style>
body {{ background-color:#0a0a0a; color:#f0f0f0; font-family:'Courier New',monospace; padding:20px; display:flex; flex-direction:column; align-items:center; }}
.block {{ background-color:#111; border:1px solid #333; border-radius:10px; padding:15px; margin-bottom:20px; width:100%; max-width:800px; box-shadow:0 0 10px #00ffcc55; }}
.header {{ font-size:18px; color:#00ffcc; margin-bottom:10px; font-weight:bold; }}
.row {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px dotted #444; }}
.key {{ color:#66ff66; font-weight:bold; min-width:40%; }}
.val {{ color:#ff4de6; font-weight:bold; word-break:break-word; text-align:right; }}
@media(max-width:600px) {{ .row {{ flex-direction:column; align-items:flex-start; }} .val {{ text-align:left; }} }}
</style>
</head>
<body>
<h1 style='color:#00ffcc;'>n3лoх Intelligence Report</h1>
{''.join(blocks)}
</body>
</html>
"""
    filename = f"{query}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
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
        payload = f"n3лoх_{call.from_user.id}_{plan}_{int(time.time())}"
        pay_url = f"https://t.me/CryptoBot?start=merchant_{CRYPTOPAY_TOKEN}_{tariff['price']}_{BASE_CURRENCY}_{payload}"
        msg = (f"💳 You chose <b>{plan}</b> plan for <b>${tariff['price']}</b>.\n"
               f"Click below to proceed with payment in {BASE_CURRENCY}:\n"
               f"<a href='{pay_url}'>Pay via CryptoBot</a>")
        await call.message.answer(msg, disable_web_page_preview=True)

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
