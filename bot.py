import os
import logging
import time
import tempfile
import sqlite3
import asyncio
from datetime import datetime

import aiohttp
from aiohttp import web, ClientError
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile, BotCommand,
    BotCommandScopeAllPrivateChats, BotCommandScopeChat
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
    'month':     {'price': 49,  'days': 29},
    'quarter':   {'price': 120, 'days': 89},
    'lifetime':  {'price': 299, 'days': 9999},
    'hide_data': {'price': 100, 'days': 0},
}
TRIAL_LIMIT    = 3
FLOOD_WINDOW   = 15
FLOOD_LIMIT    = 10
FLOOD_INTERVAL = 3
PAGE_SIZE      = 10  # на страницу в списках

# === БД ===
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
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
    is_blocked    INTEGER DEFAULT 0
)""")
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
conn.commit()

# === Админ-запрещённые запросы ===
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович','10.07.1999','10.07.99',
    '380636659255','0636659255','+380636659255',
    '+380683220001','0683220001','380683220001',
    'bodia.kohan322@gmail.com','vitalik322vitalik@gmail.com'
]

# === Бот ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# === FSM ===
class AdminStates(StatesGroup):
    wait_grant_amount = State()
    wait_username     = State()
    wait_reset_id     = State()

# === Утилиты ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔒 29 дней – $49',        callback_data='buy_month')],
        [InlineKeyboardButton(text='🔒 89 дней – $120',       callback_data='buy_quarter')],
        [InlineKeyboardButton(text='🔒 Пожизненно – $299',     callback_data='buy_lifetime')],
        [InlineKeyboardButton(text='🧊 Скрыть данные – $100',  callback_data='buy_hide_data')],
    ])

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

# === Пагинация пользователей для админки ===
PAGE_SIZE = 10
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

# === Команды меню Telegram (синяя кнопка) ===
async def setup_menu_commands():
    # Команды для всех приватных чатов (обычные пользователи)
    user_cmds = [
        BotCommand(command="start",  description="Запуск"),
        BotCommand(command="status", description="Статус подписки и лимитов"),
        BotCommand(command="help",   description="Справка"),
    ]
    await bot.set_my_commands(user_cmds, scope=BotCommandScopeAllPrivateChats())

    # Команды для админа (добавляем /admin322)
    if OWNER_ID:
        admin_cmds = user_cmds + [BotCommand(command="admin322", description="Панель администратора")]
        try:
            await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=OWNER_ID))
        except Exception as e:
            logging.warning(f"Не удалось выставить команды для OWNER_ID {OWNER_ID}: {e}")

# === Хендлеры ===

@dp.message(CommandStart())
async def start_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
              (uid,0,0,0))
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?',
                  (message.from_user.username, uid))
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

# === Админ-меню ===
@dp.message(Command('admin322'))
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Выдать запросы',    callback_data='give_requests')],
        [InlineKeyboardButton(text='🚫 Заблокировать',     callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Разблокировать',    callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Завершить триал',   callback_data='reset_menu')],
    ])
    await message.answer('<b>Панель администратора:</b>', reply_markup=kb)

@dp.callback_query(F.data == 'admin_home')
async def admin_home(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Выдать запросы',    callback_data='give_requests')],
        [InlineKeyboardButton(text='🚫 Заблокировать',     callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Разблокировать',    callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Завершить триал',   callback_data='reset_menu')],
    ])
    if call.message:
        await call.message.edit_text('<b>Панель администратора:</b>', reply_markup=kb)
    else:
        await call.answer()

# === Листинги пользователей ===
@dp.callback_query(F.data == 'give_requests')
async def give_requests_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.answer()
    kb = users_list_keyboard(action='give', page=0)
    await call.message.answer('👥 Выберите пользователя для выдачи запросов:', reply_markup=kb)

@dp.callback_query(F.data == 'block_user')
async def block_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.answer()
    kb = users_list_keyboard(action='block', page=0)
    await call.message.answer('👥 Кого заблокировать?', reply_markup=kb)

@dp.callback_query(F.data == 'unblock_user')
async def unblock_user_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.answer()
    kb = users_list_keyboard(action='unblock', page=0)
    await call.message.answer('👥 Кого разблокировать?', reply_markup=kb)

@dp.callback_query(F.data == 'reset_menu')
async def reset_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔁 Завершить триал у всех', callback_data='reset_all')],
        [InlineKeyboardButton(text='🔍 Завершить триал у конкретного', callback_data='reset_pick')],
        [InlineKeyboardButton(text='🏠 В админ-меню', callback_data='admin_home')],
    ])
    await call.message.answer('Выберите режим завершения триала:', reply_markup=kb)

@dp.callback_query(F.data == 'reset_pick')
async def reset_pick_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    await call.answer()
    kb = users_list_keyboard(action='reset', page=0)
    await call.message.answer('👥 Выберите пользователя для завершения триала:', reply_markup=kb)

@dp.callback_query(F.data.startswith('list:'))
async def paginate_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
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
    if not is_admin(call.from_user.id):
        return await call.answer()
    _, action, uid_s, page_s = call.data.split(':', 3)
    uid = int(uid_s)
    row = c.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
    uname = row[0] if row and row[0] else f'ID {uid}'

    if action == 'give':
        await state.update_data(grant_uid=uid)
        await call.message.answer(f'Выбран @{uname if uname!="ID "+str(uid) else uname}.\n'
                                  '🔢 Введите количество запросов (1–100):')
        await state.set_state(AdminStates.wait_grant_amount)
    elif action == 'block':
        c.execute('UPDATE users SET is_blocked=1 WHERE id=?', (uid,))
        conn.commit()
        await call.message.answer(f'🚫 Заблокирован @{uname if uname!="ID "+str(uid) else uname}.')
    elif action == 'unblock':
        c.execute('UPDATE users SET is_blocked=0 WHERE id=?', (uid,))
        conn.commit()
        await call.message.answer(f'✅ Разблокирован @{uname if uname!="ID "+str(uid) else uname}.')
    elif action == 'reset':
        c.execute('UPDATE users SET free_used=?, trial_expired=1 WHERE id=?', (TRIAL_LIMIT, uid))
        conn.commit()
        await call.message.answer(f'🔄 Триал завершён для @{uname if uname!="ID "+str(uid) else uname}.')
    else:
        await call.message.answer('Неизвестное действие.')
    await call.answer()

@dp.message(AdminStates.wait_grant_amount)
async def grant_amount_input(msg: Message, state: FSMContext):
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

# === Поиск и HTML ===
@dp.message(F.text & ~F.text.startswith('/'))
async def search_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',
              (uid,0,0,0))
    conn.commit()

    q = message.text.strip()
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?',
                  (message.from_user.username, uid))
        conn.commit()

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
        if q in ADMIN_HIDDEN:
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

    if c.execute('SELECT 1 FROM blacklist WHERE value=?', (q,)).fetchone():
        return await message.answer('🔒 Доступ запрещён.')

    await message.answer(f"🕷️ Выполняется поиск для <code>{q}</code>…")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.usersbox.ru/v1/search',
                headers={'Authorization': USERSBOX_API_KEY},
                params={'q': q}, timeout=10
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

    blocks = []
    for itm in data['data']['items']:
        hits = itm.get('hits', {}).get('items', [])
        src  = itm.get('source', {}).get('database', '?')
        if not hits:
            continue
        rows = "".join(
            f"<tr><td>{beautify(k)}</td><td>{', '.join(str(x) for x in v) if isinstance(v, (list, tuple)) else v}</td></tr>"
            for h in hits for k, v in h.items() if v
        )
        blocks.append(f"""
