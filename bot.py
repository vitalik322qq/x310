import os
import logging
import aiohttp
import psycopg2
import time
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
OWNER_ID = int(os.getenv('OWNER_ID'))
BASE_CURRENCY = os.getenv('BASE_CURRENCY', 'USDT')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
DATABASE_URL = os.getenv('DATABASE_URL')

# === Constants ===
TARIFFS = {
    "month": {"price": 49, "days": 29},
    "quarter": {"price": 120, "days": 89},
    "lifetime": {"price": 299, "days": 9999},
    "hide_data": {"price": 100, "days": 0}
}
TRIAL_LIMIT = int(os.getenv('TRIAL_LIMIT', '3'))
FLOOD_WINDOW = int(os.getenv('FLOOD_WINDOW', '15'))
FLOOD_LIMIT = int(os.getenv('FLOOD_LIMIT', '10'))
FLOOD_INTERVAL = int(os.getenv('FLOOD_INTERVAL', '3'))

# === Database Connection ===
conn = psycopg2.connect(DATABASE_URL)
c = conn.cursor()
# Create tables if not exists
c.execute(
"""
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
);
"""
)
c.execute(
"""
CREATE TABLE IF NOT EXISTS payments (
    payload TEXT PRIMARY KEY,
    user_id BIGINT,
    plan TEXT,
    paid_at BIGINT
);
"""
)
c.execute(
"""
CREATE TABLE IF NOT EXISTS blacklist (
    value TEXT PRIMARY KEY
);
"""
)
conn.commit()

# Admin hidden values to block
ADMIN_HIDDEN = [
    "Кохан Богдан Олегович", "10.07.1999", "10.07.99",
    "Кохан Богдан Олегович 10.07.1999", "Кохан Богдан Олегович 10.07.99",
    "380636659255", "0636659255", "+380636659255",
    "+380683220001", "0683220001", "380683220001"
]

# === Bot Init ===
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# === Helpers ===
def is_admin(uid: int) -> bool:
    return uid == OWNER_ID

def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 29 days - $49", callback_data="buy_month")],
        [InlineKeyboardButton(text="🔒 89 days - $120", callback_data="buy_quarter")],
        [InlineKeyboardButton(text="🔒 Lifetime - $299", callback_data="buy_lifetime")],
        [InlineKeyboardButton(text="🧊 Hide my data - $100", callback_data="buy_hide_data")]
    ])

def is_subscribed(uid: int) -> bool:
    c.execute("SELECT subs_until FROM users WHERE id=%s", (uid,))
    row = c.fetchone()
    return bool(row and row[0] > int(time.time()))

def check_flood(uid: int) -> bool:
    c.execute("SELECT last_queries FROM users WHERE id=%s", (uid,))
    row = c.fetchone()
    now = int(time.time())
    times = [int(t) for t in (row[0] or '').split(',') if t] + [now]
    recent = [t for t in times if now - t <= FLOOD_WINDOW]
    c.execute("UPDATE users SET last_queries=%s WHERE id=%s", (','.join(map(str, recent)), uid))
    conn.commit()
    return len(recent) > FLOOD_LIMIT or (len(recent) >= 2 and recent[-1] - recent[-2] < FLOOD_INTERVAL)

# === States ===
class AdminStates(StatesGroup):
    wait_user_id = State()
    wait_request_amount = State()
    wait_username = State()

