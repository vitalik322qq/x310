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
BOT_TOKEN           = os.getenv('BOT_TOKEN')
USERSBOX_API_KEY    = os.getenv('USERSBOX_API_KEY')
CRYPTOPAY_API_TOKEN = os.getenv('CRYPTOPAY_API_TOKEN')
OWNER_ID            = int(os.getenv('OWNER_ID', '0'))
BASE_CURRENCY       = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL         = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET      = os.getenv('WEBHOOK_SECRET')
DB_PATH             = os.getenv('DATABASE_PATH', 'n3lox_users.db')
PORT                = int(os.getenv('PORT', '8080'))

# === Constants ===
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

# === Database Setup ===
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

# === Admin hidden queries ===
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович','10.07.1999','10.07.99',
    '380636659255','0636659255','+380636659255',
    '+380683220001','0683220001','380683220001',
    'bodia.kohan322@gmail.com','vitalik322vitalik@gmail.com'
]

# === Bot Init ===
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
        [InlineKeyboardButton('🔒 29 days – $49',     callback_data='buy_month')],
        [InlineKeyboardButton('🔒 89 days – $120',    callback_data='buy_quarter')],
        [InlineKeyboardButton('🔒 Lifetime – $299',    callback_data='buy_lifetime')],
        [InlineKeyboardButton('🧊 Hide my data – $100',callback_data='buy_hide_data')],
    ])

def check_flood(uid: int) -> bool:
    c.execute('SELECT last_queries FROM users WHERE id=?', (uid,))
    last = c.fetchone()[0] if c.fetchone() else ''
    now = int(time.time())
    times = [int(t) for t in last.split(',') if t] + [now]
    recent = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute('UPDATE users SET last_queries=? WHERE id=?', (','.join(map(str, recent)), uid))
    conn.commit()
    return len(recent) > FLOOD_LIMIT or (len(recent) >= 2 and recent[-1] - recent[-2] < FLOOD_INTERVAL)

# === Handlers ===

@dp.message(CommandStart())
async def start_handler(message: Message):
    uid = message.from_user.id
    # Ensure user exists
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)', (uid,0,0,0))
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?', (message.from_user.username, uid))
    conn.commit()

    hd, fu, te = c.execute('SELECT hidden_data,free_used,trial_expired FROM users WHERE id=?', (uid,)).fetchone()
    if hd:
        welcome = '<b>Your data is hidden.</b>'
    elif te:
        welcome = '<b>Your trial ended.</b>'
    else:
        rem = TRIAL_LIMIT - fu
        welcome = f'<b>You have {rem} free searches left.</b>' if rem > 0 else '<b>Your trial ended.</b>'

    await message.answer(f"👾 Welcome to n3l0x!\n{welcome}", reply_markup=sub_keyboard())

@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_',1)[1]
    if plan not in TARIFFS:
        return await callback.answer('Unknown plan', show_alert=True)
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
        return await callback.message.answer('⚠️ Payment service error.')
    if not data.get('ok'):
        return await callback.message.answer(f"⚠️ Error: {data}")
    url = data['result'].get('bot_invoice_url') or data['result'].get('pay_url')
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('💳 Pay now', url=url)]])
    await callback.message.answer(f"💳 {plan} – ${price}", reply_markup=kb)
    await callback.answer()

@dp.message(Command('admin322'))
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton('📊 Give Requests',  callback_data='give_requests')],
        [InlineKeyboardButton('🚫 Block User',     callback_data='block_user')],
        [InlineKeyboardButton('✅ Unblock User',   callback_data='unblock_user')],
        [InlineKeyboardButton('🔄 Reset Trial',    callback_data='reset_menu')],
    ])
    await message.answer('<b>Admin Panel:</b>', reply_markup=kb)

# ——— Give Requests ———
@dp.callback_query(F.data=='give_requests')
async def give_requests(call: CallbackQuery, state: FSMContext):
    await call.answer(); await call.message.answer('🆔 Enter user ID:')
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
    data = await state.get_data(); n = msg.text
    if not n.isdigit() or not (1<=int(n)<=10):
        return await msg.answer('Enter a number 1–10')
    c.execute(
        'INSERT INTO users(id,requests_left) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET requests_left=excluded.requests_left',
        (data['uid'], int(n))
    )
    conn.commit()
    await msg.answer(f"✅ Granted {n} to {data['uid']}")
    await state.clear()

# ——— Block/Unblock ———
@dp.callback_query(F.data=='block_user')
async def block_user(call: CallbackQuery, state: FSMContext):
    await call.answer(); await call.message.answer('👤 Enter @username to block:')
    await state.update_data(mode='block'); await state.set_state(AdminStates.wait_username)

