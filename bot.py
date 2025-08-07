import os
import logging
import time
import asyncio
import tempfile
import sqlite3
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

# === Settings from ENV ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
USERSBOX_API_KEY = os.getenv('USERSBOX_API_KEY')
CRYPTOPAY_API_TOKEN = os.getenv('CRYPTOPAY_API_TOKEN')  # Crypto Pay API token
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
BASE_CURRENCY = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
DB_PATH = os.getenv('DATABASE_PATH', 'n3lox_users.db')

# === Constants ===
TARIFFS = {
    'month':    {'price': 49,  'days': 29},
    'quarter':  {'price': 120, 'days': 89},
    'lifetime': {'price': 299, 'days': 9999},
    'hide_data':{'price': 100, 'days': 0}
}
TRIAL_LIMIT    = 3
FLOOD_WINDOW   = 15
FLOOD_LIMIT    = 10
FLOOD_INTERVAL = 3

# === Database Setup ===
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()
# Create tables
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
CREATE TABLE IF NOT EXISTS payments (
    payload TEXT PRIMARY KEY,
    user_id INTEGER,
    plan TEXT,
    paid_at INTEGER
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
)
""")
conn.commit()

# Admin hidden queries
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович', '10.07.1999', '10.07.99',
    'Кохан Богдан Олегович 10.07.1999', 'Кохан Богдан Олегович 10.07.99',
    '380636659255', '0636659255', '+380636659255',
    '+380683220001', '0683220001', '380683220001'
]

# === Bot Initialization ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# === FSM States ===
class AdminStates(StatesGroup):
    wait_user_id        = State()
    wait_request_amount = State()
    wait_username       = State()
    wait_reset_id       = State()

# === Helpers ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔒 29 days - $49',  callback_data='buy_month')],
        [InlineKeyboardButton(text='🔒 89 days - $120', callback_data='buy_quarter')],
        [InlineKeyboardButton(text='🔒 Lifetime - $299', callback_data='buy_lifetime')],
        [InlineKeyboardButton(text='🧊 Hide my data - $100', callback_data='buy_hide_data')]
    ])

def is_subscribed(uid: int) -> bool:
    c.execute('SELECT subs_until FROM users WHERE id=?', (uid,))
    row = c.fetchone()
    return bool(row and row[0] > int(time.time()))

def check_flood(uid: int) -> bool:
    c.execute('SELECT last_queries FROM users WHERE id=?', (uid,))
    row = c.fetchone() or ['',]
    now = int(time.time())
    times = [int(t) for t in row[0].split(',') if t] + [now]
    recent = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute('UPDATE users SET last_queries=? WHERE id=?', (','.join(map(str, recent)), uid))
    conn.commit()
    return len(recent) > FLOOD_LIMIT or (len(recent)>=2 and recent[-1]-recent[-2] < FLOOD_INTERVAL)

# === Handlers ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    text = message.text or ''
    parts = text.split(maxsplit=1)
    arg = parts[1] if len(parts)>1 else ''
    # Cleanup old deep-link flow
    if arg.startswith('merchant_'):
        # ignore
        pass
    # Ensure user row
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?', (message.from_user.username, uid))
    conn.commit()
    # Build welcome text
    c.execute('SELECT hidden_data, free_used, trial_expired FROM users WHERE id=?', (uid,))
    hd, fu, te = c.fetchone()
    if hd:
        welcome_text = '<b>Your data is hidden.</b>'
    else:
        if te == 1:
            welcome_text = '<b>Your trial ended.</b>'
        else:
            rem = max(0, TRIAL_LIMIT - fu)
            welcome_text = f'<b>You have {rem} free searches left.</b>' if rem > 0 else '<b>Your trial ended.</b>'
    await message.answer(
        f"👾 Welcome to n3лoх!\n{welcome_text}",
        reply_markup=sub_keyboard()
    )

@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_',1)[1]
    if plan not in TARIFFS:
        return await callback.answer('Unknown plan', show_alert=True)
    price = TARIFFS[plan]['price']
    payload = f"pay_{callback.from_user.id}_{plan}_{int(time.time())}"
    body = {
        'asset': BASE_CURRENCY,
        'amount': str(price),
        'description': f"n3l0x: {plan} plan",
        'payload': payload,
        'allow_comments': False,
        'allow_anonymous': True,
        'expires_in': 1800,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://pay.crypt.bot/api/createInvoice',
                headers={'Crypto-Pay-API-Token': CRYPTOPAY_API_TOKEN},
                json=body,
                timeout=10,
            ) as resp:
                data = await resp.json()
    except Exception as e:
        logging.exception('createInvoice failed: %s', e)
        return await callback.message.answer('⚠️ Failed to contact Crypto Pay. Try again later.')
    if not data.get('ok'):
        return await callback.message.answer(f"⚠️ Crypto Pay error: {data}")
    inv = data['result']
    url = inv.get('bot_invoice_url') or inv.get('pay_url')
    if not url:
        return await callback.message.answer('⚠️ Unexpected Crypto Pay response.')
    try:
        c.execute('INSERT OR IGNORE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',
                  (payload, callback.from_user.id, plan, 0))
        conn.commit()
    except Exception:
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='💳 Pay in CryptoBot', url=url)]])
    await callback.message.answer(
        f"💳 You chose <b>{plan}</b> — <b>${price}</b> in {BASE_CURRENCY}.\n"
        f"Tap the button below to pay via @CryptoBot.",
        reply_markup=kb,
    )
    await callback.answer()

@dp.message(Command("admin322"))
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Give Requests', callback_data='give_requests')],
        [InlineKeyboardButton(text='🚫 Block User',    callback_data='block_user')],
        [InlineKeyboardButton(text='✅ Unblock User',  callback_data='unblock_user')],
        [InlineKeyboardButton(text='🔄 Reset Trial',   callback_data='reset_trial')]
    ])
    await message.answer('<b>Admin Panel:</b>', reply_markup=kb)

# Give Requests
@dp.callback_query(F.data=='give_requests')
async def give_requests(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer('Access denied', show_alert=True)
    await call.answer()
    await call.message.answer('🆔 Enter user ID:')
    await state.set_state(AdminStates.wait_user_id)

@dp.message(AdminStates.wait_user_id)
async def set_user_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer('ID must be numeric')
    await state.update_data(uid=int(msg.text))
    await msg.answer('🔢 Enter number of requests (1-10):')
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def set_requests(msg: Message, state: FSMContext):
    data = await state.get_data()
    if not msg.text.isdigit() or not (1 <= int(msg.text) <= 10):
        return await msg.answer('Enter a number between 1 and 10')
    c.execute(
        'INSERT INTO users(id,requests_left) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET requests_left=excluded.requests_left',
        (data['uid'], int(msg.text))
    )
    conn.commit()
    await msg.answer(f"✅ Granted {msg.text} requests to user {data['uid']}")
    await state.clear()

# Block / Unblock
@dp.callback_query(F.data=='block_user')
async def block_user_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer('Access denied', show_alert=True)
    await call.answer()
    await call.message.answer('👤 Enter username to block (without @):')
    await state.update_data(mode='block_user')
    await state.set_state(AdminStates.wait_username)

@dp.callback_query(F.data=='unblock_user')
async def unblock_user_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer('Access denied', show_alert=True)
    await call.answer()
    await call.message.answer('👤 Enter username to unblock (without @):')
    await state.update_data(mode='unblock_user')
    await state.set_state(AdminStates.wait_username)

@dp.message(AdminStates.wait_username)
async def change_block(msg: Message, state: FSMContext):
    data = await state.get_data()
    uname = msg.text.strip().lstrip('@')
    if data.get('mode') == 'block_user':
        c.execute('UPDATE users SET is_blocked=1 WHERE username=?', (uname,))
        await msg.answer(f'🚫 Blocked @{uname}')
    else:
        c.execute('UPDATE users SET is_blocked=0 WHERE username=?', (uname,))
        await msg.answer(f'✅ Unblocked @{uname}')
    conn.commit()
    await state.clear()

# Reset Trial
@dp.callback_query(F.data=='reset_trial')
async def reset_trial_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer('Access denied', show_alert=True)
    await call.answer()
    await call.message.answer('🆔 Enter user ID to reset trial:')
    await state.set_state(AdminStates.wait_reset_id)

@dp.message(AdminStates.wait_reset_id)
async def reset_trial_execute(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer('❌ ID must be numeric')
    uid = int(msg.text)
    c.execute('UPDATE users SET free_used=?, trial_expired=1 WHERE id=?', (TRIAL_LIMIT, uid))
    conn.commit()
    await msg.answer(f'🔄 Trial reset for user {uid}.')
    await state.clear()

# Search Handler
@dp.message(~F.text.startswith('/'))
async def search_handler(message: Message):
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?', (message.from_user.username, message.from_user.id))
        conn.commit()
    uid = message.from_user.id
    query = (message.text or '').strip()
    c.execute('SELECT is_blocked, hidden_data, requests_left, free_used, subs_until, trial_expired FROM users WHERE id=?', (uid,))
    row = c.fetchone() or (0,0,0,0,0,0)
    is_blocked, hidden_data, requests_left, free_used, subs_until, trial_expired = row
    now = int(time.time())
    if is_blocked:
        return await message.answer('🚫 You are blocked.')
    if hidden_data:
        return await message.answer('🚫 This user data is hidden.')
    if subs_until <= now and trial_expired == 1:
        return await message.answer('🔐 Trial over. Please subscribe.', reply_markup=sub_keyboard())
    if query in ADMIN_HIDDEN:
        return await message.answer('🚫 This query is prohibited.')
    if check_flood(uid):
        return await message.answer('⛔ Flood detected. Try again later.')
    if requests_left > 0:
        c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?', (uid,))
        conn.commit()
    else:
        if subs_until <= now and free_used >= TRIAL_LIMIT:
            c.execute('UPDATE users SET trial_expired=1 WHERE id=?', (uid,))
            conn.commit()
            return await message.answer('🔐 Trial over. Please subscribe.', reply_markup=sub_keyboard())
        if subs_until <= now:
            c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?', (uid,))
            c.execute('UPDATE users SET trial_expired=1 WHERE id=? AND free_used>=?', (uid, TRIAL_LIMIT))
            conn.commit()
    c.execute('SELECT 1 FROM blacklist WHERE value=?', (query,))
    if c.fetchone():
        return await message.answer('🔒 Access denied.')
    # API Call
    await message.answer(
        f"🕷️ Connecting to nodes...\n" +
        f"🧬 Running recon on <code>{query}</code>"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.usersbox.ru/v1/search?q={query}",
                headers={'Authorization': USERSBOX_API_KEY},
                timeout=10
            ) as resp:
                if resp.status != 200:
                    return await message.answer(f'⚠️ API error: {resp.status}')
                result = await resp.json()
    except (ClientError, asyncio.TimeoutError) as e:
        logging.error(f'API request failed: {e}')
        return await message.answer('⚠️ Network error. Try again later.')
    if result.get('status') != 'success' or result.get('data', {}).get('count', 0) == 0:
        return await message.answer('📡 No match found.')
    def beautify_key(k):
        m = {'full_name':'Имя','phone':'Телефон','inn':'ИНН', 'email':'Email',
             'first_name':'Имя','last_name':'Фамилия','middle_name':'Отчество',
             'birth_date':'Дата рождения','gender':'Пол','passport_series':'Серия паспорта',
             'passport_number':'Номер паспорта','passport_date':'Дата выдачи паспорта'}
        return m.get(k,k)
    blocks = []
    for item in result['data']['items']:
        hdr = f"☠ {item.get('source',{}).get('database','?')}"
        lines=[]
        for hit in item.get('hits',{}).get('items',[]):
            for k,v in hit.items():
                if not v: continue
                val = ', '.join(str(x) for x in (v if isinstance(v,list) else [v]))
                lines.append(f"<div class='row'><span class='key'>{beautify_key(k)}</span>:"
                             f"<span class='val'>{val}</span></div>")
        if lines:
            blocks.append(f"<div class='block'><div class='header'>{hdr}</div>{''.join(lines)}</div>")
    blocks_html = ''.join(blocks)
    html = f"""
    <html>
    <head><meta charset='UTF-8'><title>n3l0x OSINT Report</title><style>
    body {{background:#0a0a0a;color:#f0f0f0;font-family:'Courier New',monospace;
          padding:20px;display:flex;flex-direction:column;align-items:center;}}
    .block {{background:#111;border:1px solid #333;border-radius:10px;
             padding:15px;margin-bottom:20px;width:100%;max-width:800px;
             box-shadow:0 0 10px #00ffcc55;}}
    .header {{font-size:18px;color:#00ffcc;margin-bottom:10px;font-weight:bold;}}
    .row {{display:flex;justify-content:space-between;padding:6px 0;
            border-bottom:1px dotted #444;}}
    .key {{color:#66ff66;font-weight:bold;min-width:40%;}}
    .val {{color:#ff4de6;font-weight:bold;word-break:break-word;text-align:right;}}
    @media(max-width:600px){{.row{{flex-direction:column;align-items:flex-start;}}
                           .val{{text-align:left;}}}}
    </style></head>
    <body><h1 style='color:#00ffcc;'>n3l0x Intelligence Report</h1>{blocks_html}</body>
    </html>
    """
    with tempfile.NamedTemporaryFile('w',delete=False,suffix='.html',dir='/tmp',encoding='utf-8') as tf:
        tf.write(html); tmp_path=tf.name
    await message.answer('Found data, sending report...')
    await message.answer_document(FSInputFile(tmp_path, filename=f"{query}.html"))
    try: os.remove(tmp_path)
    except: pass

@dp.message(Command("status"))
async def status_handler(message: Message):
    uid = message.from_user.id
    c.execute('SELECT subs_until, free_used, hidden_data, requests_left, trial_expired FROM users WHERE id=?',(uid,))
    subs_until, free_used, hidden_data, requests_left, trial_expired = c.fetchone() or (0,0,0,0,0)
    now = int(time.time())
    if hidden_data: return await message.answer('🔒 Your data is hidden.')
    sub_status = ('active until '+datetime.fromtimestamp(subs_until).strftime('%Y-%m-%d %H:%M:%S')) if subs_until>now else 'not active'
    rem_trial = 0 if trial_expired==1 else max(0, TRIAL_LIMIT-free_used)
    await message.answer(f"📊 Status:\nSubscription: {sub_status}\nFree left: {rem_trial}\nManual left: {requests_left}")

@dp.message(Command("help"))
async def help_handler(message: Message):
    await message.answer(
        "/status - view subscription and limits\n"
        "/help - this help message\n"
        "Send any text to search."
    )

async def health(request): return web.Response(text='OK')

# Cryptopay webhook
async def cryptopay_webhook(request: web.Request):
    try: data = await request.json()
    except: return web.json_response({'ok': True})
    inv = data.get('invoice') or data.get('payload') or {}
    status = inv.get('status'); payl = inv.get('payload')
    if status == 'paid' and payl:
        parts = str(payl).split('_')
        if len(parts)>=4 and parts[0]=='pay':
            try:
                uid=int(parts[1]); plan=parts[2]; now=int(time.time())
                if plan=='hide_data': c.execute('UPDATE users SET hidden_data=1 WHERE id=?',(uid,))
                elif plan in TARIFFS:
                    days=TARIFFS[plan]['days']; subs=now+days*86400
                    c.execute('INSERT INTO users(id,subs_until,free_used) VALUES(?,?,?) '
                              'ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until',
                              (uid,subs,0))
                c.execute('INSERT OR REPLACE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',
                          (payl,uid,plan,now))
                conn.commit()
                try: await bot.send_message(uid, f"✅ Payment received. Plan '{plan}' activated.")
                except: pass
            except Exception:
                logging.exception('activate payment failed')
    return web.json_response({'ok': True})

# Webhook setup
app = web.Application()
app.router.add_get('/health', health)
app.router.add_route('*', '/webhook', SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET))
app.router.add_post('/cryptopay', cryptopay_webhook)
app.on_startup.append(lambda app: bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET))
app.on_shutdown.append(lambda app: (bot.delete_webhook(), conn.close()))

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')))
