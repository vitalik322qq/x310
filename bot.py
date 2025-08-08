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
    CallbackQuery, FSInputFile
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

# === Подключение к БД ===
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

# === Запрещённые запросы для админа ===
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович','10.07.1999','10.07.99',
    '380636659255','0636659255','+380636659255',
    '+380683220001','0683220001','380683220001',
    'bodia.kohan322@gmail.com','vitalik322vitalik@gmail.com'
]

# === Инициализация бота ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# === Состояния FSM ===
class AdminStates(StatesGroup):
    wait_user_id        = State()
    wait_request_amount = State()
    wait_username       = State()
    wait_reset_id       = State()

# === Вспомогательные функции ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔒 29 дней – $49',      callback_data='buy_month')],
        [InlineKeyboardButton(text='🔒 89 дней – $120',     callback_data='buy_quarter')],
        [InlineKeyboardButton(text='🔒 Пожизненно – $299',   callback_data='buy_lifetime')],
        [InlineKeyboardButton(text='🧊 Скрыть данные – $100',callback_data='buy_hide_data')],
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
        'SELECT hidden_data,free_used,trial_expired FROM users WHERE id=?',(uid,)
    ).fetchone()
    if hd:
        welcome = '<b>Ваши данные скрыты.</b>'
    elif te:
        welcome = '<b>Триал окончен.</b>'
    else:
        rem = TRIAL_LIMIT - fu
        welcome = f'<b>Осталось {rem} бесплатных запросов.</b>' if rem>0 else '<b>Триал окончен.</b>'
    await message.answer(f"👾 Добро пожаловать в n3l0x!\n{welcome}", reply_markup=sub_keyboard())

@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_',1)[1]
    if plan not in TARIFFS:
        return await callback.answer('Неизвестный план', show_alert=True)
    price = TARIFFS[plan]['price']
    payload = f"pay_{callback.from_user.id}_{plan}_{int(time.time())}"
    body = {
        'asset': BASE_CURRENCY,
        'amount': str(price),
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

@dp.message(Command('admin322'))
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Выдать запросы', callback_data='give_requests')],
        [InlineKeyboardButton(text='🚫 Заблокировать', callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Разблокировать',callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Сброс триала',  callback_data='reset_menu')],
    ])
    await message.answer('<b>Панель администратора:</b>', reply_markup=kb)