<div class="block">
  <h2 class="graffiti">{src}</h2>
  <table>
    <thead><tr><th>Поле</th><th>Значение</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>""")

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>n3l0x Intelligence Report</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Inconsolata&display=swap');
    body {{ margin: 0; background: #0b0c10; color: #c5c6c7; font-family: 'Inconsolata', monospace; line-height: 1.4; }}
    h1 {{ text-align: center; padding: 20px 0; margin: 0; color: #66fcf1; font-family: 'Orbitron', sans-serif; text-shadow: 0 0 8px rgba(102,252,241,0.8); background: #1f2833; }}
    .report {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; padding: 16px; }}
    .block {{ background: #1f2833; border: 1px solid #66fcf1; border-radius: 8px; box-shadow: 0 0 10px rgba(102,252,241,0.2); overflow: hidden; }}
    .block .graffiti {{ margin: 0; padding: 12px; font-family: 'Orbitron', sans-serif; color: #66fcf1; text-shadow: 0 0 6px rgba(102,252,241,0.7); font-size: 1.1em; text-align: center; background: #0b0c10; border-bottom: 1px solid #66fcf1; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 8px; text-align: left; font-size: 0.9em; word-break: break-word; }}
    th {{ background: #45a29e; color: #0b0c10; font-weight: 400; text-transform: uppercase; letter-spacing: 0.05em; border: 1px solid #0b0c10; }}
    td {{ background: #0b0c10; color: #c5c6c7; border: 1px solid #1f2833; }}
    tr:nth-child(even) td {{ background: #1f2833; }}
    @media (max-width: 600px) {{ h1 {{ font-size: 1.4em; }} .graffiti {{ font-size: 1em; }} }}
  </style>
</head>
<body>
  <h1>n3l0x Intelligence Report</h1>
  <div class="report">{''.join(blocks)}</div>
</body>
</html>"""

    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html', dir='/tmp', encoding='utf-8') as tf:
        tf.write(html)
        path = tf.name

    await message.answer_document(FSInputFile(path, filename=f"{q}.html"))
    os.unlink(path)

@dp.message(Command('status'))
async def status_handler(message: Message):
    uid = message.from_user.id
    subs, fu, hd, rl, te = c.execute(
        'SELECT subs_until,free_used,hidden_data,requests_left,trial_expired FROM users WHERE id=?',
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
    await message.answer(
        "/status – статус\n"
        "/help   – справка\n"
        "/admin322 – панель администратора (только у админа)\n"
        "Отправьте любой текст для поиска."
    )

# === Webhook endpoints ===
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
                old = c.execute('SELECT subs_until FROM users WHERE id=?', (uid,)).fetchone()[0]
                ns = max(now, old) + TARIFFS[plan]['days']*86400
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
    # webhook
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    # меню команд
    await setup_menu_commands()
    logging.info("Меню команд установлено.")

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
