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

from bs4 import BeautifulSoup, NavigableString
from pathlib import Path
import base64

logging.basicConfig(level=logging.INFO)

# === Настройки ===
BOT_TOKEN           = os.getenv('BOT_TOKEN')
USERSBOX_API_KEY    = os.getenv('USERSBOX_API_KEY')
CRYPTOPAY_API_TOKEN = os.getenv('CRYPTOPAY_API_TOKEN')
OWNER_ID            = int(os.getenv('OWNER_ID', '0'))
BASE_CURRENCY       = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL         = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET      = os.getenv('WEBHOOK_SECRET')
DB_PATH = os.getenv('DATABASE_PATH')  # путь к файлу БД (можно не задавать)
# Если volume смонтирован в /app/data — используем его по умолчанию
if not DB_PATH:
    DB_PATH = '/app/data/n3l0x.sqlite' if os.path.isdir('/app/data') else 'n3l0x.sqlite'
PORT                = int(os.getenv('PORT', '8080'))

# Если volume смонтирован в /data — используем его по умолчанию
if not DB_PATH:
    DB_PATH = '/data/n3l0x.sqlite' if os.path.isdir('/data') else 'n3l0x.sqlite'

# Автоподтверждать сессию после ребута (чтобы не требовать /start)
AUTO_ACK_ON_BOOT = int(os.getenv('AUTO_ACK_ON_BOOT', '1'))

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

# === Шаблон отчёта «как у них» (встроенный) ===
BRAND_NAME = "P3rsonaScan"

