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
    html{scroll-behavior:smooth}
    :root{
      --bg:#0b1220; --panel:#101829; --muted:#0d1526; --text:#dbe6ff;
      --accent:#0AEFFF; --accent2:#00E68E; --line:rgba(10,239,255,.25)
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter, Segoe UI, Roboto, Arial, sans-serif}
    header{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:16px;padding:14px 18px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#0c1426,#0a1120)}
    .nav-toggle{display:none;align-items:center;justify-content:center;width:42px;height:42px;border:1px solid var(--line);background:var(--muted);color:var(--text);border-radius:10px;cursor:pointer}
    .nav-toggle:active{transform:translateY(1px)}
    .logo-slot{grid-column:2;display:flex;justify-content:center;align-items:center;min-height:48px}
    .logo-slot svg,.logo-slot img{display:block;height:44px;width:auto;max-width:100%}
    .header_query{grid-column:3;justify-self:end;opacity:.9;font-weight:600}
    .wrap{display:grid;grid-template-columns:260px 1fr}
    .backdrop{display:none}
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
    @media(max-width:920px){
      .wrap{grid-template-columns:1fr}
      nav{display:none}
      .nav-toggle{display:inline-flex}
      body.nav-open nav{display:block;position:fixed;top:64px;left:0;right:0;bottom:0;background:var(--panel);z-index:1000;overflow:auto;padding:10px}
      body.nav-open .backdrop{display:block;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:900}
      .logo-slot svg,.logo-slot img{height:36px}
    }
    @media(max-width:480px){.logo-slot svg,.logo-slot img{height:28px}}
  
/* === Mobile overlay nav (non-destructive) === */
.nav-toggle{display:none;align-items:center;justify-content:center;width:42px;height:42px;border:1px solid var(--line);background:var(--muted);color:var(--text);border-radius:10px;cursor:pointer;text-decoration:none}
.nav-toggle:active{transform:translateY(1px)}
.mnav{display:none}
.mnav-backdrop{display:none}
.mnav-panel{display:none}
@media(max-width:920px){
  .wrap{grid-template-columns:1fr}
  nav{display:none}
  .nav-toggle{display:inline-flex}
  /* Show overlay when #mnav is target */
  #mnav:target{display:block;position:fixed;inset:0;z-index:1000}
  #mnav:target .mnav-backdrop{display:block;position:absolute;inset:0;background:rgba(0,0,0,.5)}
  #mnav:target .mnav-panel{display:block;position:absolute;top:64px;left:0;right:0;bottom:0;background:var(--panel);overflow:auto;padding:10px}
  .mnav-header{position:sticky;top:0;display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:var(--panel);border-bottom:1px solid var(--line);z-index:1}
  .mnav-close{display:inline-flex;width:38px;height:38px;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:10px;text-decoration:none;color:var(--text);background:var(--muted)}
  .navigation_ul{list-style:none;margin:0;padding:10px}
  .navigation_ul li{margin:6px 0}
  .navigation_ul a{display:block;padding:10px 12px;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:var(--muted);text-decoration:none;color:var(--text)}
  .navigation_ul a:active{transform:translateY(1px)}
  .logo-slot svg,.logo-slot img{height:36px}
}
@media(max-width:480px){
  .logo-slot svg,.logo-slot img{height:28px}
}
/* keep anchors visible under header */
.db{scroll-margin-top:76px}

  </style>
</head>
<body>
  <div id="close"></div>
  <header>
    <button class="nav-toggle" aria-label="Навигация" title="Навигация">☰</button>
    <div class="logo-slot" aria-label="brand"></div>
    <div class="header_query"></div>
  </header>

  <!-- Mobile overlay navigation (pure CSS via :target) -->
  <div id="mnav" class="mnav">
    <a class="mnav-backdrop" href="#close" aria-label="Закрыть"></a>
    <div class="mnav-panel">
      <div class="mnav-header">
        <span>Навигация</span>
        <a href="#close" class="mnav-close" aria-label="Закрыть">✕</a>
      </div>
      <ul class="navigation_ul"></ul>
    </div>
  </div>
  <div class="backdrop"></div>
  <div class="wrap">
    <nav><ul class="navigation_ul"></ul></nav>
    <main><div class="databases"></div></main>
  </div>
  <script>
  (function(){
    try{
      var btn = document.querySelector('.nav-toggle');
      var backdrop = document.querySelector('.backdrop');
      var body = document.body;
      var nav = document.querySelector('nav');
      function close(){ body.classList.remove('nav-open'); }
      if(btn){
        btn.addEventListener('click', function(){ body.classList.toggle('nav-open'); });
      }
      if(backdrop){
        backdrop.addEventListener('click', close);
      }
      if(nav){
        nav.addEventListener('click', function(e){
          var a = e.target.closest('a.navigation_link');
          if(a){ close(); }
        });
      }
    }catch(e){}
  })();
  </script>
</body>
</html>"""

EMBEDDED_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="260" viewBox="0 0 1200 260">
  <style>
    html{scroll-behavior:smooth}.logo{font:700 128px/1 'Inter','Segoe UI','Roboto','Arial',sans-serif;letter-spacing:.5px}</style>
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

    rows.append([InlineKeyboardButton(text="📜 История запросов", callback_data="qlog_menu")])
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


# --- Алиасы человеко-понятных названий источников ---
SOURCE_ALIASES = {
    "dea": "DEA",
    "olx": "OLX",
    "nova poshta": "Нова пошта",
    "novaposhta": "Нова пошта",
    "nova_pochta": "Нова пошта",
    "np": "Нова пошта",
    "ukr poshta": "Укрпошта",
    "ukrposhta": "Укрпошта",
    "mvs": "МВС",
    "mvd": "МВД",
    "minjust": "Минюст",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "telegram": "Telegram",
    "twitter": "Twitter",
    "x.com": "Twitter/X",
    "x ": "Twitter/X",
}


# --- Из списка источников: авто-канонизация названий ---
KNOWN_SOURCES = {
    '1569 dbs 20 314 877 076 records 7.8 tb 2 dbs in processing...': '1569 DBs • 20,314,877,076 records • 7.8 TB🔎 2 DBs in processing...',
    'вконтакте': 'ВКонтакте',
    'acxiom': 'Acxiom',
    'gemotest анализы': 'Gemotest (Анализы)',
    'verifications.io': 'verifications.io',
    'linkedin': 'Linkedin',
    'antipublic 1': 'AntiPublic #1',
    'antipublic 2': 'AntiPublic #2',
    'breachcompilation': 'BreachCompilation',
    'facebook': 'Facebook',
    'linkedin scrape': 'Linkedin Scrape',
    'deezer': 'Deezer',
    'url.login.pass': 'url.login.pass',
    'росреестр 1': 'Росреестр #1',
    'фссп физ. лица': 'ФССП Физ. лица',
    'wattpad.com': 'wattpad.com',
    'sbermarket_ru.2025': 'sbermarket_ru.2025',
    'фссп': 'ФССП',
    'казино 1win': 'Казино 1win',
    'госуслуги 130m': 'Госуслуги (130m)',
    'спортмастер': 'Спортмастер',
    'myspace': 'MySpace',
    'дрфо': 'ДРФО',
    'cit0day': 'Cit0day',
    'cdek': 'CDEK',
    'twitter.com': 'twitter.com',
    'tencent.com': 'tencent.com',
    'parking.mos.ru': 'parking.mos.ru',
    'гибдд москвы': 'ГИБДД Москвы',
    'мфо 2': 'МФО #2',
    'canva.com': 'canva.com',
    'badoo.com': 'badoo.com',
    'sirena-travel.ru': 'sirena-travel.ru',
    'яндекс.еда': 'Яндекс.Еда',
    'apexsms.com': 'apexsms.com',
    'турции': 'Турции',
    'fotostrana.ru': 'fotostrana.ru',
    'apollo': 'Apollo',
    'at t': 'AT&T',
    'россии': 'России',
    'myfitnesspal': 'MyFitnessPal',
    'мфо 1': 'МФО #1',
    'альфа-банк': 'Альфа-Банк',
    'mindjolt.com': 'mindjolt.com',
    'сирена жд билеты': 'Сирена ЖД билеты',
    'gemotest': 'Gemotest',
    'ibd-spectr': 'ibd-spectr',
    'vivo.com.br': 'vivo.com.br',
    'numbuster': 'NumBuster',
    'приватбанк': 'ПриватБанк',
    'российские страховщики': 'Российские страховщики',
    'avito': 'Avito',
    'егрип': 'ЕГРИП',
    'лицензии водителей украины': 'Лицензии водителей Украины',
    'jd.com': 'jd.com',
    'pochta.ru': 'pochta.ru',
    'избиратели украины': 'Избиратели Украины',
    'emias.info': 'emias.info',
    'gonitro.com': 'gonitro.com',
    'украина криминал': 'Украина Криминал',
    'neopets.com': 'neopets.com',
    'должники anticreditors': 'Должники (anticreditors)',
    'trudvsem_ru.parsing_2024': 'trudvsem_ru.parsing_2024',
    'truecaller india': 'TrueCaller India',
    'налоговая россии': 'Налоговая России',
    'telegram чатов': 'Telegram чатов',
    'енис': 'Енис',
    'mgm resorts': 'MGM Resorts',
    'youku.com': 'youku.com',
    'dailymotion.com': 'dailymotion.com',
    'магнит.маркет': 'Магнит.Маркет',
    'younow.com': 'younow.com',
    'мтс банк': 'МТС Банк',
    'flexbooker.com': 'flexbooker.com',
    'wakanim.tv': 'wakanim.tv',
    'мфо 3': 'МФО #3',
    'tumblr': 'Tumblr',
    'zaymer.ru': 'zaymer.ru',
    'zoosk.com': 'zoosk.com',
    'dadata.ru': 'dadata.ru',
    'москвы': 'Москвы',
    'сберспасибо': 'СберСпасибо',
    'imesh': 'iMesh',
    'fling.com': 'fling.com',
    'абоненты': 'Абоненты',
    'онлайн-сервис houzz.com': 'Онлайн-сервис houzz.com',
    'gravatar.com 1': 'gravatar.com #1',
    'last.fm': 'last.fm',
    'одноклассники парсинг': 'Одноклассники Парсинг',
    'my.mail.ru': 'my.mail.ru',
    'aptoide.com': 'aptoide.com',
    'уфы': 'Уфы',
    'cdek contragent': 'CDEK Contragent',
    'почта россии': 'Почта России',
    'rambler': 'Rambler',
    'мфо 4': 'МФО #4',
    'animoto': 'Animoto',
    'башкортостан адм. практика гибдд': 'Башкортостан Адм. практика ГИБДД',
    'санкт-петербурга': 'Санкт-Петербурга',
    'неизвестные дампы': 'Неизвестные дампы',
    'абоненты kievstar': 'Абоненты kievstar',
    'mos.ru': 'mos.ru',
    'большая перемена': 'Большая перемена',
    '100realt.ru': '100realt.ru',
    'getcontact': 'GetContact',
    'российская электронная школа': 'Российская электронная школа',
    'ковид москва': 'Ковид Москва',
    'казахстана': 'Казахстана',
    'ленинградская обл. фмс цбдуиг': 'Ленинградская обл. ФМС ЦБДУИГ',
    'портал ngs.ru': 'Портал ngs.ru',
    'qip.ru': 'qip.ru',
    'онлайн-кинотеатр start.ru': 'Онлайн-кинотеатр start.ru',
    'мос covid': 'Мос Covid',
    'госуслуги': 'Госуслуги',
    'mathway.com': 'mathway.com',
    '500px.com': '500px.com',
    'aitype keyboards': 'AiType Keyboards',
    'sprashivai.ru': 'sprashivai.ru',
    'mate1.com': 'mate1.com',
    'barahla.net': 'barahla.net',
    'dropbox': 'Dropbox',
    '123rf.com': '123rf.com',
    'trello.com': 'trello.com',
    'tutu.ru 1': 'tutu.ru #1',
    'согаз': 'СОГАЗ',
    'фмс владимирской области': 'ФМС Владимирской области',
    'adengi.ru': 'adengi.ru',
    'romwe.com': 'romwe.com',
    'taringa.net': 'taringa.net',
    'eyeem.com': 'eyeem.com',
    'gfan': 'Gfan',
    'parkmobile': 'ParkMobile',
    'book24.ru': 'book24.ru',
    'livejournal': 'LiveJournal',
    'qzaem.ru': 'qzaem.ru',
    'водительские права': 'Водительские права',
    '8tracks.com': '8tracks.com',
    'гибдд калужской области': 'ГИБДД Калужской области',
    'stripchat.com': 'stripchat.com',
    'росреестр московской области': 'Росреестр Московской области',
    'регистрации башкортостан': 'Регистрации Башкортостан',
    'lalafo.com': 'lalafo.com',
    'wanelo.com': 'wanelo.com',
    'privatbank': 'PrivatBank',
    'luminpdf.com': 'luminpdf.com',
    'inturist.ru': 'inturist.ru',
    'сбербанк': 'СберБанк',
    'm2bomber.com': 'm2bomber.com',
    'kvartelia.ru': 'kvartelia.ru',
    'фармацея': 'Фармацея',
    'московская электронная школа': 'Московская Электронная Школа',
    'фссп юр. лица': 'ФССП Юр. лица',
    'gsm солянка': 'GSM Солянка',
    'гибдд спб': 'ГИБДД СПБ',
    'rossko.ru 1': 'rossko.ru #1',
    'казахстан inn': 'Казахстан INN',
    'mirtesen.ru': 'mirtesen.ru',
    'новая почта украина': 'Новая Почта Украина',
    'telegram': 'Telegram',
    'билайн москва': 'Билайн Москва',
    'покупатели stockx.com': 'Покупатели stockx.com',
    'id.zing.vn': 'id.zing.vn',
    'telegram ботов': 'Telegram ботов',
    'сберлогистика cainiao.com': 'Сберлогистика (cainiao.com)',
    'boostra.ru': 'boostra.ru',
    'башкортостан инн': 'Башкортостан ИНН',
    'свердловская обл. регистрации': 'Свердловская обл. регистрации',
    'дом.ру': 'ДОМ.РУ',
    'chitai-gorod.ru': 'chitai-gorod.ru',
    'dns shop': 'DNS Shop',
    'лукойл': 'Лукойл',
    'ощадбанк': 'Ощадбанк',
    'диллеры nl international': 'Диллеры NL International',
    '2gis.ru': '2gis.ru',
    'сберлогистика leomax.ru': 'Сберлогистика (leomax.ru)',
    'atlasbus.ru': 'atlasbus.ru',
    'shein.com': 'shein.com',
    'фомс краснодарского края': 'ФОМС Краснодарского края',
    'красное белое': 'Красное & Белое',
    'московской области': 'Московской области',
    'kassy.ru': 'kassy.ru',
    'ашан': 'АШАН',
    'cutout.pro': 'cutout.pro',
    'фомс свердловской области': 'ФОМС Свердловской области',
    'mc сервер vimeworld': 'MC сервер VimeWorld',
    'приморского края': 'Приморского края',
    'банка': 'банка',
    'фомс ульяновской области': 'ФОМС Ульяновской области',
    'jobandtalent.com': 'jobandtalent.com',
    'dailyquiz.ru': 'dailyquiz.ru',
    'pryanikov38.ru': 'pryanikov38.ru',
    'фмс казахстана': 'ФМС Казахстана',
    'nl international': 'NL International',
    '585zolotoy.ru': '585zolotoy.ru',
    'tutu.ru': 'tutu.ru',
    're:store': 're:Store',
    '000webhost.com': '000webhost.com',
    'leet.cc': 'leet.cc',
    'loveplanet.ru': 'LovePlanet.ru',
    'appen.com': 'appen.com',
    'zdravcity.ru': 'zdravcity.ru',
    'объявления olx.ua': 'Объявления OLX.ua',
    'acko.ru': 'acko.ru',
    'podrygka.ru': 'podrygka.ru',
    'фомс самарской области': 'ФОМС Самарской области',
    'егрюл': 'ЕГРЮЛ',
    'skyeng': 'SkyEng',
    'работа в челябинске': 'Работа в Челябинске',
    'eskimidehash': 'EskimiDehash',
    'российские компании': 'Российские компании',
    'instagram': 'Instagram',
    'smartresponder_ru.2023': 'smartresponder_ru.2023',
    'crossfire': 'crossfire',
    'burgerkingrus.ru': 'burgerkingrus.ru',
    'digido.ph': 'digido.ph',
    'school.bars.group': 'school.bars.group',
    'oranta.ua': 'oranta.ua',
    'zomato.com': 'zomato.com',
    'whitepages.com': 'whitepages.com',
    '8fit.com': '8fit.com',
    'знакомств baihe.com': 'знакомств baihe.com',
    'логи sms activate': 'Логи SMS Activate',
    'rendez-vous.ru': 'rendez-vous.ru',
    'ростовской области': 'Ростовской области',
    'фнс самары': 'ФНС Самары',
    'eldorado.ua': 'eldorado.ua',
    'el-polis.ru': 'el-polis.ru',
    'leroymerlin.ru': 'leroymerlin.ru',
    'mail.ru': 'mail.ru',
    'mamba.ru': 'mamba.ru',
    'chegg.com': 'chegg.com',
    'фомс новосибирская обл.': 'ФОМС Новосибирская обл.',
    'неизвестные дампы ru': 'Неизвестные дампы ru',
    'diia.gov.ua': 'diia.gov.ua',
    'мфо 4 apps': 'МФО #4 Apps',
    'лицензии росздравнадзор': 'Лицензии Росздравнадзор',
    'tunngle.net': 'tunngle.net',
    'youporn': 'YouPorn',
    'oriflame': 'Oriflame',
    'воронежа': 'Воронежа',
    'winelab.ru': 'winelab.ru',
    'weheartit.com': 'weheartit.com',
    'фомс саратовской области': 'ФОМС Саратовской области',
    'level.travel': 'Level.Travel',
    'медицинская страховка мск': 'Медицинская страховка МСК',
    'onlinetrade.ru': 'OnlineTrade.ru',
    'robinhood': 'Robinhood',
    'askona.ru': 'askona.ru',
    'astramed-ms.ru': 'astramed-ms.ru',
    'orteka.ru': 'orteka.ru',
    'свердловская обл. мед. страхование': 'Свердловская обл. мед. страхование',
    'taxsee водители': 'Taxsee Водители',
    'фомс хмао': 'ФОМС ХМАО',
    'телефоны украины': 'Телефоны Украины',
    'фомс ростовской области': 'ФОМС Ростовской области',
    '2gis 1': '2GIS #1',
    'helix.ru': 'helix.ru',
    'dmed.kz': 'dmed.kz',
    'bookmate': 'Bookmate',
    'фомс вологодской области': 'ФОМС Вологодской области',
    'nnm-club.ru': 'nnm-club.ru',
    '000webhost': '000webhost',
    'tele2 программа лояльность': 'Tele2 Программа лояльность',
    'сберлогистика goldapple.ru': 'Сберлогистика (goldapple.ru)',
    'raychat.io': 'raychat.io',
    'bit.ly': 'bit.ly',
    'autozs.ru': 'autozs.ru',
    'marvin.kz': 'marvin.kz',
    'сберлогистика avito.ru': 'Сберлогистика (avito.ru)',
    'фомс челябинск': 'ФОМС Челябинск',
    'калининграда': 'Калининграда',
    'гибдд нижегородская область нарушения': 'ГИБДД Нижегородская область (нарушения)',
    '2gis 2': '2GIS #2',
    'фомс кыргызстана': 'ФОМС Кыргызстана',
    'duolingo.com 1': 'duolingo.com #1',
    'краснодара': 'Краснодара',
    'animoto.com': 'animoto.com',
    'yappy': 'Yappy',
    'mcresolver.pw': 'mcresolver.pw',
    'yandex.ru': 'yandex.ru',
    'жилье москвы': 'Жилье Москвы',
    'paysystem.tech': 'paysystem.tech',
    'сирена авиа': 'Сирена Авиа',
    'gamigo.com': 'gamigo.com',
    'artek.org': 'artek.org',
    'абоненты армении': 'Абоненты Армении',
    'красноярска': 'Красноярска',
    'superjob.ru': 'superjob.ru',
    'павлодара': 'Павлодара',
    '1000dosok.ru': '1000dosok.ru',
    'dostaevsky.ru': 'dostaevsky.ru',
    'нидерланды': 'Нидерланды',
    'воронежская обл. адм. практика': 'Воронежская обл. Адм. практика',
    'выборов истра-да': 'выборов Истра-ДА',
    'сберлогистика shoppinglive.ru': 'Сберлогистика (shoppinglive.ru)',
    'фомс пензенской области': 'ФОМС Пензенской области',
    'фомс тульской области': 'ФОМС Тульской области',
    'башкортостан адм. практика об': 'Башкортостан Адм. практика ОБ',
    'сберлогистика shopandshow.ru': 'Сберлогистика (shopandshow.ru)',
    'фомс казани': 'ФОМС Казани',
    'автобусные билеты': 'Автобусные билеты',
    'worldclass.ru': 'worldclass.ru',
    'омска': 'Омска',
    'familyspace.ru': 'familyspace.ru',
    'сберлогистика market.yandex.ru': 'Сберлогистика (market.yandex.ru)',
    'чувашии': 'Чувашии',
    'cafepress.com': 'cafepress.com',
    'zaimer.kz': 'zaimer.kz',
    'kinokassa.ru': 'kinokassa.ru',
    'фомс приморского края': 'ФОМС Приморского края',
    'synevo.ua': 'synevo.ua',
    'stosplit_ru.users_2025': 'stosplit_ru.users_2025',
    'действия чатов в telegram': 'Действия чатов в Telegram',
    'украины covid': 'Украины (Covid)',
    'билайн': 'Билайн',
    'maksavit.ru': 'maksavit.ru',
    'enbek.kz': 'enbek.kz',
    'ржд': 'РЖД',
    'emehmon_uz.departures_2025': 'emehmon_uz.departures_2025',
    'спб криминал': 'СПБ Криминал',
    'toy.ru': 'toy.ru',
    'poshmark.com': 'poshmark.com',
    'litres.ru': 'litres.ru',
    'совкомбанк': 'Совкомбанк',
    'фомс санкт-петербурга': 'ФОМС Санкт-Петербурга',
    'famil.ru': 'famil.ru',
    'volia.com': 'volia.com',
    'фомс кемеровской области': 'ФОМС Кемеровской области',
    'lsgb.net': 'lsgb.net',
    'самокаты whoosh': 'Самокаты Whoosh',
    'papajohns.ru': 'papajohns.ru',
    'lbsg.net': 'lbsg.net',
    'text.ru': 'text.ru',
    'книжный магазин bookvoed.ru': 'Книжный магазин bookvoed.ru',
    'фомс тюменской области': 'ФОМС Тюменской области',
    'bitcoinsecurity': 'bitcoinsecurity',
    'онлайн-кинотеатр kinokong': 'Онлайн-кинотеатр KinoKong',
    'саратова': 'Саратова',
    'отзывы пятерочки': 'Отзывы Пятерочки',
    'vkusnyesushi.ru': 'vkusnyesushi.ru',
    'memechat': 'Memechat',
    '1cont.ru': '1cont.ru',
    'kupivip': 'KupiVip',
    'кировской области': 'Кировской области',
    'ekonika_ru.users_2024': 'ekonika_ru.users_2024',
    'телефонов vk': 'телефонов VK',
    'яндекс.карты': 'Яндекс.Карты',
    'aptekiplus.ru': 'aptekiplus.ru',
    'билайн домашний интернет': 'Билайн (Домашний Интернет)',
    'фомс хабаровского края': 'ФОМС Хабаровского края',
    'избиратели белгородской области': 'Избиратели Белгородской области',
    'волгоградской области': 'Волгоградской области',
    'gfan.com': 'gfan.com',
    'delivery club 2': 'Delivery Club #2',
    'neznaika.info': 'neznaika.info',
    'kixify.com': 'kixify.com',
    'yota.ru': 'yota.ru',
    'patreon.com': 'patreon.com',
    'экстренные службы самары': 'Экстренные службы Самары',
    'магазин одежды tvoe.ru': 'Магазин одежды tvoe.ru',
    'parapa.mail.ru': 'parapa.mail.ru',
    'данные сайта jdbbx.com': 'Данные сайта jdbbx.com',
    'юридические лица казахстан': 'Юридические лица Казахстан',
    'фссп оренбурга': 'ФССП Оренбурга',
    'quidd.co': 'quidd.co',
    'фомс омской области': 'ФОМС Омской области',
    'adamas.ru': 'adamas.ru',
    'kari.com': 'kari.com',
    'mail.ru 3m': 'mail.ru (3M)',
    'jobs.ua': 'jobs.ua',
    'delivery club 1': 'Delivery Club #1',
    'toy.ru пользователи': 'toy.ru пользователи',
    'ростелеком краснодарского края': 'Ростелеком Краснодарского Края',
    'фомс дагестан': 'ФОМС Дагестан',
    'vietloan.vn': 'vietloan.vn',
    'тюмени': 'Тюмени',
    'sgb.net': 'sgb.net',
    'renins.ru': 'renins.ru',
    'ип 115': 'ИП 115',
    'foamstore.ru': 'foamstore.ru',
    'metro-cc.ru': 'metro-cc.ru',
    'фомс курска': 'ФОМС Курска',
    'сберлогистика sunlight.net': 'Сберлогистика (sunlight.net)',
    'сберлогистика iherb.com': 'Сберлогистика (iherb.com)',
    'tvoydom.ru': 'tvoydom.ru',
    'morele.net': 'morele.net',
    'калуги': 'Калуги',
    'egaz.uz': 'egaz.uz',
    'тамбова': 'Тамбова',
    'гибдд республики башкортостан': 'ГИБДД Республики Башкортостан',
    'ru.puma.com': 'ru.puma.com',
    'бизнес персоны': 'Бизнес Персоны',
    'cashcrate.com': 'cashcrate.com',
    'фомс томской области': 'ФОМС Томской области',
    'avito объявления': 'Avito объявления',
    'sushi-master_ru.full_orders_2022': 'sushi-master_ru.full_orders_2022',
    'аптека vitaexpress.ru': 'Аптека vitaexpress.ru',
    'магазин одежды gloria-jeans.ru': 'Магазин одежды gloria-jeans.ru',
    'сберлогистика sberlogistics.ru': 'Сберлогистика (sberlogistics.ru)',
    'fifa.com': 'fifa.com',
    'ростова-на-дону': 'Ростова-на-Дону',
    'premiumbonus.ru': 'premiumbonus.ru',
    'сберлогистика sberdevices.ru': 'Сберлогистика (sberdevices.ru)',
    'webtretho.com': 'webtretho.com',
    'ria.ru': 'ria.ru',
    'winestyle.ru': 'winestyle.ru',
    'adultfriendfinder.com': 'adultfriendfinder.com',
    'прописка армении': 'Прописка Армении',
    'city-mobil.ru': 'city-mobil.ru',
    'astrovolga.ru': 'astrovolga.ru',
    'clubhouse': 'Clubhouse',
    'втб телефоны': 'ВТБ Телефоны',
    'сочи': 'Сочи',
    'умершие саратовская область': 'Умершие Саратовская область',
    'ormatek_com.2024': 'ormatek_com.2024',
    'grastin.ru': 'grastin.ru',
    'hyundai.ru': 'hyundai.ru',
    'nival.com': 'nival.com',
    'smartresponder': 'SmartResponder',
    'book24.ua': 'book24.ua',
    'мгст': 'МГСТ',
    'work5.ru': 'work5.ru',
    'форум ykt.ru': 'Форум ykt.ru',
    'nexusmods.com': 'nexusmods.com',
    'megamarket_ru_возвраты.sb_orders_2024': 'megamarket_ru_возвраты.sb_orders_2024',
    'горнолыжный курорт роза хутор': 'Горнолыжный курорт Роза Хутор',
    'bitly.com': 'bitly.com',
    'avito 1': 'Avito #1',
    'фомс пензы': 'ФОМС Пензы',
    'фомс курганской области': 'ФОМС Курганской области',
    'bases-brothers.ru': 'bases-brothers.ru',
    'estantevirtual.com.br': 'estantevirtual.com.br',
    'менеджеры nl international': 'Менеджеры NL International',
    'данные сайта wiredbucks.com': 'Данные сайта wiredbucks.com',
    'доставка 2-berega.ru': 'Доставка 2-berega.ru',
    'miltor.ru': 'miltor.ru',
    'ixigo.com': 'ixigo.com',
    'фомс республики башкортостан': 'ФОМС Республики Башкортостан',
    'ульяновской области': 'Ульяновской области',
    'livemaster.ru': 'livemaster.ru',
    'leader-id.ru': 'leader-id.ru',
    'raidforums.com': 'raidforums.com',
    'rozysk2012.ru': 'rozysk2012.ru',
    'toondoo.com': 'toondoo.com',
    'чаты telegram': 'Чаты Telegram',
    'умный дом ростелеком': 'Умный дом Ростелеком',
    'apt-mebel.ru': 'apt-mebel.ru',
    'taxsee_ru.review_clients_2024': 'taxsee_ru.review_clients_2024',
    'best2pay.net': 'best2pay.net',
    'онлайн-сервис везёт всем 1': 'Онлайн-сервис Везёт Всем #1',
    'ip': 'ip',
    'wildberries': 'Wildberries',
    'mc сервер masedworld': 'MC сервер MasedWorld',
    'miit.ru': 'miit.ru',
    'mineland.net': 'mineland.net',
    'гибдд челябинской области': 'ГИБДД Челябинской области',
    'онлайн-сервис везёт всем 2': 'Онлайн-сервис Везёт Всем #2',
    'nashibanki.com.ua': 'nashibanki.com.ua',
    'ингушетии': 'Ингушетии',
    'auto.ru': 'auto.ru',
    'сберлогистика nespresso.com': 'Сберлогистика (nespresso.com)',
    'туристы level.travel': 'Туристы Level.Travel',
    'kia.ru лиды': 'KIA.ru лиды',
    'btc-e.com': 'btc-e.com',
    'пассажиры smartavia.com': 'Пассажиры smartavia.com',
    'ukrsotsbank.com': 'ukrsotsbank.com',
    'сберлогистика oriflame.com': 'Сберлогистика (oriflame.com)',
    'zapovednik96_ru.users_2023': 'zapovednik96_ru.users_2023',
    'пересечение границы': 'Пересечение границы',
    'ржд персонал': 'РЖД Персонал',
    'ciscompany.ru': 'ciscompany.ru',
    'stalker.so': 'stalker.so',
    'фомс астраханской области': 'ФОМС Астраханской области',
    'альфа-банк 1': 'Альфа-Банк #1',
    'права в казахстане': 'Права в Казахстане',
    'фомс псковской области': 'ФОМС Псковской области',
    'xfit.ru': 'xfit.ru',
    'билайн ярославская обл.': 'Билайн (Ярославская обл.)',
    'subagames.com': 'subagames.com',
    'kant.ru пользователи': 'kant.ru пользователи',
    'mandarin.io': 'mandarin.io',
    'pm.ru клиенты': 'PM.ru клиенты',
    'ярославля': 'Ярославля',
    'mticket.com.ua': 'mticket.com.ua',
    'логистика сбербанка': 'Логистика Сбербанка',
    'slpremia.ru': 'slpremia.ru',
    'фссп саратова': 'ФССП Саратова',
    'нальчика': 'Нальчика',
    'почтовые адреса westwing.ru': 'Почтовые адреса westwing.ru',
    'гибдд республики саха': 'ГИБДД Республики Саха',
    'houzz.com': 'houzz.com',
    'dlh.net': 'dlh.net',
    'sushi-master.ru': 'sushi-master.ru',
    'pixlr.com': 'pixlr.com',
    'альфа-страхование': 'Альфа-Страхование',
    'libex.ru': 'libex.ru',
    'renren.com': 'renren.com',
    'сберлогистика yves-rocher.ru': 'Сберлогистика (yves-rocher.ru)',
    'chess.com': 'chess.com',
    'kant.ru заказы': 'kant.ru заказы',
    'vmasshtabe.ru': 'vmasshtabe.ru',
    'gsmforum.ru': 'gsmforum.ru',
    'onebip.com': 'onebip.com',
    'aternos.org': 'aternos.org',
    'данные сайта i-gis.ru': 'Данные сайта i-gis.ru',
    'discord парсинг 1': 'Discord (парсинг) #1',
    'amihome.by': 'Amihome.by',
    'kickex.com': 'kickex.com',
    'bitrix24.ru': 'bitrix24.ru',
    'blankmediagames.com': 'blankmediagames.com',
    'пензы': 'Пензы',
    'фомс калининградской области': 'ФОМС Калининградской области',
    'podnesi.ru заказы': 'podnesi.ru заказы',
    'данные сайта cherlock.ru 2': 'Данные сайта cherlock.ru #2',
    'avito.ru категории': 'Avito.ru категории',
    'proshkolu.ru': 'proshkolu.ru',
    'детский мир': 'Детский Мир',
    'nextgenupdate.com': 'nextgenupdate.com',
    'telderi.ru': 'telderi.ru',
    'cекс-шоп он и она': 'Cекс-шоп Он и Она',
    'perekrestok.ru': 'perekrestok.ru',
    'госпиталь 51 москва': 'Госпиталь 51 (Москва)',
    'erc ur': 'ERC UR',
    'инн кыргызстана': 'ИНН Кыргызстана',
    'mc сервер reallyworld': 'MC сервер ReallyWorld',
    'mpgh.net': 'mpgh.net',
    'pro-syr.ru': 'pro-syr.ru',
    're:store билеты': 're:Store билеты',
    'club.alfabank.ru': 'club.alfabank.ru',
    'расширенный поиск getcontact': 'Расширенный поиск (GetContact)',
    'blackstarwear.ru заказы': 'blackstarwear.ru заказы',
    'stockx.com': 'stockx.com',
    'сберлогистика detmir.ru': 'Сберлогистика (detmir.ru)',
    'авторы tiktok': 'Авторы TikTok',
    'ip регистрация беларусь': 'IP Регистрация Беларусь',
    'edumarket.ru': 'edumarket.ru',
    'spim.ru': 'spim.ru',
    'бизнес персоны osk-ins': 'Бизнес Персоны OSK-INS',
    'pin-up bet': 'Pin-Up Bet',
    'qanat.kz сделки': 'qanat.kz сделки',
    'rtmis.ru': 'rtmis.ru',
    'фмс башкортостан': 'ФМС Башкортостан',
    'волонтеры dobro.ru': 'Волонтеры dobro.ru',
    'mmorg.net': 'mmorg.net',
    'сберлогистика herbalife.ru': 'Сберлогистика (herbalife.ru)',
    'фомс оренбургской области': 'ФОМС Оренбургской области',
    'baza rf': 'Baza RF',
    'armeec.ru': 'armeec.ru',
    'мордовии': 'Мордовии',
    'osk-ins.ru': 'osk-ins.ru',
    'сберлогистика psk-logistics.de': 'Сберлогистика (psk-logistics.de)',
    'warflame.com': 'warflame.com',
    'clixsense.com': 'clixsense.com',
    'сберлогистика nappyclub.ru': 'Сберлогистика (nappyclub.ru)',
    'infourok.ru': 'infourok.ru',
    'водительские права московская область': 'Водительские права (Московская область)',
    'coinmarketcap': 'CoinMarketCap',
    'гибдд армения': 'ГИБДД Армения',
    'maxrealt.ru': 'maxrealt.ru',
    'orionnet_ru.2025': 'orionnet_ru.2025',
    'e1.ru': 'e1.ru',
    'мурманска': 'Мурманска',
    'papiroska.rf': 'papiroska.rf',
    'shadi.com': 'shadi.com',
    '5turistov.ru': '5turistov.ru',
    'tiktop-free.com': 'tiktop-free.com',
    'azur.ru': 'azur.ru',
    'skolkovo.koicrmlead': 'skolkovo.koicrmlead',
    'fitbit.com': 'fitbit.com',
    'over-blog.com': 'over-blog.com',
    'vk games': 'VK Games',
    'фомс иркутской области': 'ФОМС Иркутской области',
    'vkrim.info': 'vkrim.info',
    'emehmon_uz.clients_2025': 'emehmon_uz.clients_2025',
    'stforex.ru': 'stforex.ru',
    'hoster.by': 'hoster.by',
    'vill mix': 'Vill Mix',
    'utair.ru': 'utair.ru',
    'akb.ru': 'akb.ru',
    'ru_mix.petitions_to_president_2021': 'ru_mix.petitions_to_president_2021',
    'k-vrachu.ru': 'k-vrachu.ru',
    'medsi.ru': 'medsi.ru',
    'avvo.com': 'avvo.com',
    'регистрация проживания башкирия': 'Регистрация проживания Башкирия',
    'pochta 2': 'pochta #2',
    'паспорта башкортостан': 'Паспорта Башкортостан',
    'nbki.ru 1': 'nbki.ru #1',
    'propostuplenie.ru': 'propostuplenie.ru',
    'сберлогистика chitai-gorod.ru': 'Сберлогистика (chitai-gorod.ru)',
    'imgur.com': 'imgur.com',
    'гибдд хабаровского края': 'ГИБДД Хабаровского края',
    'gowo.su': 'gowo.su',
    'zunal.com': 'zunal.com',
    'art-talant.org': 'art-talant.org',
    'логи pik-аренда': 'Логи PIK-Аренда',
    'abandonia.com': 'abandonia.com',
    'краснодарского края': 'Краснодарского края',
    'сберлогистика embeauty.ru': 'Сберлогистика (embeauty.ru)',
    'kuchenland.ru': 'kuchenland.ru',
    'avtoto': 'Avtoto',
    'asi.ru': 'asi.ru',
    'pilkinail.ru': 'pilkinail.ru',
    'my.rzd.ru сотрудники': 'my.rzd.ru сотрудники',
    'rabotaitochka.ru': 'rabotaitochka.ru',
    'epl diamond': 'EPL Diamond',
    'вьетнама': 'Вьетнама',
    'игроки shadowcraft.ru': 'Игроки ShadowCraft.ru',
    'labquest.ru': 'labquest.ru',
    'lions-credit.ru': 'lions-credit.ru',
    'citilab 2': 'Citilab #2',
    'башкортостан': 'Башкортостан',
    'xyya.net': 'xyya.net',
    'pharmacosmetica.ru': 'pharmacosmetica.ru',
    'renault.ru': 'renault.ru',
    'фомс сахалинской области': 'ФОМС Сахалинской области',
    'nihonomaru.net': 'nihonomaru.net',
    'jagex.com': 'jagex.com',
    'hh.ru': 'hh.ru',
    'лицензии на оружие украина': 'Лицензии на оружие Украина',
    '11minoxidil.ru': '11minoxidil.ru',
    'discord': 'Discord',
    'job_in_moscow': 'job_in_moscow',
    'должники казахстана': 'Должники Казахстана',
    'tiktok 1': 'TikTok #1',
    'фомс чебоксары': 'ФОМС Чебоксары',
    'гибдд калининградской области': 'ГИБДД Калининградской области',
    'mc сервер meloncraft': 'MC сервер MelonCraft',
    'avrora24_ru.deals_2025': 'avrora24_ru.deals_2025',
    'запросы на загранпаспорта': 'Запросы на загранпаспорта',
    'фомс мурманской области': 'ФОМС Мурманской области',
    'fonbet': 'Fonbet',
    'обращения unistroy.rf': 'Обращения unistroy.rf',
    'justclick.ru': 'justclick.ru',
    'мурманской области': 'Мурманской области',
    'cracked.to': 'cracked.to',
    'mc сервер vimemc': 'MC сервер VimeMC',
    'гибдд республики хакасия': 'ГИБДД Республики Хакасия',
    'milana-shoes.ru': 'milana-shoes.ru',
    'zarina.ru': 'zarina.ru',
    'севастополя': 'Севастополя',
    'onlinegibdd.ru': 'onlinegibdd.ru',
    'zynga.com': 'zynga.com',
    'данные сайта sosedi.by': 'Данные сайта sosedi.by',
    'страхование сбербанк': 'Страхование Сбербанк',
    'хакасия мед. страхование': 'Хакасия мед. страхование',
    'degitaldiction.ru': 'degitaldiction.ru',
    'mc сервер needmine': 'MC сервер needmine',
    'poisondrop.ru': 'poisondrop.ru',
    'hacker.co.kr': 'hacker.co.kr',
    'вкусвилл 1': 'ВкусВилл #1',
    'pochta 3': 'pochta #3',
    'ульяновска': 'Ульяновска',
    'vkmix.com': 'vkmix.com',
    'mc сервер atomcraft': 'MC сервер AtomCraft',
    'buslik.by': 'buslik.by',
    'pikabu': 'Pikabu',
    'сберлогистика ювелирочка.рф': 'Сберлогистика (ювелирочка.рф)',
    'instagram казахстан': 'Instagram Казахстан',
    'themarket.io': 'TheMarket.io',
    'сберлогистика zdravcity.ru': 'Сберлогистика (zdravcity.ru)',
    'регистрация выданных паспортов башкирия': 'Регистрация выданных паспортов Башкирия',
    'суши-бар японский домик': 'Суши-бар Японский домик',
    'яндекс.почты': 'Яндекс.Почты',
    'ммм 2011': 'МММ 2011',
    'сберлогистика ikea.com': 'Сберлогистика (ikea.com)',
    'elance.com': 'elance.com',
    'сберлогистика sbershop.ru': 'Сберлогистика (sbershop.ru)',
    'zvonili.com': 'zvonili.com',
    'tricolor.ru': 'tricolor.ru',
    'maudau.com.ua': 'maudau.com.ua',
    '2035school.ru': '2035school.ru',
    'sms activate': 'SMS Activate',
    'parfumcity.com.ua': 'parfumcity.com.ua',
    'бота дариполучай': 'бота ДариПолучай',
    'gamesalad.com': 'gamesalad.com',
    'otzyvy.pro': 'Otzyvy.pro',
    'gametuts.com': 'gametuts.com',
    'lookbook.nu': 'lookbook.nu',
    'leykaclub_com.2023': 'leykaclub_com.2023',
    'arenda-022.ru посты': 'arenda-022.ru посты',
    'триколор': 'Триколор',
    'olx.ua': 'OLX.ua',
    'euro-ins.ru': 'euro-ins.ru',
    'сберлогистика moscowbooks.ru': 'Сберлогистика (moscowbooks.ru)',
    'vgaps.ru': 'vgaps.ru',
    'объявления avito': 'Объявления Avito',
    'blackmarketreloaded': 'BlackMarketReloaded',
    'unionepro.ru': 'unionepro.ru',
    'klub31.ru': 'klub31.ru',
    'библиотека znanium.com': 'Библиотека znanium.com',
    'mc сервер spicemc': 'MC сервер SpiceMC',
    'millennium-platform.ru': 'millennium-platform.ru',
    'lolz.guru': 'lolz.guru',
    'headhunter': 'HeadHunter',
    'openstreetmap россия': 'OpenStreetMap Россия',
    'vmmo.ru': 'vmmo.ru',
    'openbonus24.ru': 'openbonus24.ru',
    'live4fun.ru': 'live4fun.ru',
    'parisnail.ru': 'ParisNail.ru',
    'mgnl.ru': 'MGNL.ru',
    'сберлогистика joom.com': 'Сберлогистика (joom.com)',
    'vbulletin': 'vBulletin',
    'вакансии пятёрочка': 'Вакансии Пятёрочка',
    'gamecom.com': 'gamecom.com',
    'atalyst bigline харьков': 'Atalyst Bigline Харьков',
    'нод rusnod.ru': 'НОД rusnod.ru',
    'мегафон вологодская область': 'Мегафон (Вологодская область)',
    'cannabis.com': 'cannabis.com',
    'sgroshi.ua': 'sgroshi.ua',
    'youla.ru': 'youla.ru',
    'jivo.ru': 'jivo.ru',
    'ndv.ru': 'ndv.ru',
    'psyonix.com': 'psyonix.com',
    'google_mix': 'google_mix',
    '3djobs': '3DJobs',
    'nic-snail.ru': 'nic-snail.ru',
    'rshoes.ru': 'Rshoes.ru',
    'для пробива': 'Для пробива',
    'petflow.com': 'petflow.com',
    'poloniex.com': 'poloniex.com',
    'sportmarafon.ru': 'sportmarafon.ru',
    'данные сайта yue.com': 'Данные сайта yue.com',
    'amakids.ru': 'amakids.ru',
    'regme.online': 'regme.online',
    'viva деньги': 'VIVA Деньги',
    'aptekanevis.ru': 'aptekanevis.ru',
    'olx.kz': 'olx.kz',
    'w-motors.ru': 'w-motors.ru',
    'memberreportaccess.com': 'memberreportaccess.com',
    'vedomosti.ru': 'vedomosti.ru',
    'btc-alpha': 'BTC-Alpha',
    'водители яндекс.такси': 'Водители Яндекс.Такси',
    'данные сайта ddo.com': 'Данные сайта ddo.com',
    'сберлогистика alenka.ru': 'Сберлогистика (alenka.ru)',
    'lotro.com': 'lotro.com',
    'сберлогистика vogue-gallery.ru': 'Сберлогистика (vogue-gallery.ru)',
    'universarium.org': 'universarium.org',
    'game.kaidown.com': 'game.kaidown.com',
    'mangatraders.com': 'mangatraders.com',
    'mc сервер sandplex': 'MC сервер SandPlex',
    'thewarinc.com': 'thewarinc.com',
    'funimation.com': 'funimation.com',
    'payad.me': 'payad.me',
    'medi-center.ru': 'medi-center.ru',
    'минтруд': 'Минтруд',
    'ortek.ru': 'ortek.ru',
    'citilab часть 1': 'Citilab Часть 1',
    'сберлогистика sammybeauty.ru': 'Сберлогистика (sammybeauty.ru)',
    'mmdm.ru': 'mmdm.ru',
    'tcp.com.ua': 'tcp.com.ua',
    'яндекс.еда курьеры': 'Яндекс.Еда Курьеры',
    'mybusiness.rf': 'MyBusiness.rf',
    'регистрация смертей чувашия': 'Регистрация смертей Чувашия',
    'prosushi.ru': 'prosushi.ru',
    'sexclub.ru': 'sexclub.ru',
    'доставка pirogidomoy.ru': 'Доставка pirogidomoy.ru',
    'hazecash.com': 'hazecash.com',
    'регистрация браков чувашия': 'Регистрация браков Чувашия',
    'гибдд чувашии': 'ГИБДД Чувашии',
    'avito ярославская область': 'Avito Ярославская область',
    'legal drugs': 'Legal Drugs',
    'basemarket.ru заказы': 'basemarket.ru заказы',
    'coinmama.com': 'coinmama.com',
    'brazzers.com': 'brazzers.com',
    'urok-1c.ru': 'urok-1c.ru',
    'ebay customers': 'eBay Customers',
    'forento.ru': 'forento.ru',
    'telegram волонтеры': 'Telegram волонтеры',
    'телефоны westwing.ru': 'Телефоны westwing.ru',
    'extremekids.ru': 'extremeKids.ru',
    'forumcommunity.net': 'forumcommunity.net',
    'ok.ru': 'OK.ru',
    'biletik.aero': 'biletik.aero',
    'shops': 'shops',
    'яндекс.практикум': 'Яндекс.Практикум',
    'avast.com': 'avast.com',
    'avito ульяновск': 'Avito Ульяновск',
    'нтв': 'НТВ',
    'каналов telegram': 'каналов Telegram',
    'klimat-master.ru': 'klimat-master.ru',
    'xpgamesaves.com': 'xpgamesaves.com',
    'fast-anime.ru': 'fast-anime.ru',
    'сберлогистика shopotam.com': 'Сберлогистика (shopotam.com)',
    'convex.ru': 'convex.ru',
    'leonardo.ru': 'leonardo.ru',
    'funny-games.biz': 'funny-games.biz',
    'two-step.ru': 'two-step.ru',
    'физики микс': 'Физики Микс',
    'diskuuion': 'DiskuUion',
    'corevin.com': 'corevin.com',
    'napopravku': 'napopravku',
    'yingjiesheng.com': 'yingjiesheng.com',
    'nomer.io': 'nomer.io',
    'skolkovo.contact1': 'skolkovo.contact1',
    'storybird.com': 'storybird.com',
    'gmail.com': 'gmail.com',
    'babynames.net': 'babynames.net',
    'hongfire.com': 'hongfire.com',
    'poryadok.ru': 'poryadok.ru',
    'rekrute.com': 'rekrute.com',
    'сберлогистика rivegauche.ru': 'Сберлогистика (rivegauche.ru)',
    'mira1.ru': 'Mira1.ru',
    'p.ua': 'p.ua',
    'сберлогистика aravia.ru': 'Сберлогистика (aravia.ru)',
    'kickstarter.com': 'kickstarter.com',
    'unn mix беларусь': 'UNN Mix Беларусь',
    'youhack': 'YouHack',
    'mosershop.ru': 'mosershop.ru',
    'blackstarwear.ru клиенты': 'blackstarwear.ru клиенты',
    'qiannao.com': 'qiannao.com',
    'pandachef.ru заказы': 'pandachef.ru заказы',
    'social-apteka.ru': 'social-apteka.ru',
    'forums.cdprojektred.com': 'forums.cdprojektred.com',
    'skillbox.ru': 'skillbox.ru',
    'by mix кредиты': 'BY Mix кредиты',
    'mamcupy.ru': 'mamcupy.ru',
    'mortalonline.com': 'mortalonline.com',
    'coinbulb.com': 'coinbulb.com',
    'heroleague.ru': 'heroleague.ru',
    'dontcraft': 'DontCraft',
    'запросы эцп липецк': 'Запросы ЭЦП Липецк',
    'kvadroom.ru': 'kvadroom.ru',
    'rozetka.com.ua': 'rozetka.com.ua',
    'bombardir.ru': 'bombardir.ru',
    'gamesnord.com': 'gamesnord.com',
    'сберлогистика sberbank.cards': 'Сберлогистика (sberbank.cards)',
    'leomax': 'Leomax',
    'водоканал гомель': 'Водоканал Гомель',
    'данные сайта free.navalny.com': 'Данные сайта free.navalny.com',
    'jobinruregion.ru': 'jobinruregion.ru',
    'kinomania.ru': 'kinomania.ru',
    'сберлогистика myhalsa.ru': 'Сберлогистика (myhalsa.ru)',
    'faucethub.io': 'faucethub.io',
    'регистрации гомель': 'Регистрации Гомель',
    'форму utorrent': 'Форму uTorrent',
    'blackspigotmc': 'BlackSpigotMC',
    'letovo.ru': 'letovo.ru',
    'emuparadise.me': 'emuparadise.me',
    'ultratrade.ru': 'ultratrade.ru',
    'сберлогистика roadtothedream.com': 'Сберлогистика (roadtothedream.com)',
    'mprofiko.ru': 'mprofiko.ru',
    'mc сервер litecloud.me': 'MC сервер LiteCloud.me',
    'kuking.net': 'kuking.net',
    'yippi': 'Yippi',
    'перехват sms': 'Перехват SMS',
    'romantino.ru': 'romantino.ru',
    'moneyman.org': 'moneyman.org',
    'green coffee': 'Green Coffee',
    'ubrir': 'UBRIR',
    'сберлогистика lrworld.ru': 'Сберлогистика (lrworld.ru)',
    'stickam.com': 'stickam.com',
    'teplo.od.ua': 'teplo.od.ua',
    'icigarette.ru': 'ICigarette.ru',
    'getcontact numbuster': 'GetContact & Numbuster',
    'петиции беларусь': 'Петиции Беларусь',
    'kia.ru': 'KIA.ru',
    'infobusiness': 'Infobusiness',
    'anywhere.xxx': 'anywhere.xxx',
    'сберлогистика aliexpress.ru': 'Сберлогистика (aliexpress.ru)',
    'aliexpress.ru': 'aliexpress.ru',
    'dating.de': 'dating.de',
    'сберлогистика amway.ru': 'Сберлогистика (amway.ru)',
    'zoon.ru': 'zoon.ru',
    'ffshrine.org': 'ffshrine.org',
    'intellego.com': 'intellego.com',
    'rabota-33.ru': 'rabota-33.ru',
    'неизвестный дамп россия': 'Неизвестный дамп Россия',
    'сберлогистика trokot.ru': 'Сберлогистика (trokot.ru)',
    'ubu.ru': 'ubu.ru',
    'ueber18.de': 'ueber18.de',
    'ostrov-chistoty.by': 'ostrov-chistoty.by',
    'сотрудники beeline': 'Сотрудники Beeline',
    'buxp.org': 'buxp.org',
    'capitalgames.com': 'capitalgames.com',
    'хакасия загс': 'Хакасия ЗАГС',
    'uteka.ua': 'uteka.ua',
    'geekbrains': 'GeekBrains',
    'telegram 3': 'Telegram #3',
    'alta-karter.ru': 'alta-karter.ru',
    'invest-elevrus.com': 'invest-elevrus.com',
    'сберлогистика barrier.ru bwf.ru': 'Сберлогистика (barrier.ru, bwf.ru)',
    'slivup.net': 'slivup.net',
    'eosago21-vek.ru': 'EOSAGO21-Vek.ru',
    'fsfera.ru': 'fsfera.ru',
    'загс хакасия': 'ЗАГС Хакасия',
    'hitfinex': 'hitfinex',
    'kiwitaxi.ru': 'kiwitaxi.ru',
    '3delectronics.ru': '3DElectronics.ru',
    'breached.vc': 'breached.vc',
    'profstazhirovki.rf': 'profstazhirovki.rf',
    'originalam.net': 'originalam.net',
    'okmatras.ru': 'okmatras.ru',
    'vertex-club.ru': 'vertex-club.ru',
    '1med.tv': '1med.tv',
    'сберлогистика marykay.ru': 'Сберлогистика (marykay.ru)',
    'void.to': 'Void.to',
    'powerbot.org': 'powerbot.org',
    'мтс вологодская область': 'МТС (Вологодская область)',
    'сберлогистика book24.ru': 'Сберлогистика (book24.ru)',
    'naughty': 'naughty',
    'ip беларусь': 'IP Беларусь',
    'rocketwash.me': 'rocketwash.me',
    'volgofarm.ru': 'volgofarm.ru',
    'philharmonia.spb.ru': 'philharmonia.spb.ru',
    'doxbin.com': 'doxbin.com',
    'логи detologiya': 'Логи Detologiya',
    'разводы чувашия': 'Разводы Чувашия',
    'euronote.hu': 'euronote.hu',
    'prostoporno.club': 'prostoporno.club',
    'сберлогистика alliance.ru': 'Сберлогистика (alliance.ru)',
    'kupitkorm_rf.2024': 'kupitkorm_rf.2024',
    'aslife.ru': 'aslife.ru',
    'ska.ru': 'SKA.ru',
    'белгазпромбанк': 'Белгазпромбанк',
    'robek.ru': 'robek.ru',
    'skolkovo.auth_contactfastdata': 'skolkovo.auth_contactfastdata',
    'asias_uz.customers_2023': 'asias_uz.customers_2023',
    'sadurala.com': 'sadurala.com',
    'edaboard.com': 'edaboard.com',
    'казино vavanda': 'Казино Vavanda>',
    'avito москва': 'Avito Москва',
    'логи sberlog': 'Логи Sberlog',
    '72it.ru': '72it.ru',
    'gameawards.ru': 'gameawards.ru',
    'xarakiri.ru': 'xarakiri.ru',
    'сберлогистика gloria-jeans.ru': 'Сберлогистика (gloria-jeans.ru)',
    'проститутки москвы': 'Проститутки Москвы',
    'gemabank.ru': 'gemabank.ru',
    'shoesland_ua.2022': 'shoesland_ua.2022',
    'connectpress.com': 'connectpress.com',
    'сберлогистика 1minoxidil.ru': 'Сберлогистика (1minoxidil.ru)',
    'cheatgamer.com': 'cheatgamer.com',
    'сберлогистика citilink.ru': 'Сберлогистика (citilink.ru)',
    'розыск казахстан не старые': 'Розыск Казахстан (не старые)',
    'карты gor-park.ru': 'Карты Gor-Park.ru',
    'darvin-market.ru': 'darvin-market.ru',
    'v3toys.ru': 'V3Toys.ru',
    'сберлогистика mixit.ru': 'Сберлогистика (mixit.ru)',
    'люди by mix': 'Люди BY Mix',
    'kr.gov.ua': 'kr.gov.ua',
    'fl.ru': 'fl.ru',
    'justiva.ru': 'justiva.ru',
    'сотрудники osp.ru': 'Сотрудники osp.ru',
    'hostinger': 'hostinger',
    'qanat.kz аккаунты': 'qanat.kz аккаунты',
    'sportfood40.ru': 'sportfood40.ru',
    'basarab.ru': 'basarab.ru',
    'prizyvanet.ru': 'prizyvanet.ru',
    'mver24.ru': 'mver24.ru',
    'сберлогистика viasarcina.ru': 'Сберлогистика (viasarcina.ru)',
    'ростелеком курганская область': 'Ростелеком Курганская область',
    'сберлогистика poshvu.ru': 'Сберлогистика (poshvu.ru)',
    'techimo.com': 'techimo.com',
    'paxful.com': 'paxful.com',
    'minefield': 'Minefield',
    'сбербанк право': 'Сбербанк Право',
    'zybes.net': 'zybes.net',
    'банков': 'банков',
    'mc сервер evgexacraft': 'MC сервер EvgexaCraft',
    'mc сервер saintpvp': 'MC сервер SaintPVP',
    'shop.philips.ru': 'shop.philips.ru',
    'payasugym.com': 'payasugym.com',
    'gameshot.net': 'gameshot.net',
    'сберлогистика poryadok.ru': 'Сберлогистика (poryadok.ru)',
    'tibia.net.pl': 'tibia.net.pl',
    'avrora24_ru.contact_2025': 'avrora24_ru.contact_2025',
    'сберлогистика incity.ru': 'Сберлогистика (incity.ru)',
    'aminos.by': 'aminos.by',
    'forbes.com': 'forbes.com',
    'facepunch.com': 'facepunch.com',
    'sbermarket.ru': 'sbermarket.ru',
    'friendsonly.me': 'FriendsOnly.me',
    'pik-аренда': 'PIK-Аренда',
    'litobraz.ru': 'litobraz.ru',
    'cristalix': 'Cristalix',
    'сотрудники ростелеком': 'Сотрудники Ростелеком',
    'паспорта башкирия': 'Паспорта Башкирия',
    'legalizer': 'legalizer',
    'casinomopsa': 'CasinoMopsa',
    'msp29.ru': 'MSP29.ru',
    'qanat.kz клиенты': 'qanat.kz клиенты',
    'instaforex.com': 'instaforex.com',
    'foxybingo.com': 'foxybingo.com',
    'логи stalker.so': 'Логи Stalker.so',
    'barrier.ru заказы': 'barrier.ru заказы',
    'forums.seochat.com': 'forums.seochat.com',
    'hurma_net.2023': 'hurma_net.2023',
    'extremstyle.ua': 'extremstyle.ua',
    'форум allo-internet.ru 2': 'Форум allo-internet.ru #2',
    'anilibra.tv': 'anilibra.tv',
    'delivery club курьеры': 'Delivery Club Курьеры',
    'turniketov.net': 'turniketov.net',
    'openraid.org': 'openraid.org',
    'сберлогистика vodnik.1000size.ru': 'Сберлогистика (vodnik.1000size.ru)',
    'kesko.fi': 'kesko.fi',
    'artnow': 'ArtNow',
    'dakotadostavka.ru': 'dakotaDostavka.ru',
    'россии ские магазины': 'Российские магазины',
    'excurspb.ru': 'excurspb.ru',
    'osp.ru': 'OSP.ru',
    'sumotorrent.sx': 'sumotorrent.sx',
    'redbox.com': 'redbox.com',
    'платежи pik-аренда': 'Платежи PIK-Аренда',
    'расширенный поиск callapp': 'Расширенный поиск (CallApp)',
    'навигатор дети': 'Навигатор Дети',
    'kdl.ru заказы': 'kdl.ru заказы',
    'metropolis moscow': 'Metropolis Moscow',
    'acne.org': 'acne.org',
    'розыск казахстан старые': 'Розыск Казахстан (старые)',
    'сотрудники ozon': 'Сотрудники Ozon',
    'coachella.com': 'coachella.com',
    'com23.ru': 'com23.ru',
    'мфо': 'МФО',
    'сберлогистика 21-shop.ru': 'Сберлогистика (21-shop.ru)',
    'pskb.com': 'PSKB.com',
    'euro-football.ru': 'euro-football.ru',
    'naumen.ru': 'naumen.ru',
    'vird.ru': 'vird.ru',
    'cdek market': 'CDEK Market',
    'igis.ru': 'IGIS.ru',
    'bzmolodost': 'BZMolodost',
    'ifreeads.ru': 'ifreeads.ru',
    'gametag.com': 'gametag.com',
    'радиолюбители снг': 'Радиолюбители СНГ',
    'частные пользователи pik-аренда': 'Частные пользователи PIK-Аренда',
    'intimshop.ru': 'intimshop.ru',
    'форум allo-internet.ru 1': 'Форум allo-internet.ru #1',
    'смена фио саратовская область': 'Смена ФИО Саратовская область',
    'forums.xkcd.com': 'forums.xkcd.com',
    'wealth-start-business.com': 'wealth-start-business.com',
    'playforceone.com': 'playforceone.com',
    'aimjunkies.com': 'AimJunkies.com',
    'альфа-банк спб': 'Альфа-Банк СПБ',
    'сберлогистика shop.greenmama.ru': 'Сберлогистика (shop.greenmama.ru)',
    'qanat.kz кредиты': 'qanat.kz кредиты',
    'сберлогистика faberlic.com': 'Сберлогистика (faberlic.com)',
    'воен-торг.ru': 'Воен-Торг.ru',
    'auto.ria.com': 'auto.ria.com',
    'mycube.ru': 'mycube.ru',
    'torg-sergi': 'torg-sergi',
    'liancaijing.com': 'liancaijing.com',
    'gameogre.com': 'gameogre.com',
    'buffet24.ru': 'buffet24.ru',
    '404035.ru': '404035.ru',
    'сберлогистика kikocosmetics.ru': 'Сберлогистика (kikocosmetics.ru)',
    'банк втб': 'Банк ВТБ',
    'sahibinden.com': 'sahibinden.com',
    'emails ducks.org': 'Emails Ducks.org',
    'сберлогистика nbmart.ru': 'Сберлогистика (nbmart.ru)',
    'mc сервер borkland': 'MC сервер Borkland',
    'atol.ru': 'atol.ru',
    'frostland.ru': 'frostland.ru',
    'creocommunity.com': 'creocommunity.com',
    'clickpay24.ru': 'clickpay24.ru',
    'сберлогистика seltop.ru': 'Сберлогистика (seltop.ru)',
    'yavp.pl': 'YAVP.pl',
    'sanpid.com': 'sanpid.com',
    'сберлогистика 220-volt.ru': 'Сберлогистика (220-volt.ru)',
    'job.ws.ru': 'job.ws.ru',
    'форум linux mint': 'Форум Linux Mint',
    'mc сервер sundex': 'MC сервер SunDex',
    'игроки warframe': 'Игроки Warframe',
    'kdl.ru': 'kdl.ru',
    'rubankov.ru': 'rubankov.ru',
    '7video_by.customers_2025': '7video_by.customers_2025',
    'soderganki.ru': 'soderganki.ru',
    'vk pay parsing': 'VK Pay Parsing',
    'смешанные данные свердловская область': 'Смешанные данные Свердловская область',
    'dixy.ru': 'dixy.ru',
    'wowlife_club.2024': 'wowlife_club.2024',
    'krasotka-market.ru': 'krasotka-market.ru',
    'go4ngineeringjobs.com': 'go4ngineeringjobs.com',
    'wanadoo': 'wanadoo',
    'nasha-oizza-cool.ru': 'nasha-oizza-cool.ru',
    'runescape.backstreetmerch.com': 'runescape.backstreetmerch.com',
    'bizbilla.com': 'bizbilla.com',
    'сберлогистика bombbar.ru': 'Сберлогистика (bombbar.ru)',
    'animalid': 'AnimalID',
    'xakepok.su': 'xakepok.su',
    'kwork': 'Kwork',
    'фомс кировской области': 'ФОМС Кировской области',
    'zimnie.com': 'zimnie.com',
    'infotecs.ru': 'infotecs.ru',
    'farmacent.com': 'farmacent.com',
    'arenda-022.ru пользователи': 'arenda-022.ru пользователи',
    'doxbin 1': 'Doxbin #1',
    'gubernia.ru': 'gubernia.ru',
    'goldenberg trade': 'Goldenberg Trade',
    'fxcash.net': 'FXCash.net',
    'rgbdirect.co.uk': 'rgbdirect.co.uk',
    'sportvokrug': 'Sportvokrug',
    'takamura-eats.ru': 'takamura-eats.ru',
    'сберлогистика lime-shop.ru': 'Сберлогистика (lime-shop.ru)',
    'ias100.in': 'ias100.in',
    'yurkas.by': 'yurkas.by',
    'bendercraft.ru': 'bendercraft.ru',
    'сберлогистика terra-coffee.ru': 'Сберлогистика (terra-coffee.ru)',
    'водители city-mobil.ru': 'Водители city-mobil.ru',
    'design2u.ru': 'Design2U.ru',
    'cafemumu.ru': 'cafemumu.ru',
    'berlin24.ru': 'berlin24.ru',
    'myminigames.com': 'myminigames.com',
    'emails gatehub': 'Emails Gatehub',
    'gfycat.com': 'gfycat.com',
    'subway13.ru': 'subway13.ru',
    'incor-med.ru': 'incor-med.ru',
    'игроки majestic-rp.ru': 'Игроки Majestic-RP.ru',
    'сберлогистика synthesit.ru': 'Сберлогистика (synthesit.ru)',
    'mc сервер epicmc': 'MC сервер EpicMC',
    'konfiscat.ua': 'konfiscat.ua',
    'cardsmobile': 'CardsMobile',
    'malwarebytes': 'Malwarebytes',
    'klerk.ru': 'klerk.ru',
    'mediafire.com': 'mediafire.com',
    'mc сервер ecuacraft': 'MC сервер EcuaCraft',
    'сберлогистика forma-odezhda.ru': 'Сберлогистика (forma-odezhda.ru)',
    'alvasar.ru': 'alvasar.ru',
    'сберлогистика russiandoc.ru': 'Сберлогистика (russiandoc.ru)',
    'сберлогистика re-books.ru': 'Сберлогистика (re-books.ru)',
    'сберлогистика spegat.com': 'Сберлогистика (spegat.com)',
    'сберлогистика grass.su': 'Сберлогистика (grass.su)',
    'сберлогистика centrmag.ru': 'Сберлогистика (centrmag.ru)',
    'сберлогистика eapteka.ru': 'Сберлогистика (eapteka.ru)',
    'сберлогистика medicalarts.ru': 'Сберлогистика (medicalarts.ru)',
    'mc сервер pandamine': 'MC сервер Pandamine',
    'coinpot.co': 'coinpot.co',
    'auto.drom.ru': 'auto.drom.ru',
    'сберлогистика ileatherman.ru': 'Сберлогистика (ileatherman.ru)',
    'mc fawemc': 'MC Fawemc',
    'установление отцовства чувашия': 'Установление отцовства Чувашия',
    'iqrashop.com': 'iqrashop.com',
    'smed': 'SMED',
    'hmps.in': 'hmps.in',
    'spiritfit.ru': 'spiritfit.ru',
    'warcraftrealms.com': 'warcraftrealms.com',
    'pokemoncreed.net': 'pokemoncreed.net',
    'skolkovo.auth_users_bk': 'skolkovo.auth_users_bk',
    'bigsmm.ru': 'bigsmm.ru',
    'bns-club': 'BNS-Club',
    'wonderpolls.com': 'wonderpolls.com',
    'learnfrenchbypodcast.com': 'learnfrenchbypodcast.com',
    'shop.miratorg.ru': 'shop.miratorg.ru',
    'thailionair': 'ThaiLionAir',
    'avito нижний новгород': 'Avito Нижний Новгород',
    'job-piter_ru.2024': 'job-piter_ru.2024',
    'zapovednik96_ru.orders_2023': 'zapovednik96_ru.orders_2023',
    '1belagro.com': '1Belagro.com',
    'сберлогистика kalyanforyou.ru': 'Сберлогистика (kalyanforyou.ru)',
    'qwerty': 'Qwerty',
    'avito московская область': 'Avito Московская область',
    'covid гродно': 'Covid Гродно',
    'колл-центры by mix': 'Колл-центры BY Mix',
    'магазин лента': 'Магазин Лента',
    'madam-broshkina_rf.2023': 'madam-broshkina_rf.2023',
    'сберлогистика sezon-p.ru': 'Сберлогистика (sezon-p.ru)',
    'сберлогистика tempgun.ru': 'Сберлогистика (tempgun.ru)',
    'talkwebber.ru': 'talkwebber.ru',
    'расширенный поиск callrid': 'Расширенный поиск (CallrID)',
    'логистические сотрудники сбербанк': 'Логистические сотрудники Сбербанк',
    'сберлогистика redmachine.ru': 'Сберлогистика (redmachine.ru)',
    'marketdownload.com': 'marketdownload.com',
    'atfbank.kz': 'ATFBank.kz',
    'kaspersky.ru': 'kaspersky.ru',
    'remontnick.ru': 'remontnick.ru',
    'zakupis-ekb.ru': 'zakupis-ekb.ru',
    'расширенный поиск truecaller': 'Расширенный поиск (TrueCaller)',
    'spellforce.com': 'spellforce.com',
    'magazinedee.com': 'magazinedee.com',
    'гибдд чувашия': 'ГИБДД Чувашия',
    'bleachanime.org': 'bleachanime.org',
    'сберлогистика openface.me': 'Сберлогистика (openface.me)',
    'vashdom24.ru': 'vashdom24.ru',
    'регистрация утерянных паспортов башкирия': 'Регистрация утерянных паспортов Башкирия',
    'blistol.ru': 'blistol.ru',
    'duelingnetwork.com': 'duelingnetwork.com',
    'glopart.ru.2015': 'glopart.ru.2015',
    'данные сайта cherlock.ru 1': 'Данные сайта cherlock.ru #1',
    'организации благовещенск': 'Организации Благовещенск',
    'rewasd.com': 'rewasd.com',
    'сберлогистика ccm.ru': 'Сберлогистика (ccm.ru)',
    'nadpo.ru': 'nadpo.ru',
    'сберлогистика technopark.ru': 'Сберлогистика (technopark.ru)',
    'город казань': 'Город Казань',
    'cataloxy_ru.2020': 'cataloxy_ru.2020',
    'комментарии onona': 'Комментарии Onona',
    'oneland.ru': 'oneland.ru',
    'cex.io': 'cex.io',
    'gun.ru': 'gun.ru',
    'datakabel.ru': 'datakabel.ru',
    'сберлогистика leonardo.ru': 'Сберлогистика (leonardo.ru)',
    'photographer.ru': 'photographer.ru',
    'chocofamily.kz 1': 'chocofamily.kz #1',
    'openstreetmap беларусь': 'OpenStreetMap Беларусь',
    'freejob.ru': 'freeJob.ru',
    'bulbul.ru': 'bulbul.ru',
    'сберлогистика electrobaza.ru': 'Сберлогистика (electrobaza.ru)',
    'worldpokertour.com': 'worldpokertour.com',
    'fotoboom.com': 'fotoboom.com',
    'tintenprofi.ch': 'tintenprofi.ch',
    'сберлогистика part-auto.ru': 'Сберлогистика (part-auto.ru)',
    'moto85.ru': 'moto85.ru',
    'profitech.hu': 'profitech.hu',
    'mc сервер aeromine': 'MC сервер AeroMine',
    'сберлогистика unidragon.ru': 'Сберлогистика (unidragon.ru)',
    'gamesforum.de': 'gamesforum.de',
    'сберлогистика novatour.ru': 'Сберлогистика (novatour.ru)',
    'epicgames.com': 'epicgames.com',
    'pharmvestnik.ru': 'pharmvestnik.ru',
    'сберлогистика proficosmetics.ru': 'Сберлогистика (proficosmetics.ru)',
    'сберлогистика authentica.love': 'Сберлогистика (authentica.love)',
    'bazi.guru': 'bazi.guru',
    'forex': 'forex',
    'home credit': 'Home Credit',
    'samolet.ru': 'samolet.ru',
    'xmstore_ru.2024': 'xmstore_ru.2024',
    'сберлогистика prokrasivosti.ru': 'Сберлогистика (prokrasivosti.ru)',
    'сберлогистика vkuskavkaza.ru': 'Сберлогистика (vkuskavkaza.ru)',
    'hairluxe.ru': 'hairluxe.ru',
    'сберлогистика bormash.com': 'Сберлогистика (bormash.com)',
    'usurt.ru': 'usurt.ru',
    'сберлогистика waistshop.ru': 'Сберлогистика (waistshop.ru)',
    'runelite.net': 'runelite.net',
    'game-shop.ua': 'game-shop.ua',
    'navalny': 'Navalny',
    'uniqom.ru': 'uniqom.ru',
    'сберлогистика bork.ru': 'Сберлогистика (bork.ru)',
    'blackhatprotools.net': 'blackhatprotools.net',
    'pivosibir.2019': 'pivosibir.2019',
    'сберлогистика lampstory.ru': 'Сберлогистика (lampstory.ru)',
    'style-ampire': 'Style-Ampire',
    'openstreetmap украина': 'OpenStreetMap Украина',
    'eletroplus.blogspot.com': 'eletroplus.blogspot.com',
    'bhf.io': 'bhf.io',
    'mythicalworld.net': 'mythicalworld.net',
    'сберлогистика wellmart-opt.ru': 'Сберлогистика (wellmart-opt.ru)',
    'autovse.kz': 'autovse.kz',
    'организации беларуси': 'Организации Беларуси',
    'интернет-портал uchi.ru': 'Интернет-портал uchi.ru',
    'teamextrememc.com': 'teamextrememc.com',
    'stroydvor.su': 'stroydvor.su',
    'zeep_com_ua.2023': 'zeep_com_ua.2023',
    'сберлогистика shop.maxkatz.ru': 'Сберлогистика (shop.maxkatz.ru)',
    'forums.linuxmint.com': 'forums.linuxmint.com',
    'сберлогистика parfumerovv.ru': 'Сберлогистика (parfumerovv.ru)',
    'сберлогистика arma-toys.ru': 'Сберлогистика (arma-toys.ru)',
    'sloganbase_ru.members_2023': 'sloganbase_ru.members_2023',
    'gamefuelmasters.ru': 'GameFuelMasters.ru',
    'tpprf.ru': 'TPPRF.ru',
    'брест by mix': 'Брест BY Mix',
    'academy.tn.ru': 'academy.tn.ru',
    'сберлогистика chipdip.ru': 'Сберлогистика (chipdip.ru)',
    'сберлогистика missnude.ru': 'Сберлогистика (missnude.ru)',
    'forums.manacube.com': 'forums.manacube.com',
    'dow-clinic.ru': 'dow-clinic.ru',
    'gatehub': 'Gatehub',
    'сберлогистика shop.fclm.ru': 'Сберлогистика (shop.fclm.ru)',
    'alloplus.by': 'alloplus.by',
    'windhanenergy.io': 'windhanenergy.io',
    'cheryomushki.ru': 'cheryomushki.ru',
    'сберлогистика welltex.ru': 'Сберлогистика (welltex.ru)',
    'сберлогистика firstrest.ru': 'Сберлогистика (firstrest.ru)',
    'shotbow.net': 'shotbow.net',
    'rg.ru': 'RG.ru',
    'survmed.ru': 'survmed.ru',
    'сберлогистика camping-elite.ru': 'Сберлогистика (camping-elite.ru)',
    'сберлогистика pc-1.ru': 'Сберлогистика (pc-1.ru)',
    'короленко клиника россия': 'Короленко Клиника (Россия)',
    'покупки cdek': 'Покупки CDEK',
    'sloganbase_ru.users_2023': 'sloganbase_ru.users_2023',
    'kripta.ru': 'kripta.ru',
    'vkuss-sushi_ru.bot_users_2022': 'vkuss-sushi_ru.bot_users_2022',
    'telecom media': 'Telecom Media',
    'сберлогистика lisi.ru': 'Сберлогистика (lisi.ru)',
    'mc сервер marsworld': 'MC сервер MarsWorld',
    'cryptosam.net': 'cryptosam.net',
    'lacedrecords.co': 'lacedrecords.co',
    'btc60.net': 'btc60.net',
    'ingruz.ru': 'ingruz.ru',
    'pubpit.com': 'pubpit.com',
    'malindoair': 'MalindoAir',
    'сберлогистика sos-ka.ru': 'Сберлогистика (sos-ka.ru)',
    'forums.gre.net': 'forums.gre.net',
    'epicnpc.com': 'epicnpc.com',
    'kaboom.ru': 'kaboom.ru',
    'mosdosug.com': 'mosdosug.com',
    'сберлогистика unistok.ru': 'Сберлогистика (unistok.ru)',
    'magazin-restoran.ru': 'magazin-restoran.ru',
    'zakupki.rt.ru': 'zakupki.rt.ru',
    'forum.dvdrbase.info': 'forum.dvdrbase.info',
    'uralchem.ru': 'uralchem.ru',
    'nemez1da.ru': 'nemez1da.ru',
    'аннулирование утерянных паспортов башкирия': 'Аннулирование утерянных паспортов Башкирия',
    'ростовгаз': 'РостовГаз',
    'pharmgeocom.ru': 'pharmgeocom.ru',
    'абакан': 'Абакан',
    'vps.it': 'vps.it',
    'xoffer.hk': 'xoffer.hk',
    'tritongear.ru': 'tritongear.ru',
    'сберлогистика barrier.ru': 'Сберлогистика (barrier.ru)',
    'kdl.ru экспресс-доставка': 'kdl.ru экспресс-доставка',
    'сберлогистика msk-spartak.ru': 'Сберлогистика (msk-spartak.ru)',
    'openstreetmap казахстан': 'OpenStreetMap Казахстан',
    '1c school': '1C School',
    'onona': 'Onona',
    'forex-investor.net': 'forex-investor.net',
    'gun59.ru': 'Gun59.ru',
    'сберлогистика kodbox.ru': 'Сберлогистика (kodbox.ru)',
    'mydocuments36.ru': 'mydocuments36.ru',
    'terrasoft': 'Terrasoft',
    'fptaxi.ru': 'FPTaxi.ru',
    'bongo-bong_ru.sb_orders_2024': 'bongo-bong_ru.sb_orders_2024',
    'bondagestory.biz': 'bondagestory.biz',
    'сберлогистика leddeco.ru': 'Сберлогистика (leddeco.ru)',
    'hostmonster.com': 'hostmonster.com',
    'rukodelov_ru.sb_orders_2024': 'rukodelov_ru.sb_orders_2024',
    'hackgive.me': 'hackgive.me',
    'pm.ru': 'PM.ru',
    'euroschoolindia.com': 'euroSchoolIndia.com',
    'americanbeauty club': 'AmericanBeauty Club',
    'iqos': 'IQOS',
    'opt-opt-opt.ru': 'opt-opt-opt.ru',
    'mc сервер fawemc': 'MC сервер FaweMC',
    'forumdate.ru': 'forumdate.ru',
    'nutrimun_ru.sb_orders_2024': 'nutrimun_ru.sb_orders_2024',
    'rusdosug.com': 'rusdosug.com',
    'mandarin bank': 'Mandarin Bank',
    'bitshacking.com': 'bitshacking.com',
    'hydrogenplatform.com': 'hydrogenplatform.com',
    'hudognik.net': 'hudognik.net',
    'moneycontrol.com': 'moneycontrol.com',
    'сберлогистика figurist.ru': 'Сберлогистика (figurist.ru)',
    'frozencraft.ru': 'frozencraft.ru',
    'vivv-sposa': 'vivv-sposa',
    'willway.ru': 'willway.ru',
    'участники фбк': 'Участники ФБК',
    'alpmarathon': 'AlpMarathon',
    'muzonews.ru': 'muzonews.ru',
    'dehashed': 'dehashed',
    'finfive.ru': 'FinFive.ru',
    'cre8asiteforums.com': 'cre8asiteforums.com',
    'domadengi самара': 'DomaDengi Самара',
    'bit2visitor.com': 'bit2visitor.com',
    'dogewallet.com': 'dogewallet.com',
    'смена имени чувашия': 'Смена имени Чувашия',
    'телефоны ярцево': 'Телефоны Ярцево',
    'jobmada.com': 'jobmada.com',
    'radiokey.ru': 'radiokey.ru',
    'bitscircle.com': 'bitscircle.com',
    'butterflylabs.com': 'butterflylabs.com',
    'юридические лица': 'Юридические лица',
    'openstreetmap_org.uzbekistan_2025_04': 'openstreetmap_org.uzbekistan_2025_04',
    'craftboard.pl': 'craftboard.pl',
    'mc сервер justpex': 'MC сервер JustPex',
    'dezir-clinic.ru': 'dezir-clinic.ru',
    'italonceramica.ru': 'italonceramica.ru',
    'upsidedowncake.ru': 'UpsideDownCake.ru',
    'vipfish.ru': 'VIPFish.ru',
    'kraken.com': 'kraken.com',
    'mineserwer.pl': 'mineserwer.pl',
    'megatorrent.ru': 'megatorrent.ru',
    'knopka1.рф': 'knopka1.рф',
    'kovrik30.ru': 'Kovrik30.ru',
    'blackhatdevil.com': 'blackhatdevil.com',
    'minecraft 6g6s.org': 'Minecraft 6g6s.org',
    'начало school': 'Начало School',
    'roll20.net': 'roll20.net',
    'wallet.btc.com': 'wallet.btc.com',
    'xclan.org': 'xclan.org',
    'valvegator.ru': 'valveGator.ru',
    'vanitymc.co': 'vanitymc.co',
    'insurance': 'Insurance',
    'korolenko_klinika.clients_not_rus_2015': 'korolenko_klinika.clients_not_rus_2015',
    'boardakshop': 'BoardakShop',
    'cannabiscardpay.com': 'cannabiscardpay.com',
    'monashop.ru': 'monashop.ru',
    'clubfactory.com': 'clubfactory.com',
    'льготы гомель': 'Льготы Гомель',
    'колледж новый оскол': 'Колледж Новый Оскол',
    'zvero_ru.2024': 'zvero_ru.2024',
    'vitaexpress.ru': 'VitaExpress.ru',
    'zloadr.com': 'zloadr.com',
    'beri-ruli.ru': 'beri-ruli.ru',
    'powerlogo': 'PowerLogo',
    'prokatalog.ru': 'prokatalog.ru',
    'coinbase': 'Coinbase',
    'drinkme.ru': 'drinkme.ru',
    'demonforums.net': 'demonforums.net',
    'беларусь зельва': 'Беларусь Зельва',
    'spohesap.com': 'spohesap.com',
    'mc сервер womplay': 'MC сервер WomPlay',
    'mc сервер bobermc': 'MC сервер BoberMC',
    'gatehub.com': 'gatehub.com',
    'rdt-info.ru': 'RDT-Info.ru',
    'гибдд элиста': 'ГИБДД Элиста',
    'moscow-sun.ru': 'moscow-sun.ru',
    'phoenix-plus.ru': 'phoenix-plus.ru',
    'developers-heaven.net': 'developers-heaven.net',
    'holzmebel_ru.2024': 'holzmebel_ru.2024',
    'ru.bidspirit.com': 'Ru.BidSpirit.com',
    'депутаты россии': 'Депутаты России',
    'viperc.net': 'viperc.net',
    'qwertypay': 'qwertypay',
    'tools-profi_ru.2024': 'tools-profi_ru.2024',
    'craftapple.com': 'craftapple.com',
    'skolkovo.backoffice_invoices': 'skolkovo.backoffice_invoices',
    'worldcrafteros.net': 'worldcrafteros.net',
    'cindao_rus.2022': 'cindao_rus.2022',
    'justskins.com': 'justskins.com',
    'должники ярославской области': 'Должники Ярославской области',
    'shoppingbitcoins.com': 'shoppingbitcoins.com',
    'bitcoin.lixter.com': 'bitcoin.lixter.com',
    'издательство русское слово': 'Издательство Русское слово',
    'coins.numizmat.net': 'coins.numizmat.net',
    'mc сервер buildcraft': 'MC сервер BuildCraft',
    'foodpark_rf.2022': 'foodpark_rf.2022',
    'safeskyhacks.com': 'safeskyhacks.com',
    'varoxcraft.de': 'varoxcraft.de',
    'cool-motors.ru': 'cool-motors.ru',
    'omegacraft.cl': 'omegacraft.cl',
    'mc сервер towercraft': 'MC сервер TowerCraft',
    'merlinsmagicbitcoins.com': 'merlinsmagicbitcoins.com',
    'mover24.ru': 'Mover24.ru',
    'mc сервер booksmine': 'MC сервер BooksMine',
    'hitlerattacks.com': 'hitlerattacks.com',
    'утерянные паспорта башкирия': 'Утерянные паспорта Башкирия',
    'suvenirka24.ru': 'suvenirka24.ru',
    'sbmt bsu by': 'SBMT BSU BY',
    'mc сервер hiddenmc': 'MC сервер HiddenMC',
    'mc сервер codemine': 'MC сервер CodeMine',
    'mc сервер 144.91.64.167': 'MC сервер 144.91.64.167',
    'skolkovo.1c_manual': 'skolkovo.1c_manual',
    'awesome.database': 'awesome.database',
    'maps_yandex_ru.parsing_2024': 'maps_yandex_ru.parsing_2024',
    'ua_mix.callcenter_2023': 'ua_mix.callcenter_2023'
}


def normkey(s: str) -> str:
    """Normalize arbitrary source names to a stable lookup key."""
    import re as _re
    s = (s or "").lower()
    s = _re.sub(r"\s*\[[^\]]*\]\s*", "", s)
    s = _re.sub(r"[«»\"“”‚‘’]", "", s)
    s = _re.sub(r"[^a-z0-9а-яёіїєґ _\-./:@+]+", " ", s, flags=_re.I)
    s = _re.sub(r"\s+", " ", s).strip()
    return s

def normalize_source_name(s: str) -> str:
    raw = (s or "").strip()
    low = raw.lower()

    # 0) сначала быстрый поиск среди известных источников (по нормализованному ключу)
    nk = normkey(raw)
    if nk in KNOWN_SOURCES:
        return KNOWN_SOURCES[nk]

    # 1) прямые алиасы по подстроке
    for k, v in SOURCE_ALIASES.items():
        if k in low:
            return v

    # 2) чистка от непонятных символов (оставим буквы/цифры/базовую пунктуацию/пробел)
    cleaned = re.sub(r"[^A-Za-zА-Яа-я0-9 .,_\-+/()&:]", "", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 3) Если после чистки получилось что-то похожее на известный ключ — попробуем ещё раз
    nk2 = normkey(cleaned)
    if nk2 in KNOWN_SOURCES:
        return KNOWN_SOURCES[nk2]

    # 4) для коротких аббревиатур делаем верхний регистр
    if cleaned and len(cleaned) <= 4 and cleaned.replace(" ", "").isalpha():
        return cleaned.upper()

    return cleaned or "Источник"
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

    nav_ul = soup.select_one('nav .navigation_ul')
    mnav_ul = soup.select_one('#mnav .navigation_ul')
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
        friendly = normalize_source_name(src)
        header.string = f"{friendly} [{year}]" if year else friendly
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
            a.string = normalize_source_name(src)
            li.append(a)
            if nav_ul:
                nav_ul.append(li)
            if mnav_ul:
                li2 = soup.new_tag('li')
                a2 = soup.new_tag('a', href=f"#{safe_id}", **{'class': 'navigation_link'})
                a2.string = normalize_source_name(src)
                li2.append(a2)
                mnav_ul.append(li2)

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


# === Injected: queries log support ===
def _ensure_queries_log_table():
    try:
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS queries_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query_text TEXT,
                created_at INTEGER,
                result_count INTEGER DEFAULT 0,
                html_b64 TEXT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qlog_user ON queries_log(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_qlog_created ON queries_log(created_at)")
    except Exception as e:
        logging.warning("cannot ensure queries_log table: %s", e)

async def _qlog_on_startup(app):
    _ensure_queries_log_table()

app = web.Application()
app.on_startup.append(_qlog_on_startup)
app.router.add_get('/health', health)
app.router.add_route('*','/webhook', SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET))
app.router.add_post('/cryptopay', cryptopay_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)



# === Injected: admin query log handlers ===
def _qlog_render_page(page: int = 0, per_page: int = 10):
    offset = page * per_page
    rows = c.execute(
        "SELECT id,user_id,query_text,created_at,result_count FROM queries_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    ).fetchall()
    total = c.execute("SELECT COUNT(*) FROM queries_log").fetchone()[0]
    lines = ["<b>📜 История запросов</b>"]
    if not rows:
        lines.append("Пока пусто.")
    else:
        for rid, uid, q, ts, cnt in rows:
            dt = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
            uname = c.execute("SELECT COALESCE(NULLIF(username,''), '') FROM users WHERE id=?", (uid,)).fetchone()
            uname = uname[0] if uname and uname[0] else ""
            title = f"@{uname}" if uname else f"ID {uid}"
            lines.append(f"• <b>{title}</b> — <code>{q}</code> — {dt} — {cnt} записей")
    text = "\n".join(lines)
    # keyboard: download buttons + pagination
    kb_rows = []
    for rid, uid, q, ts, cnt in rows:
        kb_rows.append([InlineKeyboardButton(text=f"📄 Скачать «{q[:20]}…»", callback_data=f"qlog_dl:{rid}")])
    # Pagination
    max_page = (total - 1) // per_page if total else 0
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"qlog_page:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"qlog_page:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="🏠 В админ-меню", callback_data="admin_home")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)

@dp.callback_query(F.data == 'qlog_menu')
async def qlog_menu(call: CallbackQuery):
    try:
        if not is_admin(call.from_user.id):
            return await call.answer()
        if need_start(call.from_user.id):
            await ask_press_start(call.message.chat.id)
            return await call.answer()
        text, kb = _qlog_render_page(0, 10)
        await admin_render(call, text, kb)
        await call.answer()
    except Exception as e:
        logging.exception('qlog_menu failed: %s', e)
        try:
            await call.answer('Ошибка истории', show_alert=True)
        except:
            pass

@dp.callback_query(F.data.startswith('qlog_page:'))
async def qlog_page(call: CallbackQuery):
    try:
        if not is_admin(call.from_user.id):
            return await call.answer()
        if need_start(call.from_user.id):
            await ask_press_start(call.message.chat.id)
            return await call.answer()
        try:
            page = int(call.data.split(':',1)[1])
        except:
            page = 0
        text, kb = _qlog_render_page(page, 10)
        await admin_render(call, text, kb)
        await call.answer()
    except Exception as e:
        logging.exception('qlog_page failed: %s', e)
        try:
            await call.answer('Ошибка истории', show_alert=True)
        except:
            pass

@dp.callback_query(F.data.startswith('qlog_dl:'))
async def qlog_dl(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer()
    if need_start(call.from_user.id):
        await ask_press_start(call.message.chat.id); return await call.answer()
    try:
        rid = int(call.data.split(':',1)[1])
    except:
        return await call.answer('Ошибка id')
    row = c.execute("SELECT user_id,query_text,created_at,html_b64 FROM queries_log WHERE id=?", (rid,)).fetchone()
    if not row:
        return await call.answer('Нет записи')
    uid, q, ts, b64 = row
    if not b64:
        return await call.answer('HTML не сохранён')
    try:
        import tempfile, base64, os
        html = base64.b64decode(b64.encode('ascii'))
        with tempfile.NamedTemporaryFile('wb', delete=False, suffix='.html', dir='/tmp') as tf:
            tf.write(html)
            path = tf.name
        await call.message.answer_document(FSInputFile(path, filename=f"{q}.html"))
        try: os.unlink(path)
        except: pass
    except Exception as e:
        logging.warning("qlog download failed: %s", e)
        await call.answer('Ошибка отправки файла')


if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=PORT)
