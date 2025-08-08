import os
import logging
import time
import tempfile
import sqlite3
import asyncio
import re
import html as html_lib
from urllib.parse import urlparse
from datetime import datetime

import aiohttp
from aiohttp import web, ClientError
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, BotCommand,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

logging.basicConfig(level=logging.INFO)

# === Настройки ===
BOT_TOKEN           = os.getenv('BOT_TOKEN')
USERSBOX_API_KEY    = os.getenv('USERSBOX_API_KEY')
CRYPTOPAY_API_TOKEN = os.getenv('CRYPTOPAY_API_TOKEN')
OWNER_ID            = int(os.getenv('OWNER_ID', '0'))
BASE_CURRENCY       = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL         = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET      = os.getenv('WEBHOOK_SECRET')
DB_PATH             = os.getenv('DATABASE_PATH', 'n3lox_users.db')
PORT                = int(os.getenv('PORT', '8080'))

# === Константы ===
TARIFFS = {
    'month':     {'price': 49,  'days': 29,   'title': '29 дней – $49'},
    'quarter':   {'price': 120, 'days': 89,   'title': '89 дней – $120'},
    'lifetime':  {'price': 299, 'days': 9999, 'title': 'Пожизненно – $299'},
    'hide_data': {'price': 100, 'days': 0,    'title': 'Скрыть данные – $100'},
}
TRIAL_LIMIT    = 3
FLOOD_WINDOW   = 15
FLOOD_LIMIT    = 10
FLOOD_INTERVAL = 3
PAGE_SIZE      = 10   # пользователей на страницу в списках
AUTO_COLLAPSE_THRESHOLD = 20  # >N строк — сворачиваем группу по умолчанию

# === Подключение к БД ===
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA synchronous=NORMAL;")
c = conn.cursor()

# Таблицы
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    subs_until    INTEGER,
    free_used     INTEGER,
    trial_expired INTEGER DEFAULT 0,
    last_queries  TEXT DEFAULT '',
    hidden_data   INTEGER DEFAULT 0,
    username      TEXT DEFAULT '',
    requests_left INTEGER DEFAULT 0,
    is_blocked    INTEGER DEFAULT 0,
    boot_ack_ts   INTEGER DEFAULT 0
)""")
c.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
c.execute("CREATE INDEX IF NOT EXISTS idx_users_isblocked ON users(is_blocked)")
c.execute("""
CREATE TABLE IF NOT EXISTS payments (
    payload TEXT PRIMARY KEY,
    user_id INTEGER,
    plan    TEXT,
    paid_at INTEGER
)""")
c.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
)""")
c.execute("""
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
)""")
conn.commit()

# BOOT_TS — метка текущего запуска
BOOT_TS = str(int(time.time()))
c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('BOOT_TS', ?)", (BOOT_TS,))
conn.commit()

# === Админ-запрещённые запросы ===
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович','10.07.1999','10.07.99',
    '380636659255','0636659255','+380636659255',
    '+380683220001','0683220001','380683220001',
    'bodia.kohan322@gmail.com','vitalik322vitalik@gmail.com'
]

# === Бот / FSM ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

class AdminStates(StatesGroup):
    wait_grant_amount        = State()
    wait_blacklist_values    = State()
    wait_unblacklist_values  = State()

# === Утилиты ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['month']['title'],     callback_data='buy_month')],
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['quarter']['title'],   callback_data='buy_quarter')],
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['lifetime']['title'],  callback_data='buy_lifetime')],
        [InlineKeyboardButton(text='🧊 ' + TARIFFS['hide_data']['title'], callback_data='buy_hide_data')],
    ])

def start_keyboard() -> ReplyKeyboardMarkup:
    # Большая кнопка /start
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/start")]],
        resize_keyboard=True, one_time_keyboard=True, input_field_placeholder="Нажмите /start, чтобы активировать сессию"
    )

def need_start(uid: int) -> bool:
    row = c.execute("SELECT boot_ack_ts FROM users WHERE id=?", (uid,)).fetchone()
    ack = int(row[0]) if row and row[0] else 0
    return ack < int(BOOT_TS)

async def ask_press_start(chat_id: int):
    await bot.send_message(
        chat_id,
        "♻️ Бот был перезапущен или вы ещё не активировали сессию.\nПожалуйста, нажмите /start.",
        reply_markup=start_keyboard()
    )

def check_flood(uid: int) -> bool:
    c.execute('SELECT last_queries FROM users WHERE id=?', (uid,))
    row = c.fetchone()
    last = row[0] if row else ''
    now = int(time.time())
    times = [int(t) for t in last.split(',') if t] + [now]
    recent = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute('UPDATE users SET last_queries=? WHERE id=?',
              (','.join(map(str, recent)), uid))
    conn.commit()
    return len(recent) > FLOOD_LIMIT or (len(recent) >= 2 and recent[-1] - recent[-2] < FLOOD_INTERVAL)