# — Выдать запросы —
@dp.callback_query(F.data=='give_requests')
async def give_requests(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer('🆔 Введите ID пользователя:')
    await state.set_state(AdminStates.wait_user_id)

@dp.message(AdminStates.wait_user_id)
async def set_user_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer('ID должен быть числом')
    await state.update_data(uid=int(msg.text))
    await msg.answer('🔢 Введите количество запросов (1–10):')
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def set_requests(msg: Message, state: FSMContext):
    data = await state.get_data()
    n = msg.text
    if not n.isdigit() or not (1 <= int(n) <= 10):
        return await msg.answer('Введите число от 1 до 10')
    c.execute(
        'INSERT INTO users(id,requests_left) VALUES(?,?) '
        'ON CONFLICT(id) DO UPDATE SET requests_left=excluded.requests_left',
        (data['uid'], int(n))
    )
    conn.commit()
    await msg.answer(f"✅ Пользователю {data['uid']} выдано {n} запросов.")
    await state.clear()

# — Блокировка / Разблокировка —
@dp.callback_query(F.data=='block_user')
async def block_user(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer('👤 Введите @username для блокировки:')
    await state.update_data(mode='block')
    await state.set_state(AdminStates.wait_username)

@dp.callback_query(F.data=='unblock_user')
async def unblock_user(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer('👤 Введите @username для разблокировки:')
    await state.update_data(mode='unblock')
    await state.set_state(AdminStates.wait_username)

@dp.message(AdminStates.wait_username)
async def change_block(msg: Message, state: FSMContext):
    data = await state.get_data()
    uname = msg.text.strip().lstrip('@')
    c.execute('UPDATE users SET is_blocked=? WHERE username=?',
              (1 if data['mode']=='block' else 0, uname))
    conn.commit()
    await msg.answer(f"{data['mode'].capitalize()}ed @{uname}")
    await state.clear()

# — Сброс триала —
@dp.callback_query(F.data=='reset_menu')
async def reset_menu(call: CallbackQuery):
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔁 Сбросить у всех', callback_data='reset_all')],
        [InlineKeyboardButton(text='🔍 Сбросить по ID',  callback_data='reset_by_id')],
    ])
    await call.message.answer('Выберите режим сброса:', reply_markup=kb)

@dp.callback_query(F.data=='reset_all')
async def reset_all(call: CallbackQuery, state: FSMContext):
    cursor = conn.execute('UPDATE users SET free_used=0, trial_expired=0')
    conn.commit()
    count = cursor.rowcount
    logging.info(f"reset_all: affected {count} users")
    await call.answer()
    await call.message.answer(f"✅ Сброшен триал у всех ({count} пользователей).")
    await state.clear()

@dp.callback_query(F.data=='reset_by_id')
async def reset_by_id(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer('🆔 Введите ID для сброса:')
    await state.set_state(AdminStates.wait_reset_id)

@dp.message(AdminStates.wait_reset_id)
async def do_reset_by_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer('ID должен быть числом')
    uid = int(msg.text)
    cursor = conn.execute(
        'UPDATE users SET free_used=0, trial_expired=0 WHERE id=?', (uid,)
    )
    conn.commit()
    count = cursor.rowcount
    logging.info(f"reset_by_id: user {uid}, affected {count} rows")
    if count:
        await msg.answer(f"✅ Сброс триала для пользователя {uid}.")
    else:
        await msg.answer(f"⚠️ Пользователь с ID {uid} не найден.")
    await state.clear()

# — Поиск и HTML-отчёт —
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

    # Доступ
    if not is_admin(uid):
        if is_blocked:
            return await message.answer('🚫 Вы заблокированы.')
        if hidden_data:
            return await message.answer('🚫 Ваши данные скрыты.')
        if subs_until <= now and trial_expired:
            return await message.answer('🔐 Триал закончился. Подпишитесь.', reply_markup=sub_keyboard())
        if q in ADMIN_HIDDEN:
            return await message.answer('🚫 Запрос запрещён.')
        if check_flood(uid):
            return await message.answer('⛔ Слишком часто. Попробуйте позже.')

        if requests_left > 0:
            c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?', (uid,))
        else:
            if subs_until <= now:
                if free_used < TRIAL_LIMIT:
                    c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?', (uid,))
                    if free_used + 1 >= TRIAL_LIMIT:
                        c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                else:
                    c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
                    conn.commit()
                    return await message.answer('🔐 Триал окончен.', reply_markup=sub_keyboard())
        conn.commit()

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
            'passport_number':'Номер паспорта','passport_date':'Дата выдачи паспорта'
        }
        return m.get(k, k)

    blocks = []
    for itm in data['data']['items']:
        hits = itm.get('hits', {}).get('items', [])
        src  = itm.get('source', {}).get('database', '?')
        if not hits: continue
        rows = "".join(
            f"<tr><td>{beautify(k)}</td><td>{', '.join(v) if isinstance(v, (list,tuple)) else v}</td></tr>"
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
<html lang="ru"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>n3l0x Report</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Permanent+Marker&display=swap');
body {{ margin:0; background:#000; color:#E0E0E0; font-family:Courier,monospace; }}
h1 {{ text-align:center; padding:16px; color:#39FF14; text-shadow:0 0 6px #39FF14; }}
.report {{ display:flex; flex-wrap:wrap; justify-content:center; gap:16px; padding:16px; }}
.block {{ background:#111; border:2px solid #39FF14; border-radius:8px; width:320px; overflow:auto; }}
.block .graffiti {{ margin:12px 0; font-family:'Permanent Marker',monospace; color:#FF33CC; text-shadow:0 0 8px #FF33CC; text-align:center; font-size:1.2em; }}
table {{ width:100%; border-collapse:collapse; margin-bottom:16px; }}
th {{ background:#39FF14; color:#000; padding:8px; }}
td {{ background:#222; color:#E0E0E0; padding:8px; }}
tr:nth-child(even) td {{ background:#1A1A1A; }}
@media(max-width:600px) {{ .block {{ width:90%; }} }}
</style>
</head><body>
<h1>n3l0x Intelligence Report</h1>
<div class="report">{''.join(blocks)}</div>
</body></html>"""

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
    await message.answer(f"📊 Подписка: {sub}\nБесплатно осталось: {free}\nРучных: {rl}")

@dp.message(Command('help'))
async def help_handler(message: Message):
    await message.answer(
        "/status – статус\n"
        "/help   – это сообщение\n"
        "Отправьте текст для поиска."
    )

# === Вебхуки ===
async def health(request): return web.Response(text='OK')

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

app = web.Application()
app.router.add_get('/health', health)
app.router.add_route('*','/webhook',
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET))
app.router.add_post('/cryptopay', cryptopay_webhook)

app.on_startup.append(lambda _: asyncio.create_task(
    bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)) if WEBHOOK_URL else None)
app.on_shutdown.append(lambda _: asyncio.create_task(bot.delete_webhook()) or conn.close())

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=PORT)