# === Handlers ===
@dp.message(CommandStart())
async def start_handler(message: Message):
    args = message.get_full_command()[1] or ''
    # Payment deep-link
    if args.startswith('merchant_'):
        parts = args.split('_')
        if len(parts) >= 6:
            _, token, price, currency, uid_str, plan = parts[:6]
            payload = args
            if token == CRYPTOPAY_TOKEN and plan in TARIFFS:
                c.execute("SELECT 1 FROM payments WHERE payload=%s", (payload,))
                if not c.fetchone():
                    if plan == 'hide_data':
                        c.execute("UPDATE users SET hidden_data=TRUE WHERE id=%s", (int(uid_str),))
                    else:
                        days = TARIFFS[plan]['days']
                        subs_until = int(time.time()) + days * 86400
                        c.execute(
                            "INSERT INTO users (id, subs_until, free_used) VALUES (%s,%s,%s) "
                            "ON CONFLICT (id) DO UPDATE SET subs_until=%s",
                            (int(uid_str), subs_until, 0, subs_until)
                        )
                    c.execute(
                        "INSERT INTO payments (payload,user_id,plan,paid_at) VALUES (%s,%s,%s,%s)",
                        (payload, int(uid_str), plan, int(time.time()))
                    )
                    conn.commit()
                    await message.answer(f"✅ Plan '{plan}' activated.")
                    return
    # Regular /start
    uid = message.from_user.id
    c.execute(
        "INSERT INTO users (id, subs_until, free_used, hidden_data) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (uid, 0, 0, False)
    )
    conn.commit()
    if is_admin(uid):
        msg = "<b>As admin, unlimited access.</b>"
    else:
        if is_subscribed(uid):
            msg = "<b>Your subscription is active.</b>"
        else:
            if c.execute("SELECT hidden_data FROM users WHERE id=%s", (uid,)).fetchone()[0]:
                msg = "<b>Your data is hidden.</b>"
            else:
                free_used = c.execute("SELECT free_used FROM users WHERE id=%s", (uid,)).fetchone()[0]
                remaining = TRIAL_LIMIT - free_used
                msg = (f"<b>You have {remaining} free searches remaining.</b>" 
                       if remaining > 0 else "<b>Your trial has ended.</b>")
    await message.answer("👾 Welcome to n3лoх!\n" + msg, reply_markup=sub_keyboard())

@dp.callback_query(F.data.startswith('buy_'))
async def buy_plan(callback: CallbackQuery):
    plan = callback.data.split('_', 1)[1]
    if plan in TARIFFS:
        price = TARIFFS[plan]['price']
        payload = f"merchant_{CRYPTOPAY_TOKEN}_{price}_{BASE_CURRENCY}_{callback.from_user.id}_{plan}_{int(time.time())}"
        link = f"https://t.me/CryptoBot?start={payload}"
        await callback.message.answer(f"💳 Pay for {plan} (${price}):\n{link}", disable_web_page_preview=True)
        await callback.answer()

@dp.message(F.text == "/admin322")
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id): return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Give Requests", callback_data="give_requests")],
        [InlineKeyboardButton(text="🚫 Block User", callback_data="block_user")],
        [InlineKeyboardButton(text="✅ Unblock User", callback_data="unblock_user")]
    ])
    await message.answer("<b>Admin Panel:</b>", reply_markup=keyboard)