async def setup_menu_commands():
    user_cmds = [
        BotCommand(command="start",  description="Запуск"),
        BotCommand(command="status", description="Статус подписки и лимитов"),
        BotCommand(command="help",   description="Справка"),
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeAllPrivateChats())
    if OWNER_ID:
        admin_cmds = user_cmds + [BotCommand(command="admin322", description="Панель администратора")]
        try:
            await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=OWNER_ID))
        except Exception as e:
            logging.warning(f"Не удалось выставить команды для OWNER_ID {OWNER_ID}: {e}")

# ---------- НОРМАЛИЗАЦИЯ ТЕЛЕФОНОВ ----------
_phone_clean_re = re.compile(r"[^\d]+")
def normalize_phone(raw: str) -> str | None:
    if not raw or not any(ch.isdigit() for ch in raw):
        return None
    digits = _phone_clean_re.sub("", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "38" + digits
    if digits.startswith("380") and 11 <= len(digits) <= 13:
        return digits
    if len(digits) == 12 and digits.startswith("380"):
        return digits
    return None

def normalize_query_if_phone(q: str) -> tuple[str, str | None]:
    norm = normalize_phone(q)
    return (norm if norm else q, norm)

# ---------- ССЫЛКИ / HTML ----------
def is_url(s: str) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip()
    return s.startswith("http://") or s.startswith("https://")

def esc(s: str) -> str:
    return html_lib.escape(str(s), quote=True)

def label_for_url(src: str, url: str, key: str | None = None) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        netloc = ""
    s = (src or "").lower()
    if "olx" in s or "olx" in netloc:
        return "🛒 Профиль OLX"
    if "instagram" in s or "instagram.com" in netloc:
        return "📸 Instagram"
    if "t.me" in netloc or "telegram" in s:
        return "✈️ Telegram"
    if "facebook" in s or "facebook.com" in netloc or "fb.com" in netloc:
        return "📘 Facebook"
    if "linkedin" in s or "linkedin.com" in netloc:
        return "💼 LinkedIn"
    if "x.com" in netloc or "twitter.com" in netloc or "twitter" in s:
        return "𝕏 Twitter"
    if "youtube" in s or "youtu.be" in netloc:
        return "▶️ YouTube"
    if "tiktok" in s or "tiktok.com" in netloc:
        return "🎵 TikTok"
    if "github" in s or "github.com" in netloc:
        return "🐙 GitHub"
    if key:
        k = key.lower()
        if "profile" in k:
            return "👤 Профиль"
        if "url" in k or "link" in k:
            return "🌐 Открыть ссылку"
    return "🌐 Открыть ссылку"

def render_value(src: str, key: str, v) -> str:
    if isinstance(v, (list, tuple)):
        parts = [render_value(src, key, item) for item in v if item not in (None, "", [], {})]
        if not parts:
            return ""
        return '<div class="val-grid">' + "".join(f'<div class="val-item">{p}</div>' for p in parts) + '</div>'
    if isinstance(v, dict):
        inner = ", ".join(f"{esc(k)}: {esc(val)}" for k, val in v.items())
        return f'<span class="mono">{inner}</span>'
    if isinstance(v, str):
        vs = v.strip()
        if is_url(vs):
            label = label_for_url(src, vs, key)
            return f'<a class="btn neon" href="{esc(vs)}" target="_blank" rel="noopener">{esc(label)}</a>'
        return f"<span>{esc(vs)}</span>"
    return f"<span>{esc(v)}</span>"

# ---------- ГРУППИРОВКА / СОРТИРОВКА ----------
GROUP_ORDER = [
    "Идентификация",
    "Контакты",
    "Документы",
    "Адреса",
    "Аккаунты / Профили",
    "Активность",
    "Прочее",
]

def group_for_key(k: str) -> str:
    k_low = k.lower()
    if any(s in k_low for s in ["full_name","first_name","last_name","middle_name","name","gender","birth","bday","date_of_birth"]):
        return "Идентификация"
    if any(s in k_low for s in [
        "phone","tel","email","mail",
        "telegram","tg","t.me","instagram","insta","facebook","fb","vk","linkedin","twitter","x_","youtube","tiktok",
        "site","website","url","link"
    ]):
        return "Контакты"
    if any(s in k_low for s in ["passport","inn","series","number","doc","document","id_card","tax"]):
        return "Документы"
    if any(s in k_low for s in ["address","region","city","street","addr","oblast","район","область","насел","улиц","index","postcode"]):
        return "Адреса"
    if any(s in k_low for s in ["username","login","profile","account","nick","user_id","uid"]):
        return "Аккаунты / Профили"
    if any(s in k_low for s in ["created","updated","last_login","registered","reg_date","timestamp","date","time"]):
        return "Активность"
    return "Прочее"

SORT_PRIORITY = {
    "Идентификация": ["full_name","last_name","first_name","middle_name","birth_date","gender","name"],
    "Контакты": ["phone","email","telegram","instagram","facebook","vk","linkedin","twitter","x","youtube","tiktok","site","website","url","link"],
    "Документы": ["passport_series","passport_number","passport_date","inn","tax","id_card","doc","document","series","number"],
    "Адреса": ["country","region","oblast","city","street","house","apt","postcode","index","address"],
    "Аккаунты / Профили": ["username","login","profile","account","user_id","uid","nick"],
    "Активность": ["last_login","created","updated","registered","reg_date","timestamp","date","time"],
    "Прочее": []
}

def sort_weight(group: str, key: str) -> tuple[int, str]:
    base = SORT_PRIORITY.get(group, [])
    k = key.lower()
    for i, p in enumerate(base):
        if k == p:
            return (i, k)
    for i, p in enumerate(base):
        if k.startswith(p):
            return (i + 100, k)
    return (1000, k)

# ---------- Пагинация пользователей ----------
def fetch_users_page(page: int):
    offset = page * PAGE_SIZE
    rows = c.execute(
        "SELECT id, COALESCE(NULLIF(username,''), '') as uname, is_blocked, requests_left, free_used, trial_expired "
        "FROM users ORDER BY (uname='' ), id DESC LIMIT ? OFFSET ?",
        (PAGE_SIZE, offset)
    ).fetchall()
    total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return rows, total

def users_list_keyboard(action: str, page: int = 0) -> InlineKeyboardMarkup:
    rows, total = fetch_users_page(page)
    kb_rows = []
    for uid, uname, is_blocked, req_left, fu, te in rows:
        title = f"@{uname}" if uname else f"ID {uid}"
        status_bits = []
        if is_blocked: status_bits.append("🚫")
        if req_left:   status_bits.append(f"🧮{req_left}")
        if te:         status_bits.append("⛔trial")
        if not status_bits: status_bits.append("✅")
        btn_text = f"{title}  {' '.join(status_bits)}"
        kb_rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"select:{action}:{uid}:{page}")])
    nav = []
    max_page = (total - 1) // PAGE_SIZE if total else 0
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list:{action}:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"list:{action}:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="🏠 В админ-меню", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