EMBEDDED_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UserBox Report</title>
  <link rel="icon" href="data:image/svg+xml,<svg/>">
  <style>
    :root{
      --bg:#0b1220; --panel:#101829; --muted:#0d1526; --text:#dbe6ff;
      --accent:#0AEFFF; --accent2:#00E68E; --line:rgba(10,239,255,.25)
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter, Segoe UI, Roboto, Arial, sans-serif}
    header{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;padding:14px 18px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0c1426,#0a1120)}
    .logo-slot{grid-column:2;display:flex;justify-content:center;align-items:center;min-height:48px}
    .logo-slot svg,.logo-slot img{display:block;height:44px;width:auto;max-width:100%}
    .header_query{grid-column:3;justify-self:end;opacity:.9;font-weight:600}
    .wrap{display:grid;grid-template-columns:260px 1fr}
    nav{border-right:1px solid var(--line);background:var(--panel);min-height:calc(100vh - 72px)}
    .navigation_ul{list-style:none;margin:0;padding:10px}
    .navigation_link{display:block;padding:8px 10px;margin:4px 0;background:var(--muted);color:var(--text);text-decoration:none;border:1px solid rgba(255,255,255,.06);border-radius:10px}
    main{padding:18px}
    .db{border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:0 0 18px;background:var(--panel);box-shadow:0 6px 24px rgba(0,0,0,.35)}
    .db_header{padding:12px 14px;font-weight:800;letter-spacing:.3px;color:var(--accent);border-bottom:1px dashed var(--line);background:#0b1424}
    .db_cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;padding:12px}
    .card{background:var(--muted);border:1px solid rgba(255,255,255,.06);border-radius:12px;overflow:hidden}
    .table-main{display:block}
    .row{display:grid;grid-template-columns:160px 1fr;border-bottom:1px solid rgba(255,255,255,.04)}
    .row:last-child{border-bottom:none}
    .row_left{padding:10px 12px;color:#9fb0d0;background:#0a1322;font-weight:700;border-right:1px solid rgba(255,255,255,.04)}
    .row_right{padding:10px 12px;word-break:break-word}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    @media(max-width:920px){.wrap{grid-template-columns:1fr}nav{display:none}.logo-slot svg,.logo-slot img{height:36px}}
    @media(max-width:480px){.logo-slot svg,.logo-slot img{height:28px}}
  </style>
</head>
<body>
  <header>
    <div class="logo-slot" aria-label="brand"></div>
    <div class="header_query"></div>
  </header>
  <div class="wrap">
    <nav><ul class="navigation_ul"></ul></nav>
    <main><div class="databases"></div></main>
  </div>
</body>
</html>"""

EMBEDDED_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="260" viewBox="0 0 1200 260">
  <style>.logo{font:700 128px/1 'Inter','Segoe UI','Roboto','Arial',sans-serif;letter-spacing:.5px}</style>
  <text class="logo" x="20" y="170" fill="#EAF2FF">
    P<tspan fill="#00E68E">3</tspan>rsona<tspan fill="#0AEFFF">Scan</tspan>
  </text>
</svg>"""

EMBEDDED_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <rect width="256" height="256" rx="28" fill="#0B1220"/>
  <line x1="28" y1="124" x2="228" y2="124" stroke="#0AEFFF" stroke-width="4" opacity=".7"/>
  <path d="M36 44 h36 v12 h-24 v36 h-12z M220 200 h-36 v-12 h24 v-36 h12z" fill="#0AEFFF" opacity=".85"/>
  <text x="42" y="180" font-family="Inter,Segoe UI,Roboto,Arial,sans-serif" font-size="144" font-weight="800" fill="#EAF2FF">
    P<tspan fill="#00E68E">3</tspan>
  </text>
</svg>"""
EMBEDDED_FAVICON_B64 = base64.b64encode(EMBEDDED_FAVICON_SVG.encode('utf-8')).decode('ascii')

TEMPLATE_HTML = EMBEDDED_TEMPLATE

# === Подключение к БД ===
db_dir = os.path.dirname(DB_PATH) or '.'
os.makedirs(db_dir, exist_ok=True)

# autocommit; меньше шансов потерять транзакцию при резком ребуте
conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
with conn:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA wal_autocheckpoint=1000;")

c = conn.cursor()

# Таблицы
with conn:
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
    c.execute("CREATE INDEX IF NOT EXISTS idx_users_subs_until ON users(subs_until)")

    c.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        payload TEXT PRIMARY KEY,
        user_id INTEGER,
        plan    TEXT,
        paid_at INTEGER
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON payments(paid_at)")

    # Сохраняем созданные инвойсы (pending), чтобы сверять по вебхуку и при reconcile
    c.execute("""
    CREATE TABLE IF NOT EXISTS invoices (
        invoice_id TEXT PRIMARY KEY,
        payload    TEXT,
        user_id    INTEGER,
        plan       TEXT,
        amount     REAL,
        asset      TEXT,
        status     TEXT,
        created_at INTEGER
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_invoices_payload ON invoices(payload)")

    c.execute("""
    CREATE TABLE IF NOT EXISTS blacklist (
        value TEXT PRIMARY KEY
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

# BOOT_TS — метка текущего запуска
BOOT_TS = int(time.time())
with conn:
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('BOOT_TS', ?)", (str(BOOT_TS),))

# Автоматически подтверждаем «/start» после ребута, чтобы не блокировать команды
if AUTO_ACK_ON_BOOT:
    with conn:
        c.execute("UPDATE users SET boot_ack_ts = ? WHERE boot_ack_ts < ?", (BOOT_TS, BOOT_TS))

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

# ---------- Админ-UI инфраструктура (ЯКОРЬ + секции) ----------
ADMIN_ANCHORS: dict[int, int] = {}        # chat_id -> message_id
ADMIN_OPEN_SECTIONS: dict[int, set] = {}  # admin_id -> {"subs","bl","mod","utils"}

def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def grid(buttons: list[InlineKeyboardButton], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    rows, row = [], []
    for b in buttons:
        row.append(b)
        if len(row) == cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows

async def admin_render(target: Message | CallbackQuery, text: str,
                       kb: InlineKeyboardMarkup | None = None, *, reset: bool = False):
    """
    Рендерим админ-экран в одном «якорном» сообщении.
    reset=True — создаём новый якорь (и удаляем старый).
    """
    if isinstance(target, Message):
        chat_id = target.chat.id
    else:
        chat_id = target.message.chat.id

    old_anchor = ADMIN_ANCHORS.get(chat_id)

    if reset or not old_anchor:
        if old_anchor:
            try:
                await bot.delete_message(chat_id, old_anchor)
            except:
                pass
        msg = await bot.send_message(chat_id, text, reply_markup=kb)
        ADMIN_ANCHORS[chat_id] = msg.message_id
        return

    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=old_anchor, text=text, reply_markup=kb)
    except:
        msg = await bot.send_message(chat_id, text, reply_markup=kb)
        ADMIN_ANCHORS[chat_id] = msg.message_id

def admin_kb_home(uid: int) -> InlineKeyboardMarkup:
    opened = ADMIN_OPEN_SECTIONS.setdefault(uid, {"subs"})
    subs_open = "subs" in opened
    bl_open   = "bl"   in opened
    mod_open  = "mod"  in opened
    util_open = "utils" in opened

    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text=("▼ " if subs_open else "► ") + "Подписки/Лимиты", callback_data="toggle:subs")])
    if subs_open:
        rows += grid([
            InlineKeyboardButton(text="🎟 Выдать подписку", callback_data="grant_sub"),
            InlineKeyboardButton(text="📊 Выдать запросы",  callback_data="give_requests"),
            InlineKeyboardButton(text="⛔ Завершить триал",  callback_data="reset_menu"),
        ], cols=2)

    rows.append([InlineKeyboardButton(text=("▼ " if bl_open else "► ") + "Чёрный список / Скрытие", callback_data="toggle:bl")])
    if bl_open:
        rows += grid([
            InlineKeyboardButton(text="🧊 Добавить значения",   callback_data="add_blacklist"),
            InlineKeyboardButton(text="🗑 Удалить значения",     callback_data="remove_blacklist"),
        ], cols=2)

    rows.append([InlineKeyboardButton(text=("▼ " if mod_open else "► ") + "Модерация пользователей", callback_data="toggle:mod")])
    if mod_open:
        rows += grid([
            InlineKeyboardButton(text="🚫 Заблокировать",  callback_data="block_user"),
            InlineKeyboardButton(text="✅ Разблокировать",  callback_data="unblock_user"),
        ], cols=2)

    rows.append([InlineKeyboardButton(text=("▼ " if util_open else "► ") + "Сервис", callback_data="toggle:utils")])
    if util_open:
        rows += grid([
            InlineKeyboardButton(text="🏠 Выйти из админки", callback_data="admin_close"),
            InlineKeyboardButton(text="♻️ Обновить",         callback_data="admin_home"),
        ], cols=2)

    return InlineKeyboardMarkup(inline_keyboard=rows)

# === Утилиты ===
def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['month']['title'],     callback_data='buy_month')],
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['quarter']['title'],   callback_data='buy_quarter')],
        [InlineKeyboardButton(text='🔒 ' + TARIFFS['lifetime']['title'],  callback_data='buy_lifetime')],
        [InlineKeyboardButton(text='🧊 ' + TARIFFS['hide_data']['title'], callback_data='buy_hide_data')],
    ])

def start_keyboard() -> ReplyKeyboardMarkup:
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
    recent = [t for t in times if now - t <= FLOOD_WINDOW][-20:]  # максимум 20 отметок
    with conn:
        c.execute('UPDATE users SET last_queries=? WHERE id=?', (','.join(map(str, recent)), uid))
    return len(recent) > FLOOD_LIMIT or (len(recent) >= 2 and recent[-1] - recent[-2] < FLOOD_INTERVAL)

async def setup_menu_commands():
    from aiogram.types import BotCommandScopeDefault, BotCommandScopeChat

    user_cmds = [
        BotCommand(command="start",  description="Запуск"),
        BotCommand(command="status", description="Статус подписки и лимитов"),
        BotCommand(command="help",   description="Справка"),
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())

    if OWNER_ID:
        admin_cmds = user_cmds + [
            BotCommand(command="admin322", description="Панель администратора"),
        ]
        await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=OWNER_ID))

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

# === Рендер отчёта «как у них» ===
def _beautify_label_for_template(k: str) -> str:
    m = {
        'full_name':'Имя','phone':'Телефон','inn':'ИНН','email':'Email',
        'first_name':'Имя','last_name':'Фамилия','middle_name':'Отчество',
        'birth_date':'Дата рождения','gender':'Пол','passport_series':'Серия паспорта',
        'passport_number':'Номер паспорта','passport_date':'Дата выдачи'
    }
    return m.get(k, k)

def _is_url_simple(s: str) -> bool:
    if not isinstance(s, str): return False
    s = s.strip().lower()
    return s.startswith('http://') or s.startswith('https://')

def _render_value_node(soup: BeautifulSoup, src: str, key: str, v):
    if isinstance(v, (list, tuple)):
        box = soup.new_tag('div')
        for i, item in enumerate(v):
            node = _render_value_node(soup, src, key, item)
            if i > 0: box.append(soup.new_tag('br'))
            box.append(node)
        return box
    if isinstance(v, dict):
        return NavigableString(", ".join(f"{k2}: {v2}" for k2, v2 in v.items()))
    if isinstance(v, str):
        vs = v.strip()
        if _is_url_simple(vs):
            a = soup.new_tag('a', href=vs, target="_blank", rel="noopener")
            try:
                label = label_for_url(src, vs, key)
            except Exception:
                label = vs
            a.string = label
            return a
        return NavigableString(vs)
    return NavigableString(str(v))

def render_report_like_theirs(query_text: str, items: list[dict]) -> str:
    soup = BeautifulSoup(TEMPLATE_HTML, 'html.parser')

    # Чистим любые упоминания UserBox и ставим бренд/тайтл
    if soup.title:
        soup.title.string = f"{BRAND_NAME} — Report"
    for el in soup.find_all(string=lambda s: isinstance(s, str) and 'usersbox' in s.lower() or 'userbox' in s.lower()):
        try:
            el.replace_with(BRAND_NAME)
        except Exception:
            pass
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            val = tag.attrs.get(attr)
            if isinstance(val, str) and (('usersbox' in val.lower()) or ('userbox' in val.lower())):
                tag.attrs[attr] = re.sub(r'(?i)users?box', BRAND_NAME, val)

    # Фавикон → наш SVG (data URI)
    if soup.head:
        for ln in list(soup.head.find_all('link')):
            rel = ln.get('rel')
            if rel and any('icon' in r.lower() for r in (rel if isinstance(rel, list) else [rel])):
                ln.decompose()
        ico = soup.new_tag('link', rel='icon', type='image/svg+xml',
                           href='data:image/svg+xml;base64,' + EMBEDDED_FAVICON_B64)
        soup.head.append(ico)

    # Логотип (вставляем SVG-вордмарк)
    slot = soup.select_one('.logo-slot') or soup.select_one('#logo') or soup.select_one('[data-logo-slot]')
    if slot:
        slot.clear()
        frag = BeautifulSoup(EMBEDDED_LOGO_SVG, 'html.parser')
        node = frag.find('svg') or frag
        # enforce responsive sizing
if getattr(node, 'attrs', None):
    node.attrs.pop('width', None)
    node.attrs.pop('height', None)
slot.append(node)

    # Запрос пользователя в хедере
    hq = soup.select_one('.header_query')
    if hq:
        hq.clear()
        hq.append(NavigableString(query_text))

    container = soup.select_one('.databases') or soup.select_one('.content') or soup.body

    # Удаляем старые блоки .db
    for old in container.select('.db'):
        old.decompose()

    nav_ul = soup.select_one('.navigation_ul')
    if nav_ul:
        nav_ul.clear()

    db_index = 0
    for itm in items:
        src = (itm.get('source') or {}).get('database') or '?'
        year = (itm.get('source') or {}).get('year') or ''
        hits = (itm.get('hits') or {}).get('items') or []
        if not hits:
            continue

        safe_id = re.sub(r'[^a-zA-Z0-9_]+', '_', f"{src}_{year}_{db_index}")[:64] or f"db_{db_index}"
        db_index += 1

        db = soup.new_tag('div', **{'class': 'db', 'id': safe_id})
        header = soup.new_tag('div', **{'class': 'db_header'})
        header.string = f"{src} [{year}]" if year else src
        db.append(header)

        cards_wrap = soup.new_tag('div', **{'class': 'db_cards'})
        db.append(cards_wrap)

        for hit in hits:
            card = soup.new_tag('div', **{'class': 'card'})
            table = soup.new_tag('div', **{'class': 'table-main'})

            grouped = {g: [] for g in GROUP_ORDER}
            for k, v in hit.items():
                if v in (None, '', [], {}):
                    continue
                grp = group_for_key(k)
                grouped[grp].append((k, v))

            for grp in GROUP_ORDER:
                items_g = grouped.get(grp) or []
                if not items_g:
                    continue
                items_g.sort(key=lambda kv: sort_weight(grp, kv[0]))
                for k, v in items_g:
                    row = soup.new_tag('div', **{'class': 'row'})
                    left = soup.new_tag('div', **{'class': 'row_left'})
                    left.string = _beautify_label_for_template(k)
                    right = soup.new_tag('div', **{'class': 'row_right'})
                    right.append(_render_value_node(soup, src, k, v))
                    row.append(left); row.append(right)
                    table.append(row)

            card.append(table)
            cards_wrap.append(card)

        container.append(db)

        if nav_ul:
            li = soup.new_tag('li')
            a = soup.new_tag('a', href=f"#{safe_id}", **{'class': 'navigation_link'})
            a.string = src
            li.append(a); nav_ul.append(li)

    if not container.select('.db'):
        stub = soup.new_tag('div', **{'class':'db'})
        hh = soup.new_tag('div', **{'class':'db_header'})
        hh.string = 'Совпадений не найдено'
        stub.append(hh)
        container.append(stub)

    return str(soup)

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
    with conn:
        c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
                  (uid,0,0,0))
        if message.from_user.username:
            c.execute('UPDATE users SET username=? WHERE id=?',
                      (message.from_user.username, uid))
        c.execute('UPDATE users SET boot_ack_ts=? WHERE id=?', (int(BOOT_TS), uid))

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
    with conn:
        c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    if need_start(uid):
        return await ask_press_start(message.chat.id)

    subs, fu, hd, rl, te, _ = c.execute(
        'SELECT subs_until,free_used,hidden_data,requests_left,trial_expired,boot_ack_ts FROM users WHERE id=?',
        (uid,)
    ).fetchone()
    now = int(time.time())
    if hd:
        return await message.answer('🔒 Ваши данные скрыты.')
    sub = datetime.fromtimestamp(subs).strftime('%Y-%m-%d') if subs and subs > now else 'none'
    free = 0 if te else TRIAL_LIMIT - fu
    await message.answer(f"📊 Подписка: {sub}\nБесплатно осталось: {free}\nРучных осталось: {rl}")

@dp.message(Command('help'))
async def help_handler(message: Message):
    uid = message.from_user.id
    with conn:
        c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    if need_start(uid):
        return await ask_press_start(message.chat.id)

    help_text = (
        "/start  – запуск/обновление сессии\n"
        "/status – статус и лимиты\n"
        "/help   – справка\n"
    )
    if is_admin(uid):
        help_text += "/admin322 – панель администратора\n"
    help_text += "Отправьте любой текст для поиска."
    await message.answer(help_text)

# --- Админ-меню: якорь + секции ---
@dp.message(Command('admin322'))
async def admin_menu(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if need_start(message.from_user.id):
        return await ask_press_start(message.chat.id)
    ADMIN_OPEN_SECTIONS[message.from_user.id] = {"subs"}
    await admin_render(message, "<b>Панель администратора</b>", admin_kb_home(message.from_user.id), reset=True)
    try:
        await message.delete()
    except:
        pass

@dp.callback_query(F.data == 'admin_home')
async def admin_home(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await admin_render(call, "<b>Панель администратора</b>", admin_kb_home(call.from_user.id))
    await call.answer()

@dp.callback_query(F.data == 'admin_close')
async def admin_close(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    chat_id = call.message.chat.id
    anchor = ADMIN_ANCHORS.pop(chat_id, None)
    if anchor:
        try:
            await bot.delete_message(chat_id, anchor)
        except:
            pass
    await call.message.answer("Админ-панель закрыта.")
    await call.answer()

@dp.callback_query(F.data.startswith('toggle:'))
async def admin_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    key = call.data.split(':',1)[1]
    opened = ADMIN_OPEN_SECTIONS.setdefault(call.from_user.id, set())
    if key in opened: opened.remove(key)
    else: opened.add(key)
    await admin_render(call, "<b>Панель администратора</b>", admin_kb_home(call.from_user.id))
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
    await admin_render(call, 'Выберите план подписки:', kb)
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
    await admin_render(call, f'👥 Выберите пользователя для начисления подписки ({plan})', kb)
    await call.answer()

# --- Cкрыть/раскрыть произвольные данные (blacklist) ---
@dp.callback_query(F.data == 'add_blacklist')
async def add_blacklist_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await admin_render(call, "Вставьте значения через запятую, которые нужно скрыть (ФИО, телефоны, e-mail, даты и т.д.).\nПример:\n<code>Иванов Иван, 380661112233, 10.07.1999, test@example.com</code>")
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
        return await admin_render(msg, "Пустой ввод. Отменено.")
    values = [v.strip() for v in raw.split(',')]
    values = [v for v in values if v]
    added = 0
    with conn:
        for v in values:
            try:
                c.execute("INSERT OR IGNORE INTO blacklist(value) VALUES(?)", (v,))
                added += c.rowcount
            except:
                pass
    await admin_render(msg, f"✅ В чёрный список добавлено: {added} из {len(values)}.\nЭти значения будут блокироваться при поиске.")
    await state.clear()

@dp.callback_query(F.data == 'remove_blacklist')
async def remove_blacklist_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await admin_render(call, "Вставьте значения через запятую, которые нужно удалить из чёрного списка.\nПример:\n<code>Иванов Иван, 380661112233, 10.07.1999</code>")
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
        return await admin_render(msg, "Пустой ввод. Отменено.")
    values = [v.strip() for v in raw.split(',')]
    values = [v for v in values if v]
    removed = 0
    with conn:
        for v in values:
            try:
                c.execute("DELETE FROM blacklist WHERE value=?", (v,))
                removed += c.rowcount
            except:
                pass
    await admin_render(msg, f"✅ Из чёрного списка удалено: {removed} из {len(values)}.")
    await state.clear()

# === Листинги пользователей (прочие экраны) ===
@dp.callback_query(F.data == 'give_requests')
async def give_requests_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='give', page=0)
    await admin_render(call, '👥 Выберите пользователя для выдачи запросов:', kb)
    await call.answer()

@dp.callback_query(F.data == 'block_user')
async def block_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='block', page=0)
    await admin_render(call, '👥 Кого заблокировать?', kb)
    await call.answer()

@dp.callback_query(F.data == 'unblock_user')
async def unblock_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='unblock', page=0)
    await admin_render(call, '👥 Кого разблокировать?', kb)
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
    await admin_render(call, 'Выберите режим завершения триала:', kb)
    await call.answer()

@dp.callback_query(F.data == 'reset_pick')
async def reset_pick_list(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    kb = users_list_keyboard(action='reset', page=0)
    await admin_render(call, '👥 Выберите пользователя для завершения триала:', kb)
    await call.answer()

@dp.callback_query(F.data.startswith('list:'))
async def paginate_users(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    _, action, page_s = call.data.split(':', 2)
    page = int(page_s)
    kb = users_list_keyboard(action=action, page=page)
    await admin_render(call, 'Обновил список.', kb)
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
    uname_print = f'@{uname}' if uname and not uname.startswith('ID ') else uname

    if action == 'give':
        await state.update_data(grant_uid=uid)
        await admin_render(call, f'Выбран {uname_print}.\n🔢 Введите количество запросов (1–100):')
        await state.set_state(AdminStates.wait_grant_amount)

    elif action == 'block':
        with conn:
            c.execute('UPDATE users SET is_blocked=1 WHERE id=?', (uid,))
        await admin_render(call, f'🚫 Заблокирован {uname_print}.', admin_kb_home(call.from_user.id))

    elif action == 'unblock':
        with conn:
            c.execute('UPDATE users SET is_blocked=0 WHERE id=?', (uid,))
        await admin_render(call, f'✅ Разблокирован {uname_print}.', admin_kb_home(call.from_user.id))

    elif action == 'reset':
        with conn:
            c.execute('UPDATE users SET free_used=?, trial_expired=1 WHERE id=?', (TRIAL_LIMIT, uid))
        await admin_render(call, f'🔄 Триал завершён для {uname_print}.', admin_kb_home(call.from_user.id))

    elif action in ('sub_month','sub_quarter','sub_lifetime'):
        plan = action.split('_',1)[1]
        now_ts = int(time.time())
        old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()
        old_until = int(old[0]) if old and old[0] else 0
        new_until = max(now_ts, old_until) + TARIFFS[plan]['days']*86400
        with conn:
            c.execute('INSERT INTO users(id,subs_until) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until',
                      (uid, new_until))
            c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
        until_txt = datetime.fromtimestamp(new_until).strftime('%Y-%m-%d')
        await admin_render(call, f'🎟 Подписка «{plan}» выдана {uname_print} до {until_txt}.', admin_kb_home(call.from_user.id))

    else:
        await admin_render(call, 'Неизвестное действие.', admin_kb_home(call.from_user.id))
    await call.answer()

@dp.message(AdminStates.wait_grant_amount)
async def grant_amount_input(msg: Message, state: FSMContext):
    if msg.from_user.id != OWNER_ID:
        await state.clear(); return
    if need_start(msg.from_user.id):
        await state.clear()
        return await ask_press_start(msg.chat.id)
    if not (msg.text or "").isdigit():
        return await admin_render(msg, 'Введите число 1–100.')
    amount = int(msg.text)
    if not (1 <= amount <= 100):
        return await admin_render(msg, 'Диапазон 1–100.')
    data = await state.get_data()
    uid = data.get('grant_uid')
    if not uid:
        await state.clear()
        return await admin_render(msg, '⚠️ Пользователь не выбран. Попробуйте снова.')
    with conn:
        c.execute('UPDATE users SET requests_left=? WHERE id=?', (amount, uid))
    uname = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    uname = uname[0] if uname and uname[0] else f'ID {uid}'
    uname_print = f'@{uname}' if uname and not uname.startswith('ID ') else uname
    await admin_render(msg, f'✅ Выдано {amount} запросов {uname_print}.', admin_kb_home(msg.from_user.id))
    await state.clear()

# === Массовый сброс триала ===
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
            if message_id:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=f"🔄 Массовый сброс… {min(i+1000, total)}/{total}"
                )
        except:
            pass
    try:
        if message_id:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                text=f"✅ Триал завершён у всех. Обновлено записей: {affected}.")
        else:
            await bot.send_message(chat_id, f"✅ Триал завершён у всех. Обновлено записей: {affected}.")
    except:
        pass

@dp.callback_query(F.data=='reset_all')
async def reset_all(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    await call.answer('Запустил массовый сброс…')
    msg = await call.message.answer("🔄 Массовый сброс… 0%")
    asyncio.create_task(_reset_all_job(chat_id=msg.chat.id, message_id=msg.message_id))
    await state.clear()

# === Поиск и HTML (НОВЫЙ ШАБЛОН «как у них») ===
@dp.message(F.text & ~F.text.startswith('/'))
async def search_handler(message: Message):
    uid = message.from_user.id
    with conn:
        c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
                  (uid,0,0,0))
        if message.from_user.username:
            c.execute('UPDATE users SET username=? WHERE id=?',
                      (message.from_user.username, uid))

    if need_start(uid):
        return await ask_press_start(message.chat.id)

    original_q = message.text.strip()
    q_for_api, norm_phone = normalize_query_if_phone(original_q)

    is_blocked, hidden_data, requests_left, free_used, subs_until, trial_expired = c.execute(
        'SELECT is_blocked,hidden_data,requests_left,free_used,subs_until,trial_expired '
        'FROM users WHERE id=?', (uid,)
    ).fetchone()
    now_ts = int(time.time())

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
            with conn:
                c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?', (uid,))
        else:
            if subs_until and subs_until > now_ts:
                pass
            else:
                if trial_expired:
                    return await message.answer('🔐 Триал окончен. Подпишитесь.', reply_markup=sub_keyboard())
                if free_used < TRIAL_LIMIT:
                    with conn:
                        c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?', (uid,))
                        if free_used + 1 >= TRIAL_LIMIT:
                            c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                else:
                    with conn:
                        c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                    return await message.answer('🔐 Триал окончен. Подпишитесь.', reply_markup=sub_keyboard())

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

    try:
        items = data['data'].get('items', [])
        html_out = render_report_like_theirs(shown_q, items)
    except Exception as e:
        logging.exception("render_report_like_theirs failed: %s", e)
        return await message.answer('⚠️ Ошибка рендера HTML.')

    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html', dir='/tmp', encoding='utf-8') as tf:
        tf.write(html_out)
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
    except Exception as e:
        logging.exception("createInvoice error: %s", e)
        return await callback.message.answer('⚠️ Ошибка платежного сервиса.')
    if not data.get('ok'):
        return await callback.message.answer(f"⚠️ Ошибка: {data}")

    inv = data['result']
    inv_id = str(inv.get('invoice_id') or inv.get('id'))
    url = inv.get('bot_invoice_url') or inv.get('pay_url')
    try:
        with conn:
            c.execute(
                'INSERT OR REPLACE INTO invoices(invoice_id,payload,user_id,plan,amount,asset,status,created_at) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (inv_id, payload, callback.from_user.id, plan, float(price), BASE_CURRENCY, 'pending', int(time.time()))
            )
    except Exception as e:
        logging.warning("cannot upsert invoice: %s", e)

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
    except Exception:
        return web.json_response({'ok': True})

    inv = js.get('invoice') or js
    status = inv.get('status')
    payload = inv.get('payload')

    if status == 'paid' and payload:
        row = c.execute("SELECT 1 FROM payments WHERE payload=?", (payload,)).fetchone()
        if row:
            return web.json_response({'ok': True})

        try:
            parts = payload.split('_')
            if parts[0] != 'pay' or len(parts) < 4:
                return web.json_response({'ok': True})
            uid, plan = int(parts[1]), parts[2]
        except Exception:
            return web.json_response({'ok': True})

        now_ts = int(time.time())
        try:
            if plan == 'hide_data':
                with conn:
                    c.execute('UPDATE users SET hidden_data=1 WHERE id=?', (uid,))
            else:
                old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()
                old_until = int(old[0]) if old and old[0] else 0
                ns = max(now_ts, old_until) + TARIFFS[plan]['days']*86400
                with conn:
                    c.execute(
                        'INSERT INTO users(id,subs_until,free_used,trial_expired) VALUES(?,?,?,1) '
                        'ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until, free_used=0, trial_expired=1',
                        (uid, ns, 0)
                    )
            with conn:
                c.execute(
                    'INSERT OR REPLACE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',
                    (payload, uid, plan, now_ts)
                )
                inv_id = str(inv.get('invoice_id') or inv.get('id') or '')
                if inv_id:
                    c.execute('UPDATE invoices SET status=? WHERE invoice_id=?', ('paid', inv_id))
        except Exception as e:
            logging.exception("cryptopay webhook processing error: %s", e)
        try:
            await bot.send_message(uid, f"✅ Оплата принята: {plan}")
        except:
            pass
    return web.json_response({'ok': True})

# === Reconcile ===
async def reconcile_cryptopay_recent(hours: int = 24):
    if not CRYPTOPAY_API_TOKEN:
        return
    since = int(time.time()) - hours*3600
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                'https://pay.crypt.bot/api/getInvoices',
                headers={'Crypto-Pay-API-Token': CRYPTOPAY_API_TOKEN},
                params={'count': 100, 'status': 'paid'},
                timeout=12
            ) as r:
                data = await r.json()
        if not data.get('ok'):
            return
        for itm in data['result'].get('items', []):
            if itm.get('status') != 'paid':
                continue
            paid_at = int(itm.get('paid_at') or 0)
            if paid_at and paid_at < since:
                continue
            payload = itm.get('payload')
            if not payload:
                continue
            row = c.execute("SELECT 1 FROM payments WHERE payload=?", (payload,)).fetchone()
            if row:
                continue
            try:
                parts = payload.split('_')
                if parts[0] != 'pay' or len(parts) < 4:
                    continue
                uid, plan = int(parts[1]), parts[2]
            except Exception:
                continue
            now_ts = int(time.time())
            try:
                if plan == 'hide_data':
                    with conn:
                        c.execute('UPDATE users SET hidden_data=1 WHERE id=?', (uid,))
                else:
                    old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()
                    old_until = int(old[0]) if old and old[0] else 0
                    ns = max(now_ts, old_until) + TARIFFS[plan]['days']*86400
                    with conn:
                        c.execute(
                            'INSERT INTO users(id,subs_until,free_used,trial_expired) VALUES(?,?,?,1) '
                            'ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until, free_used=0, trial_expired=1',
                            (uid, ns, 0)
                        )
                with conn:
                    c.execute(
                        'INSERT OR REPLACE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',
                        (payload, uid, plan, paid_at or now_ts)
                    )
            except Exception as e:
                logging.exception("reconcile error: %s", e)
            try:
                await bot.send_message(uid, f"✅ Подтвердил оплату: {plan} (reconcile)")
            except:
                pass
    except Exception as e:
        logging.warning("reconcile request failed: %s", e)

# === Startup/Shutdown ===
async def on_startup(app):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    await setup_menu_commands()
    asyncio.create_task(reconcile_cryptopay_recent(24))
    logging.info("Меню команд установлено. BOOT_TS=%s, DB_PATH=%s", BOOT_TS, DB_PATH)

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