@dp.callback_query(F.data=='unblock_user')
async def unblock_user(call: CallbackQuery, state: FSMContext):
    await call.answer(); await call.message.answer('👤 Enter @username to unblock:')
    await state.update_data(mode='unblock'); await state.set_state(AdminStates.wait_username)

@dp.message(AdminStates.wait_username)
async def change_block(msg: Message, state: FSMContext):
    data = await state.get_data(); uname = msg.text.strip().lstrip('@')
    if data['mode']=='block':
        c.execute('UPDATE users SET is_blocked=1 WHERE username=?',(uname,))
    else:
        c.execute('UPDATE users SET is_blocked=0 WHERE username=?',(uname,))
    conn.commit()
    await msg.answer(f"{data['mode'].capitalize()}ed @{uname}")
    await state.clear()

# ——— Reset Trial Menu ———
@dp.callback_query(F.data=='reset_menu')
async def reset_menu(call: CallbackQuery):
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton('🔁 Reset ALL trials', callback_data='reset_all')],
        [InlineKeyboardButton('🔍 Reset by ID',    callback_data='reset_by_id')],
    ])
    await call.message.answer('Choose reset mode:', reply_markup=kb)

@dp.callback_query(F.data=='reset_all')
async def reset_all(call: CallbackQuery):
    await call.answer()
    c.execute('UPDATE users SET free_used=0, trial_expired=0')
    conn.commit()
    await call.message.answer('🔄 All trials have been reset.')

@dp.callback_query(F.data=='reset_by_id')
async def reset_by_id(call: CallbackQuery, state: FSMContext):
    await call.answer(); await call.message.answer('🆔 Enter user ID for reset:')
    await state.set_state(AdminStates.wait_reset_id)

@dp.message(AdminStates.wait_reset_id)
async def do_reset_by_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer('ID must be numeric')
    uid = int(msg.text)
    c.execute('UPDATE users SET free_used=0, trial_expired=0 WHERE id=?',(uid,))
    conn.commit()
    await msg.answer(f'🔄 Trial reset for user {uid}.')
    await state.clear()