# === Хендлеры ===

@dp.message(CommandStart())
async def start_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
              (uid,0,0,0))
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?',
                  (message.from_user.username, uid))
    c.execute('UPDATE users SET boot_ack_ts=? WHERE id=?', (int(BOOT_TS), uid))
    conn.commit()

    hd, fu, te = c.execute(
        'SELECT hidden_data,free_used,trial_expired FROM users WHERE id=?', (uid,)
    ).fetchone()
    if hd:
        welcome = '<b>Ваши данные скрыты.</b>'
    elif te:
        welcome = '<b>Триал окончен.</b>'
    else:
        rem = TRIAL_LIMIT - fu
        welcome = f'<b>Осталось {rem} бесплатных запросов.</b>' if rem > 0 else '<b>Триал окончен.</b>'
    await message.answer(f"👾 Добро пожаловать в n3l0x!\n{welcome}", reply_markup=sub_keyboard())

# --- ГЛОБАЛЬНЫЙ ГЕЙТ ДЛЯ КОМАНД (кроме /start) ---
@dp.message(Command('status'))
async def status_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    conn.commit()
    if need_start(uid):
        return await ask_press_start(message.chat.id)

    subs, fu, hd, rl, te, _ = c.execute(
        'SELECT subs_until,free_used,hidden_data,requests_left,trial_expired,boot_ack_ts FROM users WHERE id=?',
        (uid,)
    ).fetchone()
    now = int(time.time())
    if hd:
        return await message.answer('🔒 Ваши данные скрыты.')
    sub = datetime.fromtimestamp(subs).strftime('%Y-%m-%d') if subs > now else 'none'
    free = 0 if te else TRIAL_LIMIT - fu
    await message.answer(f"📊 Подписка: {sub}\nБесплатно осталось: {free}\nРучных осталось: {rl}")

