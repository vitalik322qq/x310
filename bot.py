import os
import logging
import aiohttp
import sqlite3  # using sqlite3 instead of psycopg2 for simplicity
import time
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web, ClientError

# === Settings from ENV ===
BOT_TOKEN = os.getenv('BOT_TOKEN')
USERSBOX_API_KEY = os.getenv('USERSBOX_API_KEY')
CRYPTOPAY_TOKEN = os.getenv('CRYPTOPAY_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
BASE_CURRENCY = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')

# === Constants ===
TARIFFS = {
    'month':    {'price': 49,  'days': 29},
    'quarter':  {'price': 120, 'days': 89},
    'lifetime': {'price': 299, 'days': 9999},
    'hide_data':{'price': 100, 'days': 0}
}
TRIAL_LIMIT   = int(os.getenv('TRIAL_LIMIT', '3'))
FLOOD_WINDOW  = int(os.getenv('FLOOD_WINDOW', '15'))
FLOOD_LIMIT   = int(os.getenv('FLOOD_LIMIT', '10'))
FLOOD_INTERVAL= int(os.getenv('FLOOD_INTERVAL', '3'))

# === Database Connection ===
conn = sqlite3.connect('n3lox_users.db', check_same_thread=False)
c = conn.cursor()
# Create tables if not exist
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    subs_until BIGINT,
    free_used INT,
    trial_expired BOOLEAN DEFAULT FALSE,
    last_queries TEXT DEFAULT '',
    hidden_data BOOLEAN DEFAULT FALSE,
    username TEXT DEFAULT '',
    requests_left INT DEFAULT 0,
    is_blocked BOOLEAN DEFAULT FALSE
)
"""
)
c.execute("""
CREATE TABLE IF NOT EXISTS payments (
    payload TEXT PRIMARY KEY,
    user_id BIGINT,
    plan TEXT,
    paid_at BIGINT
)
"""
)
c.execute("""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
)
"""
)
conn.commit()

# Admin hidden queries
ADMIN_HIDDEN = [
    'Кохан Богдан Олегович', '10.07.1999', '10.07.99',
    'Кохан Богдан Олегович 10.07.1999', 'Кохан Богдан Олегович 10.07.99',
    '380636659255', '0636659255', '+380636659255',
    '+380683220001', '0683220001', '380683220001'
]

# === Bot Init ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# === Helpers ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton('🔒 29 days - $49',  callback_data='buy_month')],
        [InlineKeyboardButton('🔒 89 days - $120', callback_data='buy_quarter')],
        [InlineKeyboardButton('🔒 Lifetime - $299', callback_data='buy_lifetime')],
        [InlineKeyboardButton('🧊 Hide my data - $100', callback_data='buy_hide_data')]
    ])

def is_subscribed(uid: int) -> bool:
    c.execute('SELECT subs_until FROM users WHERE id=?', (uid,))
    row = c.fetchone()
    return bool(row and row[0] > int(time.time()))

def check_flood(uid: int) -> bool:
    c.execute('SELECT last_queries FROM users WHERE id=?', (uid,))
    row = c.fetchone()
    now = int(time.time())
    times = [int(t) for t in (row[0] or '').split(',') if t] + [now]
    recent = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute('UPDATE users SET last_queries=? WHERE id=?', (','.join(map(str,recent)), uid))
    conn.commit()
    return len(recent) > FLOOD_LIMIT or (len(recent)>=2 and recent[-1]-recent[-2]<FLOOD_INTERVAL)

# FSM States for Admin
class AdminStates(StatesGroup):
    wait_user_id      = State()
    wait_request_amount = State()
    wait_username     = State()

# === Handlers ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    # Parse deep-link argument from /start command
    text = message.text or ''
    parts = text.split(' ', 1)
    arg = parts[1] if len(parts) > 1 else ''
    # Deep-link payment handling
    if arg.startswith('merchant_'):
        parts = arg.split('_')
        if len(parts) >= 6:
            _, token, price, currency, uid_str, plan = parts[:6]
            payload = arg
            if token == CRYPTOPAY_TOKEN and plan in TARIFFS:
                c.execute('SELECT 1 FROM payments WHERE payload=?', (payload,))
                if not c.fetchone():
                    if plan == 'hide_data':
                        c.execute('UPDATE users SET hidden_data=TRUE WHERE id=?', (int(uid_str),))
                    else:
                        days = TARIFFS[plan]['days']
                        subs = int(time.time()) + days * 86400
                        c.execute(
                            'INSERT INTO users (id,subs_until,free_used) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET subs_until=?',
                            (int(uid_str), subs, 0, subs)
                        )
                    c.execute('INSERT INTO payments (payload,user_id,plan,paid_at) VALUES (?,?,?,?)',
                              (payload, int(uid_str), plan, int(time.time())))
                    conn.commit()
                    await message.answer(f"✅ Plan '{plan}' activated.")
                    return
    # Regular /start
    uid = message.from_user.id
    c.execute('INSERT INTO users (id,subs_until,free_used,hidden_data) VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING',
              (uid, 0, 0, False))
    conn.commit()
    if is_admin(uid):
        text = '<b>As admin, unlimited access.</b>'
    elif is_subscribed(uid):
        text = '<b>Your subscription is active.</b>'
    else:
        c.execute('SELECT hidden_data, free_used FROM users WHERE id=?', (uid,))
        hd, fu = c.fetchone()
        if hd:
            text = '<b>Your data is hidden.</b>'
        else:
            rem = TRIAL_LIMIT - fu
            text = f'<b>You have {rem} free searches left.</b>' if rem > 0 else '<b>Your trial ended.</b>'
    await message.answer(
    f"👾 Welcome to n3лoх!
{text}",
    reply_markup=sub_keyboard()
))f"✅ Plan '{plan}' activated.")
                    return
    # Regular /start
    uid = message.from_user.id
    c.execute('INSERT INTO users (id,subs_until,free_used,hidden_data) VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING',
              (uid,0,0,False))
    conn.commit()
    if is_admin(uid):
        text='<b>As admin, unlimited access.</b>'
    elif is_subscribed(uid):
        text='<b>Your subscription is active.</b>'
    else:
        c.execute('SELECT hidden_data, free_used FROM users WHERE id=?',(uid,))
        hd,fu = c.fetchone()
        if hd:
            text='<b>Your data is hidden.</b>'
        else:
            rem = TRIAL_LIMIT-fu
            text = f'<b>You have {rem} free searches left.</b>' if rem>0 else '<b>Your trial ended.</b>'
    await message.answer(f"👾 Welcome to n3лoх!\n{text}" , reply_markup=sub_keyboard())

@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_',1)[1]
    if plan in TARIFFS:
        price = TARIFFS[plan]['price']
        payload = f"merchant_{CRYPTOPAY_TOKEN}_{price}_{BASE_CURRENCY}_{callback.from_user.id}_{plan}_{int(time.time())}"
        link = f"https://t.me/CryptoBot?start={payload}"
        await callback.message.answer(f"💳 Pay for {plan} (${price}):\n{link}", disable_web_page_preview=True)
        await callback.answer()

# Admin Panel
@dp.message(F.text=='/admin322')
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id): return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📊 Give Requests', callback_data='give_requests')],
        [InlineKeyboardButton('🚫 Block User',    callback_data='block_user')],
        [InlineKeyboardButton('✅ Unblock User',  callback_data='unblock_user')]
    ])
    await message.answer('<b>Admin Panel:</b>', reply_markup=kb)

@dp.callback_query(F.data=='give_requests')
async def give_requests(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return await call.answer('Denied',show_alert=True)
    await call.message.answer('🆔 Enter user ID:')
    await state.set_state(AdminStates.wait_user_id)
    await call.answer()

@dp.message(AdminStates.wait_user_id)
async def set_user_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer('Must be number')
    await state.update_data(uid=int(msg.text))
    await msg.answer('🔢 Enter requests (1-10):')
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def set_requests(msg: Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer('Must be number')
    data=await state.get_data(); userid=data['uid']; cnt=int(msg.text)
    if cnt<1 or cnt>10: return await msg.answer('1-10 only')
    c.execute('INSERT INTO users(id,requests_left) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET requests_left=?',
              (userid,cnt,cnt))
    conn.commit(); await msg.answer(f'Granted {cnt} to {userid}'); await state.clear()

@dp.callback_query(F.data=='block_user')
async def block_user(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer('Denied',show_alert=True)
    await call.message.answer('👤 Enter username to block:')
    await AdminStates.wait_username.set()
    await call.answer()

@dp.callback_query(F.data=='unblock_user')
async def unblock_user(call: CallbackQuery):
    if not is_admin(call.from_user.id): return await call.answer('Denied',show_alert=True)
    await call.message.answer('👤 Enter username to unblock:')
    await AdminStates.wait_username.set()
    await call.answer()

@dp.message(AdminStates.wait_username)
async def change_block(msg: Message, state: FSMContext):
    uname=msg.text.strip().lstrip('@')
    data=await state.get_data()
    # last callback tells mode
    mode = msg.reply_to_message and msg.reply_to_message.text.lower().startswith('👤 enter username to unblock')
    if mode:
        c.execute('UPDATE users SET is_blocked=FALSE WHERE username=?',(uname,))
        await msg.answer(f'✅ Unblocked @{uname}')
    else:
        c.execute('UPDATE users SET is_blocked=TRUE WHERE username=?',(uname,))
        await msg.answer(f'🚫 Blocked @{uname}')
    conn.commit(); await state.clear()

@dp.message()
async def search_handler(message: Message):
    uid=message.from_user.id; query=message.text.strip()
    # blocked?
    c.execute('SELECT is_blocked FROM users WHERE id=?',(uid,))
    if c.fetchone()[0]: return await message.answer('🚫 You are blocked.')
    # hidden data?
    c.execute('SELECT hidden_data FROM users WHERE id=?',(uid,))
    if c.fetchone()[0]: return await message.answer('🚫 This user data is hidden.')
    # admin hidden
    if query in ADMIN_HIDDEN: return await message.answer('Этого кента нельзя пробивать.')
    # manual requests
    c.execute('SELECT requests_left FROM users WHERE id=?',(uid,))
    rl=c.fetchone()[0]
    if rl>0:
        c.execute('UPDATE users SET requests_left=requests_left-1 WHERE id=?',(uid,)); conn.commit()
    else:
        # trial/sub
        if not is_admin(uid) and not is_subscribed(uid):
            c.execute('SELECT free_used FROM users WHERE id=?',(uid,))
            fu=c.fetchone()[0]
            if fu>=TRIAL_LIMIT:
                c.execute('UPDATE users SET trial_expired=TRUE WHERE id=?',(uid,)); conn.commit()
                return await message.answer('🔐 Trial over.',reply_markup=sub_keyboard())
    # flood
    if not is_admin(uid) and check_flood(uid): return await message.answer('⛔ Flood')
    # blacklist
    c.execute('SELECT 1 FROM blacklist WHERE value=?',(query,))
    if c.fetchone(): return await message.answer('🔒 Access denied')
    # call API
    await message.answer(f"🕷️ Connecting to nodes...\n🧬 Running recon on <code>{query}</code>")
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"https://api.usersbox.ru/v1/search?q={query}", headers={'Authorization':USERSBOX_API_KEY},timeout=10) as resp:
                if resp.status!=200: return await message.answer(f"⚠️ API {resp.status}")
                result=await resp.json()
    except (ClientError, asyncio.TimeoutError) as e:
        logging.error(f"API failed: {e}")
        return await message.answer('⚠️ Network error')
    if result.get('status')!='success' or result.get('data',{}).get('count',0)==0:
        return await message.answer('📡 No match')
    # build HTML
    def beautify_key(k): return {'full_name':'Имя','phone':'Телефон','inn':'ИНН'}.get(k,k)
    blocks=[]
    for it in result['data']['items']:
        hdr=f"☠ {it.get('source',{}).get('database','?')}"; lines=[]
        for hit in it.get('hits',{}).get('items',[]):
            for k,v in hit.items():
                if not v: continue
                val=", ".join(str(x) for x in (v if isinstance(v,list) else [v]))
                lines.append(f"<div class='row'><span class='key'>{beautify_key(k)}:</span><span class='val'>{val}</span></div>")
        if lines: blocks.append(f"<div class='block'><div class='header'>{hdr}</div>{''.join(lines)}</div>")
    html="""
