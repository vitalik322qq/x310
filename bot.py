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

# === Init ===
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
conn = sqlite3.connect("/app/data/n3l0x_users.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    subs_until INTEGER,
    free_used INTEGER,
    trial_expired INTEGER DEFAULT 0,
    last_queries TEXT DEFAULT '',
    hidden_data INTEGER DEFAULT 0,
    username TEXT DEFAULT '',
    requests_left INTEGER DEFAULT 0,
    is_blocked INTEGER DEFAULT 0
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS logs (
    user_id INTEGER,
    query TEXT,
    timestamp INTEGER
)
""")
conn.commit()

class AdminStates(StatesGroup):
    wait_user_id = State()
    wait_request_amount = State()
    wait_username = State()
    wait_unblock_username = State()

def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 29 days - $49", callback_data="buy_month")],
        [InlineKeyboardButton(text="🔒 89 days - $120", callback_data="buy_quarter")],
        [InlineKeyboardButton(text="🔒 Lifetime - $299", callback_data="buy_lifetime")],
        [InlineKeyboardButton(text="🧊 Hide my data - $100", callback_data="buy_hide_data")]
    ])

@dp.message(F.text == "/admin322")
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Выдать запросы", callback_data="give_requests")],
        [InlineKeyboardButton(text="❌ Заблокировать", callback_data="block_user")],
        [InlineKeyboardButton(text="✅ Разблокировать", callback_data="unblock_user")]
    ])
    await message.answer("<b>Админ-панель:</b>", reply_markup=keyboard)

@dp.callback_query(F.data == "give_requests")
async def handle_give_requests(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔑 Введите Telegram ID пользователя:")
    await state.set_state(AdminStates.wait_user_id)
    await callback.answer()

@dp.message(AdminStates.wait_user_id)
async def get_user_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введите корректный ID")
    await state.update_data(user_id=int(message.text))
    await message.answer("🔢 Введите количество запросов (1-10):")
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def set_requests(message: Message, state: FSMContext):
    if not message.text.isdigit() or not 1 <= int(message.text) <= 10:
        return await message.answer("❌ Введите число от 1 до 10")
    data = await state.get_data()
    user_id = data["user_id"]
    count = int(message.text)
    c.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not c.fetchone():
        c.execute("INSERT INTO users (id, requests_left) VALUES (?, ?)", (user_id, count))
    else:
        c.execute("UPDATE users SET requests_left = ? WHERE id = ?", (count, user_id))
    conn.commit()
    await message.answer(f"✅ Пользователю {user_id} выдано {message.text} запросов")
    await state.clear()

@dp.callback_query(F.data == "block_user")
async def handle_block(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("👤 Введите username (без @):")
    await state.set_state(AdminStates.wait_username)
    await callback.answer()

@dp.message(AdminStates.wait_username)
async def block_user(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    c.execute("UPDATE users SET is_blocked = 1 WHERE username = ?", (username,))
    conn.commit()
    await message.answer(f"🚫 Пользователь @{username} заблокирован")
    await state.clear()

@dp.callback_query(F.data == "unblock_user")
async def handle_unblock(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("👤 Введите username для разблокировки (без @):")
    await state.set_state(AdminStates.wait_unblock_username)
    await callback.answer()

@dp.message(AdminStates.wait_unblock_username)
async def unblock_user(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    c.execute("UPDATE users SET is_blocked = 0 WHERE username = ?", (username,))
    conn.commit()
    await message.answer(f"✅ Пользователь @{username} разблокирован")
    await state.clear()

@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    now = int(time.time())
    username = message.from_user.username or ""
    c.execute("SELECT id FROM users WHERE id = ?", (uid,))
    if not c.fetchone():
        c.execute("INSERT INTO users (id, subs_until, free_used, trial_expired, username) VALUES (?, ?, ?, ?, ?)", (uid, 0, 0, 0, username))
    else:
        c.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
    conn.commit()
    await message.answer("Добро пожаловать! Ваша учетная запись активна.")

@dp.message(F.text == "/history")
async def user_history(message: Message):
    uid = message.from_user.id
    c.execute("SELECT query, timestamp FROM logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5", (uid,))
    rows = c.fetchall()
    if not rows:
        return await message.answer("📭 История запросов пуста")
    text = "📜 Последние запросы:\n\n" + "\n".join([f"🔹 {q} — {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')}" for q, ts in rows])
    await message.answer(text)

# В других местах, где обрабатываются запросы пользователей, вставьте:
# c.execute("INSERT INTO logs (user_id, query, timestamp) VALUES (?, ?, ?)", (uid, query, int(time.time())))
# conn.commit()