@dp.message(Command('help'))
async def help_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    conn.commit()
    if need_start(uid):
        return await ask_press_start(message.chat.id)
    await message.answer(
        "/start  – запуск/обновление сессии\n"
        "/status – статус и лимиты\n"
        "/help   – справка\n"
        "/admin322 – панель администратора (только у админа)\n"
        "Отправьте любой текст для поиска."
    )

# --- Админ-меню (с гейтом /start) ---
@dp.message(Command('admin322'))
async def admin_menu(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if need_start(message.from_user.id):
        return await ask_press_start(message.chat.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Выдать запросы',    callback_data='give_requests')],
        [InlineKeyboardButton(text='🎟 Дать подписку',     callback_data='grant_sub')],
        [InlineKeyboardButton(text='🧊 Скрыть произвольные данные', callback_data='add_blacklist')],
        [InlineKeyboardButton(text='🗑 Снять скрытие (удалить из ЧС)', callback_data='remove_blacklist')],
        [InlineKeyboardButton(text='🚫 Заблокировать',     callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Разблокировать',    callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Завершить триал',   callback_data='reset_menu')],
    ])
    await message.answer('<b>Панель администратора:</b>', reply_markup=kb)

@dp.callback_query(F.data == 'admin_home')
async def admin_home(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id)
        return await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Выдать запросы',    callback_data='give_requests')],
        [InlineKeyboardButton(text='🎟 Дать подписку',     callback_data='grant_sub')],
        [InlineKeyboardButton(text='🧊 Скрыть произвольные данные', callback_data='add_blacklist')],
        [InlineKeyboardButton(text='🗑 Снять скрытие (удалить из ЧС)', callback_data='remove_blacklist')],
        [InlineKeyboardButton(text='🚫 Заблокировать',     callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Разблокировать',    callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Завершить триал',   callback_data='reset_menu')],
    ])
    await call.message.edit_text('<b>Панель администратора:</b>', reply_markup=kb)
    await call.answer()

# --- ДАТЬ ПОДПИСКУ: выбор плана -> список пользователей ---
@dp.callback_query(F.data == 'grant_sub')
async def grant_sub_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🟢 ' + TARIFFS['month']['title'],    callback_data='sub_plan:month')],
        [InlineKeyboardButton(text='🟣 ' + TARIFFS['quarter']['title'],  callback_data='sub_plan:quarter')],
        [InlineKeyboardButton(text='💎 ' + TARIFFS['lifetime']['title'], callback_data='sub_plan:lifetime')],
        [InlineKeyboardButton(text='🏠 В админ-меню', callback_data='admin_home')],
    ])
    await call.message.answer('Выберите план подписки:', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith('sub_plan:'))
async def sub_plan_pick_users(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    plan = call.data.split(':',1)[1]
    if plan not in ('month','quarter','lifetime'):
        return await call.answer('Неверный план', show_alert=True)
    kb = users_list_keyboard(action=f'sub_{plan}', page=0)
    await call.message.answer(f'👥 Выберите пользователя для начисления подписки ({plan})', reply_markup=kb)
    await call.answer()

# --- Cкрыть/раскрыть произвольные данные (blacklist) ---
@dp.callback_query(F.data == 'add_blacklist')
async def add_blacklist_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await call.message.answer("Вставьте значения через запятую, которые нужно скрыть (ФИО, телефоны, e-mail, даты и т.д.).\nПример:\n<code>Иванов Иван, 380661112233, 10.07.1999, test@example.com</code>")
    await state.set_state(AdminStates.wait_blacklist_values)
    await call.answer()

@dp.message(AdminStates.wait_blacklist_values)
async def add_blacklist_values(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear(); return
    if need_start(msg.from_user.id):
        await state.clear()
        return await ask_press_start(msg.chat.id)
    raw = (msg.text or "").strip()
    if not raw:
        await state.clear()
        return await msg.answer("Пустой ввод. Отменено.")
    values = [v.strip() for v in raw.split(',')]
    values = [v for v in values if v]
    added = 0
    for v in values:
        try:
            c.execute("INSERT OR IGNORE INTO blacklist(value) VALUES(?)", (v,))
            added += c.rowcount
        except:
            pass
    conn.commit()
    await msg.answer(f"✅ В чёрный список добавлено: {added} из {len(values)}.\nЭти значения будут блокироваться при поиске.")
    await state.clear()

@dp.callback_query(F.data == 'remove_blacklist')
async def remove_blacklist_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await call.message.answer("Вставьте значения через запятую, которые нужно удалить из чёрного списка.\nПример:\n<code>Иванов Иван, 380661112233, 10.07.1999</code>")
    await state.set_state(AdminStates.wait_unblacklist_values)
    await call.answer()

@dp.message(AdminStates.wait_unblacklist_values)
async def remove_blacklist_values(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear(); return
    if need_start(msg.from_user.id):
        await state.clear()
        return await ask_press_start(msg.chat.id)
    raw = (msg.text or "").strip()
    if not raw:
        await state.clear()
        return await msg.answer("Пустой ввод. Отменено.")
    values = [v.strip() for v in raw.split(',')]
    values = [v for v in values if v]
    removed = 0
    for v in values:
        try:
            c.execute("DELETE FROM blacklist WHERE value=?", (v,))
            removed += c.rowcount
        except:
            pass
    conn.commit()
    await msg.answer(f"✅ Из чёрного списка удалено: {removed} из {len(values)}.")
    await state.clear()

# === Листинги пользователей (прочие экраны) ===
@dp.callback_query(F.data == 'give_requests')
async def give_requests_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='give', page=0)
    await call.message.answer('👥 Выберите пользователя для выдачи запросов:', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == 'block_user')
async def block_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='block', page=0)
    await call.message.answer('👥 Кого заблокировать?', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == 'unblock_user')
async def unblock_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='unblock', page=0)
    await call.message.answer('👥 Кого разблокировать?', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == 'reset_menu')