@dp.callback_query(F.data == "give_requests")
async def handle_give_requests(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return await callback.answer("Access denied.", show_alert=True)
    await callback.message.answer("🆔 Enter Telegram user ID:")
    await state.set_state(AdminStates.wait_user_id)
    await callback.answer()

@dp.message(AdminStates.wait_user_id)
async def get_user_id(message: Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("❌ ID must be numeric.")
    await state.update_data(user_id=int(message.text))
    await message.answer("🔢 Enter number of requests (1-10):")
    await state.set_state(AdminStates.wait_request_amount)

@dp.message(AdminStates.wait_request_amount)
async def set_requests(message: Message, state: FSMContext):
    if not message.text.isdigit() or not 1 <= int(message.text) <= 10:
        return await message.answer("❌ Must be between 1 and 10.")
    data = await state.get_data()
    user_id, count = data['user_id'], int(message.text)
    c.execute(
        "INSERT INTO users (id, requests_left) VALUES (%s,%s) "
        "ON CONFLICT (id) DO UPDATE SET requests_left=%s",
        (user_id, count, count)
    )
    conn.commit()
    await message.answer(f"✅ Granted {count} requests to {user_id}.")
    await state.clear()

@dp.callback_query(F.data == "block_user")
async def block_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return await callback.answer("Access denied.", show_alert=True)
    await callback.message.answer("👤 Enter username to block (without @):")
    await state.set_state(AdminStates.wait_username)
    await callback.answer()

@dp.message(AdminStates.wait_username)
async def block_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    c.execute("UPDATE users SET is_blocked=TRUE WHERE username=%s", (username,))
    conn.commit()
    await message.answer(f"🚫 User @{username} blocked.")
    await state.clear()

@dp.message()
async def search_handler(message: Message):
    uid = message.from_user.id
    query = message.text.strip()
    # hidden_data check
    c.execute("SELECT hidden_data FROM users WHERE id=%s", (uid,))
    if c.fetchone()[0]:
        return await message.answer("🚫 This user data is hidden.")
    # admin hidden list
    if query in ADMIN_HIDDEN:
        return await message.answer("Ты че собака, этого кента нельзя пробивать.")
    # check manual requests_left
    c.execute("SELECT requests_left FROM users WHERE id=%s", (uid,))
    req_row = c.fetchone()
    if req_row and req_row[0] > 0:
        c.execute("UPDATE users SET requests_left = requests_left - 1 WHERE id=%s", (uid,))
        conn.commit()
    else:
        # subscription or trial check
        if not is_admin(uid) and not is_subscribed(uid):
            free_used = c.execute("SELECT free_used FROM users WHERE id=%s", (uid,)).fetchone()[0]
            if free_used >= TRIAL_LIMIT:
                c.execute("UPDATE users SET trial_expired=TRUE WHERE id=%s", (uid,))
                conn.commit()
                return await message.answer("🔐 Your trial is over. Please subscribe.", reply_markup=sub_keyboard())
    # anti-flood
    if not is_admin(uid) and check_flood(uid):
        return await message.answer("⛔ Flood detected. Try again later.")
    # blacklist
    c.execute("SELECT 1 FROM blacklist WHERE value=%s", (query,))
    if c.fetchone():
        return await message.answer("🔒 Access denied. Data is encrypted.")
    # Proceed to API call and HTML generation
    await message.answer(f"🕷️ Connecting to nodes...
🧬 Running recon on <code>{query}</code>")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.usersbox.ru/v1/search?q={query}",
                headers={"Authorization": USERSBOX_API_KEY}, timeout=10
            ) as response:
                if response.status != 200:
                    return await message.answer(f"⚠️ API Error: {response.status}")
                result = await response.json()
    except (ClientError, asyncio.TimeoutError) as e:
        logging.error(f"API request failed: {e}")
        return await message.answer("⚠️ Network error. Please try again later.")
    if result.get("status") != "success" or result.get("data", {}).get("count", 0) == 0:
        return await message.answer("📡 No match found in any database.")
    # (HTML gen continues ...)(message: Message):
    uid = message.from_user.id
    query = message.text.strip()
    # hidden data check
    c.execute("SELECT hidden_data FROM users WHERE id=%s", (uid,))
    if c.fetchone()[0]:
        return await message.answer("🚫 This user data is hidden.")
    if query in ADMIN_HIDDEN:
        return await message.answer("Ты че собака, этого кента нельзя пробивать.")
    if not is_admin(uid) and check_flood(uid):
        return await message.answer("⛔ Flood detected. Try again later.")
    c.execute("SELECT 1 FROM blacklist WHERE value=%s", (query,))
    if c.fetchone():
        return await message.answer("🔒 Access denied. Data is encrypted.")
    if not is_admin(uid) and not is_subscribed(uid):
        free_used = c.execute("SELECT free_used FROM users WHERE id=%s", (uid,)).fetchone()[0]
        if free_used >= TRIAL_LIMIT:
            c.execute("UPDATE users SET trial_expired=TRUE WHERE id=%s", (uid,))
            conn.commit()
            return await message.answer("🔐 Your trial is over. Please subscribe.", reply_markup=sub_keyboard())
    # Call API
    await message.answer(f"🕷️ Connecting to nodes...\n🧬 Running recon on <code>{query}</code>")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.usersbox.ru/v1/search?q={query}", headers={"Authorization": USERSBOX_API_KEY}, timeout=10) as response:
                if response.status != 200:
                    return await message.answer(f"⚠️ API Error: {response.status}")
                result = await response.json()
    except (ClientError, asyncio.TimeoutError) as e:
        logging.error(f"API request failed: {e}")
        return await message.answer("⚠️ Network error. Please try again later.")
    if result.get("status") != "success" or result.get("data", {}).get("count", 0) == 0:
        return await message.answer("📡 No match found in any database.")
    # Beautify and build HTML
    def beautify_key(key):
        key_map = {"full_name":"Имя","phone":"Телефон","inn":"ИНН","email":"Email","first_name":"Имя","last_name":"Фамилия","middle_name":"Отчество","birth_date":"Дата рождения","gender":"Пол","passport_series":"Серия паспорта","passport_number":"Номер паспорта","passport_date":"Дата выдачи паспорта"}
        return key_map.get(key, key)
    blocks = []
    for item in result["data"]["items"]:
        source = item.get("source", {})
        header = f"☠ {source.get('database','?')}"
        lines = []
        for hit in item.get("hits", {}).get("items", []):
            for k, v in hit.items():
                if not v: continue
                val = ", ".join(str(i) for i in (v if isinstance(v, list) else [v]))
                lines.append(f"<div class='row'><span class='key'>{beautify_key(k)}:</span><span class='val'>{val}</span></div>")
        if lines:
            blocks.append(f"<div class='block'><div class='header'>{header}</div>{''.join(lines)}</div>")
    html = f"""
<html><head><meta charset='UTF-8'><title>n3лoх Intelligence Report</title><style>
body{{background:#0a0a0a;color:#f0f0f0;font-family:'Courier New',monospace;padding:20px;display:flex;flex-direction:column;align-items:center;}}
.block{{background:#111;border:1px solid#333;border-radius:10px;padding:15px;margin-bottom:20px;width:100%;max-width:800px;box-shadow:0 0 10px #00ffcc55}}
.header{{font-size:18px;color:#00ffcc;margin-bottom:10px;font-weight:bold;}}
.row{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dotted #444;}}
.key{{color:#66ff66;font-weight:bold;min-width:40%;}}
.val{{color:#ff4de6;font-weight:bold;word-break:break-word;text-align:right;}}
@media(max-width:600px){{.row{{flex-direction:column;align-items:flex-start}}.val{{text-align:left;}}}}
</style></head><body><h1 style='color:#00ffcc;'>n3лoх Intelligence Report</h1>{''.join(blocks)}</body></html>
"""
    filename = f"{query}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html)
    await message.answer("Нашел кента. Держи файл:")
    await message.answer_document(FSInputFile(filename))
    os.remove(filename)
    if not is_admin(uid) and not is_subscribed(uid):
        c.execute("UPDATE users SET free_used=free_used+1 WHERE id=%s", (uid,))
        conn.commit()

# Additional commands
@dp.message(F.text == '/status')
async def status_handler(message: Message):
    uid = message.from_user.id
    c.execute("SELECT subs_until, free_used, hidden_data, requests_left FROM users WHERE id=%s", (uid,))
    subs_until, free_used, hidden_data, req_left = c.fetchone()
    now = int(time.time())
    if hidden_data:
        return await message.answer("🔒 Your data is hidden.")
    sub_status = ("active until " + datetime.fromtimestamp(subs_until).strftime("%Y-%m-%d %H:%M:%S")) if subs_until > now else "not active"
    remaining_free = TRIAL_LIMIT - free_used
    await message.answer(
        f"📊 Status:
Subscription: {sub_status}
Free searches left: {remaining_free}
Manual requests left: {req_left}"
    )

@dp.message(F.text == '/help')
async def help_handler(message: Message):
    help_text = (
        "/status - show your subscription and limits
"
        "/help - this help message
"
        "You can send any text to search; use buttons to subscribe."
    )
    await message.answer(help_text)

# Health check endpoint
async def health(request):
    return web.Response(text="OK")

# Webhook setup
async def on_startup(app: web.Application):
    await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    conn.close()

app = web.Application()
handler = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=WEBHOOK_SECRET)
app.router.add_route('*', '/webhook', handler)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    web.run_app(app, host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