<html><head><meta charset='utf-8'><style>body{{}}/*...*/</style></head><body>"""+''.join(blocks)+"</body></html>"
    fname=f"{query}.html"
    with open(fname,'w',encoding='utf-8') as f: f.write(html)
    await message.answer('Нашел кента. Держи файл:')
    await message.answer_document(FSInputFile(fname))
    os.remove(fname)
    if not is_admin(uid) and not is_subscribed(uid):
        c.execute('UPDATE users SET free_used=free_used+1 WHERE id=?',(uid,)); conn.commit()

# Commands
@dp.message(F.text=='/status')
async def status_handler(m:Message):
    uid=m.from_user.id; c.execute('SELECT subs_until,free_used,hidden_data,requests_left FROM users WHERE id=?',(uid,))
    su,fu,hd,rl=c.fetchone(); now=int(time.time())
    if hd: return await m.answer('🔒 Your data is hidden')
    sub=('active until '+datetime.fromtimestamp(su).strftime('%Y-%m-%d %H:%M')) if su>now else 'not active'
    rem=TRIAL_LIMIT-fu
    await m.answer(f"📊Status:\nSubscription: {sub}\nFree left: {rem}\nManual left: {rl}")

@dp.message(F.text=='/help')
async def help_handler(m:Message):
    await m.answer('/status - status\n/help - help\nSend text to search')

async def health(request): return web.Response(text='OK')

# Webhook
async def on_startup(app): await bot.set_webhook(WEBHOOK_URL,secret_token=WEBHOOK_SECRET)
async def on_shutdown(app): await bot.delete_webhook(); conn.close()
app=web.Application()
app.router.add_get('/health',health)
app.router.add_route('*','/webhook',SimpleRequestHandler(dp,bot,secret_token=WEBHOOK_SECRET))
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host='0.0.0.0', port=int(os.getenv('PORT',8080)))