async def reset_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔁 Завершить триал у всех', callback_data='reset_all')],
        [InlineKeyboardButton(text='🔍 Завершить триал у конкретного', callback_data='reset_pick')],
        [InlineKeyboardButton(text='🏠 В админ-меню', callback_data='admin_home')],
    ])
    await call.message.answer('Выберите режим завершения триала:', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == 'reset_pick')
async def reset_pick_list(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='reset', page=0)
    await call.message.answer('👥 Выберите пользователя для завершения триала:', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith('list:'))
async def paginate_users(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    _, action, page_s = call.data.split(':', 2)
    page = int(page_s)
    kb = users_list_keyboard(action=action, page=page)
    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except:
        await call.message.answer('Обновил список.', reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith('select:'))
async def user_selected(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    _, action, uid_s, page_s = call.data.split(':', 3)
    uid = int(uid_s)
    row = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    uname = row[0] if row and row[0] else f'ID {uid}'

    if action == 'give':
        await state.update_data(grant_uid=uid)
        await call.message.answer(f'Выбран @{uname if uname!="ID "+str(uid) else uname}.\n🔢 Введите количество запросов (1–100):')
        await state.set_state(AdminStates.wait_grant_amount)

    elif action == 'block':
        c.execute('UPDATE users SET is_blocked=1 WHERE id=?', (uid,)); conn.commit()
        await call.message.answer(f'🚫 Заблокирован @{uname if uname!="ID "+str(uid) else uname}.')

    elif action == 'unblock':
        c.execute('UPDATE users SET is_blocked=0 WHERE id=?', (uid,)); conn.commit()
        await call.message.answer(f'✅ Разблокирован @{uname if uname!="ID "+str(uid) else uname}.')

    elif action == 'reset':
        c.execute('UPDATE users SET free_used=?, trial_expired=1 WHERE id=?', (TRIAL_LIMIT, uid)); conn.commit()
        await call.message.answer(f'🔄 Триал завершён для @{uname if uname!="ID "+str(uid) else uname}.')

    elif action in ('sub_month','sub_quarter','sub_lifetime'):
        plan = action.split('_',1)[1]
        now = int(time.time())
        old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()
        old_until = int(old[0]) if old and old[0] else 0
        new_until = max(now, old_until) + TARIFFS[plan]['days']*86400
        c.execute('INSERT INTO users(id,subs_until) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until',
                  (uid, new_until))
        # Триал считаем завершённым
        c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
        conn.commit()
        until_txt = datetime.fromtimestamp(new_until).strftime('%Y-%m-%d')
        await call.message.answer(f'🎟 Подписка «{plan}» выдана @{uname if uname!="ID "+str(uid) else uname} до {until_txt}.')

    else:
        await call.message.answer('Неизвестное действие.')
    await call.answer()

@dp.message(AdminStates.wait_grant_amount)
async def grant_amount_input(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear(); return
    if need_start(msg.from_user.id):
        await state.clear()
        return await ask_press_start(msg.chat.id)
    if not msg.text.isdigit():
        return await msg.answer('Введите число 1–100.')
    amount = int(msg.text)
    if not (1 <= amount <= 100):
        return await msg.answer('Диапазон 1–100.')
    data = await state.get_data()
    uid = data.get('grant_uid')
    if not uid:
        await state.clear()
        return await msg.answer('⚠️ Пользователь не выбран. Попробуйте снова.')
    c.execute('UPDATE users SET requests_left=? WHERE id=?', (amount, uid))
    conn.commit()
    uname = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    uname = uname[0] if uname and uname[0] else f'ID {uid}'
    await msg.answer(f'✅ Выдано {amount} запросов @{uname if uname!="ID "+str(uid) else uname}.')
    await state.clear()

# === Массовый сброс триала (асинхронно) ===
async def _reset_all_job(chat_id: int, message_id: int | None):
    ids = [row[0] for row in c.execute("SELECT id FROM users").fetchall()]
    total = len(ids)
    affected = 0
    for i in range(0, total, 1000):
        batch = ids[i:i+1000]
        def _update_batch():
            cur = conn.cursor()
            cur.execute("BEGIN")
            cur.executemany("UPDATE users SET free_used=?, trial_expired=1 WHERE id=?",
                            [(TRIAL_LIMIT, _id) for _id in batch])
            cur.execute("COMMIT")
            return cur.rowcount
        changed = await asyncio.to_thread(_update_batch)
        affected += changed
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f"🔄 Массовый сброс… {min(i+1000, total)}/{total}"
            )
        except:
            pass
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
            text=f"✅ Триал завершён у всех. Обновлено записей: {affected}.")
    except:
        await bot.send_message(chat_id, f"✅ Триал завершён у всех. Обновлено записей: {affected}.")

@dp.callback_query(F.data=='reset_all')
async def reset_all(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await call.answer('Запустил массовый сброс…')
    msg = await call.message.answer("🔄 Массовый сброс… 0%")
    asyncio.create_task(_reset_all_job(chat_id=msg.chat.id, message_id=msg.message_id))
    await state.clear()

# === Поиск и HTML (с группировкой, сортировкой, сворачиванием) ===
@dp.message(F.text & ~F.text.startswith('/'))
async def search_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
              (uid,0,0,0))
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?',
                  (message.from_user.username, uid))
    conn.commit()

    # Гейт /start
    if need_start(uid):
        return await ask_press_start(message.chat.id)

    original_q = message.text.strip()
    q_for_api, norm_phone = normalize_query_if_phone(original_q)

    is_blocked, hidden_data, requests_left, free_used, subs_until, trial_expired = c.execute(
        'SELECT is_blocked,hidden_data,requests_left,free_used,subs_until,trial_expired '
        'FROM users WHERE id=?', (uid,)
    ).fetchone()
    now = int(time.time())

    if not is_admin(uid):
        if is_blocked:
            return await message.answer('🚫 Вы заблокированы.')
        if hidden_data:
            return await message.answer('🚫 Ваши данные скрыты.')
        if original_q in ADMIN_HIDDEN or (norm_phone and norm_phone in ADMIN_HIDDEN):
            return await message.answer('🚫 Запрос запрещён.')
        if check_flood(uid):
            return await message.answer('⛔ Слишком часто. Попробуйте позже.')

        if requests_left > 0:
            c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?', (uid,))
            conn.commit()
        else:
            if subs_until > now:
                pass
            else:
                if trial_expired:
                    return await message.answer('🔐 Триал окончен. Подпишитесь.', reply_markup=sub_keyboard())
                if free_used < TRIAL_LIMIT:
                    c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?', (uid,))
                    if free_used + 1 >= TRIAL_LIMIT:
                        c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                    conn.commit()
                else:
                    c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                    conn.commit()
                    return await message.answer('🔐 Триал окончен. Подпишитесь.', reply_markup=sub_keyboard())

    # blacklist по оригиналу и по нормальному телефону
    black_hit = c.execute('SELECT 1 FROM blacklist WHERE value=?', (original_q,)).fetchone()
    if not black_hit and norm_phone:
        black_hit = c.execute('SELECT 1 FROM blacklist WHERE value=?', (norm_phone,)).fetchone()
    if black_hit:
        return await message.answer('🔒 Доступ запрещён.')

    shown_q = norm_phone if norm_phone else original_q
    await message.answer(f"🕷️ Выполняется поиск для <code>{shown_q}</code>…")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.usersbox.ru/v1/search',
                headers={'Authorization': USERSBOX_API_KEY},
                params={'q': q_for_api}, timeout=12
            ) as resp:
                if resp.status != 200:
                    return await message.answer(f'⚠️ API ошибка: {resp.status}')
                data = await resp.json()
    except (ClientError, asyncio.TimeoutError):
        return await message.answer('⚠️ Сетевая ошибка.')

    if data.get('status') != 'success' or data.get('data', {}).get('count', 0) == 0:
        return await message.answer('📡 Совпадений не найдено.')

    def beautify(k):
        m = {
            'full_name':'Имя','phone':'Телефон','inn':'ИНН',
            'email':'Email','first_name':'Имя','last_name':'Фамилия',
            'middle_name':'Отчество','birth_date':'Дата рождения',
            'gender':'Пол','passport_series':'Серия паспорта',
            'passport_number':'Номер паспорта','passport_date':'Дата выдачи'
        }
        return m.get(k, k)

    all_blocks = []
    for itm in data['data']['items']:
        hits = itm.get('hits', {}).get('items', [])
        src  = itm.get('source', {}).get('database', '?')
        if not hits:
            continue

        grouped = {g: [] for g in GROUP_ORDER}
        for h in hits:
            for k, v in h.items():
                if v in (None, "", [], {}):
                    continue
                grp = group_for_key(k)
                key_title = beautify(k)
                val_html = render_value(src, k, v)
                grouped[grp].append((key_title, val_html))

        group_html = []
        for grp in GROUP_ORDER:
            items = grouped.get(grp) or []
            if not items:
                continue
            # сортировка
            items.sort(key=lambda kv: sort_weight(grp, kv[0]))
            rows = "".join(f"<tr><td>{esc(k)}</td><td>{val}</td></tr>" for k, val in items)
            # автосворачивание: первые две группы открыты, но если строк больше порога — закрыты
            open_default = (grp in GROUP_ORDER[:2]) and (len(items) <= AUTO_COLLAPSE_THRESHOLD)
            open_attr = " open" if open_default else ""
            group_html.append(f"""
<details class="group"{open_attr}>
  <summary class="g-summary"><span class="caret"></span><span class="g-title">{esc(grp)}</span><span class="g-count">{len(items)}</span></summary>
  <div class="g-body">
    <table>
      <thead><tr><th>Поле</th><th>Значение</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</details>""")

        if not group_html:
            continue

        all_blocks.append(f"""
<div class="block">
  <h2 class="graffiti">{esc(src)}</h2>
  {''.join(group_html)}
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>n3l0x Intelligence Report</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Inconsolata&display=swap');
    :root {{
      --bg: #0b0c10;
      --panel: #1f2833;
      --text: #c5c6c7;
      --accent: #66fcf1;
      --accent2: #45a29e;
      --muted: #0f141a;
      --muted2:#16202a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:'Inconsolata', monospace; line-height:1.45; }}
    h1 {{
      text-align:center; padding:20px 0; margin:0;
      color:var(--accent); font-family:'Orbitron', sans-serif;
      text-shadow:0 0 8px rgba(102,252,241,.8);
      background:var(--panel); border-bottom:1px solid var(--accent);
    }}
    .report {{
      display:grid; grid-template-columns:repeat(auto-fit, minmax(320px,1fr));
      gap:18px; padding:18px;
    }}
    .block {{
      background:var(--panel); border:1px solid var(--accent);
      border-radius:12px; box-shadow:0 0 10px rgba(102,252,241,.18); overflow:hidden;
    }}
    .block .graffiti {{
      margin:0; padding:14px; font-family:'Orbitron', sans-serif;
      color:var(--accent); text-shadow:0 0 6px rgba(102,252,241,.7);
      font-size:1.05em; text-align:center; background:var(--bg);
      border-bottom:1px solid var(--accent);
    }}
    /* группы */
    .group {{ border-top:1px dashed rgba(102,252,241,.25); }}
    .g-summary {{
      list-style:none; cursor:pointer; user-select:none;
      display:flex; align-items:center; gap:10px;
      padding:10px 12px; background:rgba(102,252,241,.05);
      border-bottom:1px dashed rgba(102,252,241,.2);
    }}
    .g-summary::-webkit-details-marker {{ display:none; }}
    .g-title {{
      color:var(--accent2); font-weight:700; letter-spacing:.04em;
      text-transform:uppercase; font-size:.9em;
    }}
    .g-count {{
      margin-left:auto; color:var(--accent); font-family:'Orbitron', sans-serif;
      font-size:.85em; padding:2px 8px; border:1px solid var(--accent);
      border-radius:999px; background:rgba(102,252,241,.08);
    }}
    .caret {{
      width:0; height:0; border-left:6px solid var(--accent);
      border-top:5px solid transparent; border-bottom:5px solid transparent;
      transform:rotate(0deg); transition:transform .15s ease;
    }}
    details[open] .caret {{ transform:rotate(90deg); }}
    .g-body {{ padding:10px 12px 16px; }}

    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ padding:8px; text-align:left; font-size:.92em; vertical-align:top; }}
    thead th {{
      background:var(--accent2); color:var(--bg);
      font-weight:700; letter-spacing:.05em;
      border:1px solid var(--bg);
    }}
    td {{
      background:var(--muted); color:var(--text);
      border:1px solid #1f2a33; overflow-wrap:anywhere; word-break:break-word;
    }}
    tr:nth-child(even) td {{ background:var(--muted2); }}

    .mono {{ font-family:'Inconsolata', monospace; opacity:.95; }}

    .val-grid {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .val-item {{ flex:0 0 auto; }}

    .btn {{
      display:inline-block; text-decoration:none; padding:7px 10px; border-radius:8px;
      border:1px solid var(--accent); background:linear-gradient(90deg, rgba(102,252,241,.15), rgba(69,162,158,.15));
      color:var(--accent); font-weight:600; box-shadow:0 0 8px rgba(102,252,241,.2) inset, 0 0 6px rgba(102,252,241,.15);
      transition:transform .08s ease, box-shadow .12s ease, background .12s ease; white-space:nowrap;
    }}
    .btn:hover {{ transform:translateY(-1px); box-shadow:0 0 10px rgba(102,252,241,.35), 0 0 10px rgba(102,252,241,.35) inset; }}
    .btn.neon {{ text-shadow:0 0 6px rgba(102,252,241,.6); }}

    @media (max-width: 600px) {{
      h1 {{ font-size: 1.3em; }}
      .graffiti {{ font-size: 1em; }}
      th, td {{ font-size: .9em; }}
    }}
  </style>
</head>
<body>
  <h1>n3l0x Intelligence Report</h1>
  <div class="report">{''.join(all_blocks)}</div>
</body>
</html>"""

    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html', dir='/tmp', encoding='utf-8') as tf:
        tf.write(html)
        path = tf.name

    await message.answer_document(FSInputFile(path, filename=f"{shown_q}.html"))
    try:
        os.unlink(path)
    except:
        pass

# === Покупка (публичная) ===
@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_',1)[1]
    if plan not in TARIFFS:
        return await callback.answer('Неизвестный план', show_alert=True)
    price = TARIFFS[plan]['price']
    payload = f"pay_{callback.from_user.id}_{plan}_{int(time.time())}"
    body = {
        'asset': BASE_CURRENCY, 'amount': str(price),
        'description': f"n3l0x: {plan}",
        'payload': payload,
        'allow_comments': False, 'allow_anonymous': True,
        'expires_in': 1800
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                'https://pay.crypt.bot/api/createInvoice',
                headers={'Crypto-Pay-API-Token': CRYPTOPAY_API_TOKEN},
                json=body, timeout=10
            ) as r:
                data = await r.json()
    except:
        return await callback.message.answer('⚠️ Ошибка платежного сервиса.')
    if not data.get('ok'):
        return await callback.message.answer(f"⚠️ Ошибка: {data}")
    url = data['result'].get('bot_invoice_url') or data['result'].get('pay_url')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 Оплатить', url=url)]
    ])
    await callback.message.answer(f"💳 План «{plan}» – ${price}", reply_markup=kb)
    await callback.answer()