# ——— Search Handler ———
@dp.message(F.text & ~F.text.startswith('/'))
async def search_handler(message: Message):
    uid = message.from_user.id
    c.execute('INSERT OR IGNORE INTO users(id,subs_until,free_used,hidden_data) VALUES(?,?,?,?)',(uid,0,0,0))
    conn.commit()

    q = message.text.strip()
    if message.from_user.username:
        c.execute('UPDATE users SET username=? WHERE id=?',(message.from_user.username,uid))
        conn.commit()

    is_blocked, hidden_data, requests_left, free_used, subs_until, trial_expired = \
        c.execute('SELECT is_blocked,hidden_data,requests_left,free_used,subs_until,trial_expired FROM users WHERE id=?',(uid,)).fetchone()
    now = int(time.time())

    # access control
    if not is_admin(uid):
        if is_blocked:
            return await message.answer('🚫 Blocked.')
        if hidden_data:
            return await message.answer('🚫 Data hidden.')
        if subs_until<=now and trial_expired:
            return await message.answer('🔐 Trial over. Subscribe.', reply_markup=sub_keyboard())
        if q in ADMIN_HIDDEN:
            return await message.answer('🚫 Prohibited.')
        if check_flood(uid):
            return await message.answer('⛔ Flood. Try later.')

        if requests_left>0:
            c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?',(uid,))
        else:
            if subs_until<=now:
                if free_used<TRIAL_LIMIT:
                    c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?',(uid,))
                    if free_used+1>=TRIAL_LIMIT:
                        c.execute('UPDATE users SET trial_expired=1 WHERE id=?',(uid,))
                else:
                    c.execute('UPDATE users SET trial_expired=1 WHERE id=?',(uid,))
                    conn.commit()
                    return await message.answer('🔐 Trial over. Subscribe.', reply_markup=sub_keyboard())
        conn.commit()

    if c.execute('SELECT 1 FROM blacklist WHERE value=?',(q,)).fetchone():
        return await message.answer('🔒 Denied.')

    await message.answer(f"🕷️ Recon on <code>{q}</code>…")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.usersbox.ru/v1/search",
                headers={'Authorization': USERSBOX_API_KEY},
                params={'q': q}, timeout=10
            ) as r:
                if r.status!=200:
                    return await message.answer(f'⚠️ API {r.status}')
                data = await r.json()
    except:
        return await message.answer('⚠️ Network error.')

    if data.get('status')!='success' or data.get('data',{}).get('count',0)==0:
        return await message.answer('📡 No match.')

    def beautify(k):
        m = {
            'full_name':'Имя','phone':'Телефон','inn':'ИНН',
            'email':'Email','first_name':'Имя','last_name':'Фамилия',
            'middle_name':'Отчество','birth_date':'Дата рождения',
            'gender':'Пол','passport_series':'Паспорт серия',
            'passport_number':'Паспорт №','passport_date':'Выдан'
        }
        return m.get(k,k)

    # Build hacker-style HTML
    blocks = []
    for itm in data['data']['items']:
        hits = itm.get('hits',{}).get('items',[])
        src  = itm.get('source',{}).get('database','?')
        if not hits: continue
        rows = "".join(
            f"<tr><td>{beautify(k)}</td><td>{', '.join(v) if isinstance(v,(list,tuple)) else v}</td></tr>"
            for h in hits for k,v in h.items() if v
        )
        blocks.append(f"""
    <div class="block">
      <h2>{src.upper()}</h2>
      <table>
        <thead><tr><th>Field</th><th>Value</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>""")

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>n3l0x Report</title>
<style>
body{{margin:0;padding:0;background:#000;color:#0f0;font-family:Courier,monospace}}
h1{{text-align:center;padding:10px 0;border-bottom:1px solid #0f0}}
.report{{display:flex;flex-wrap:wrap;justify-content:center}}
.block{{background:#111;border:1px solid #0f0;border-radius:4px;margin:8px;padding:8px;width:300px;overflow:auto}}
.block h2{{margin:0 0 4px;color:#0f0;font-size:1.1em;text-align:center}}
table{{width:100%;border-collapse:collapse}}
th,td{{border:1px solid #0f0;padding:4px;text-align:left;font-size:0.9em}}
th{{background:#020}}
@media(max-width:640px){{.block{{width:90%}}}}
</style>
</head><body>
<h1>n3l0x Intelligence</h1>
<div class="report">{''.join(blocks)}</div>
</body></html>"""

    # send HTML file
    with tempfile.NamedTemporaryFile('w',delete=False,suffix='.html',dir='/tmp',encoding='utf-8') as tf:
        tf.write(html); path=tf.name

    await message.answer_document(FSInputFile(path,filename=f"{q}.html"))
    os.unlink(path)

# ——— /status & /help ———
@dp.message(Command('status'))
async def status(m:Message):
    uid=m.from_user.id
    su,fu,hd,rl,te = c.execute('SELECT subs_until,free_used,hidden_data,requests_left,trial_expired FROM users WHERE id=?',(uid,)).fetchone()
    now=int(time.time())
    if hd: return await m.answer('🔒 Hidden')
    sub = datetime.fromtimestamp(su).strftime('%Y-%m-%d') if su>now else 'none'
    free = 0 if te else TRIAL_LIMIT-fu
    await m.answer(f"📊 Sub: {sub}\nFree:{free}\nManual:{rl}")

@dp.message(Command('help'))
async def help(m:Message):
    await m.answer("/status – status\n/help – this msg\nSend text to search")

# === Webhooks ===
async def health(r): return web.Response(text='OK')
async def cryptopay_webhook(r: web.Request):
    try: js=await r.json()
    except: return web.json_response({'ok':True})
    inv=js.get('invoice') or js.get('payload') or {}
    if inv.get('status')=='paid' and inv.get('payload'):
        parts=inv['payload'].split('_')
        if parts[0]=='pay' and len(parts)>=4:
            uid,plan=int(parts[1]),parts[2]
            now=int(time.time())
            if plan=='hide_data':
                c.execute('UPDATE users SET hidden_data=1 WHERE id=?',(uid,))
            else:
                old=c.execute('SELECT subs_until FROM users WHERE id=?',(uid,)).fetchone()[0]
                ns=max(now,old)+TARIFFS[plan]['days']*86400
                c.execute('INSERT INTO users(id,subs_until,free_used) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET subs_until=excluded.subs_until,free_used=0,trial_expired=1',(uid,ns,0))
            c.execute('INSERT OR REPLACE INTO payments(payload,user_id,plan,paid_at) VALUES(?,?,?,?)',(inv['payload'],uid,plan,now))
            conn.commit()
            try: await bot.send_message(uid,f"✅ Paid '{plan}'")
            except: pass
    return web.json_response({'ok':True})

app=web.Application()
app.router.add_get('/health',health)
app.router.add_route('*','/webhook',SimpleRequestHandler(dp,bot,WEBHOOK_SECRET))
app.router.add_post('/cryptopay',cryptopay_webhook)
app.on_startup.append(lambda app: asyncio.create_task(bot.set_webhook(WEBHOOK_URL,secret_token=WEBHOOK_SECRET)) if WEBHOOK_URL else None)
app.on_shutdown.append(lambda app: asyncio.create_task(bot.delete_webhook()) or conn.close())

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(app,host='0.0.0.0',port=PORT)