# === Вебхуки ===
async def health(request):
    return web.Response(text='OK')

async def cryptopay_webhook(request: web.Request):
    try:
        js = await request.json()
    except:
        return web.json_response({'ok': True})
    inv = js.get('invoice') or js.get('payload') or {}
    if inv.get('status') == 'paid' and inv.get('payload'):
        parts = inv['payload'].split('_')
        if parts[0] == 'pay' and len(parts) >= 4:
            uid, plan = int(parts[1]), parts[2]
            now = int(time.time())
            if plan == 'hide_data':
                c.execute('UPDATE users SET hidden_data=1 WHERE id=?', (uid,))
            else:
                old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()
                old_until = int(old[0]) if old and old[0] else 0
                ns = max(now, old_until) + TARIFFS[plan]['days']*86400
                c.execute(
                    'INSERT INTO users(id,subs_until,free_used) VALUES(?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until, free_used=0, trial_expired=1',
                    (uid, ns, 0)
                )
            c.execute(
                'INSERT OR REPLACE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',
                (inv['payload'], uid, plan, now)
            )
            conn.commit()
            try:
                await bot.send_message(uid, f"✅ Оплата принята: {plan}")
            except:
                pass
    return web.json_response({'ok': True})

# === Startup/Shutdown ===
async def on_startup(app):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    await setup_menu_commands()
    logging.info("Меню команд установлено. BOOT_TS=%s", BOOT_TS)

async def on_shutdown(app):
    try:
        await bot.delete_webhook()
    finally:
        conn.close()

app = web.Application()
app.router.add_get('/health', health)
app.router.add_route('*','/webhook', SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET))
app.router.add_post('/cryptopay', cryptopay_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=PORT)
