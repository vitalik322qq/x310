
from aiohttp import web
import sqlite3
import json
from datetime import datetime, timedelta

DB_PATH = "n3l0x_users.db"

async def handle_payment(request):
    try:
        data = await request.json()
        invoice_id = data.get("invoice_id")
        status = data.get("status")
        user_info = data.get("user", {})
        user_id = user_info.get("id")

        if status == "paid" and user_id:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            now = int(datetime.now().timestamp())
            duration = 29 * 86400  # default month
            expires = now + duration
            cursor.execute("REPLACE INTO users (user_id, is_subscribed, subscription_expires_at) VALUES (?, ?, ?)", 
                           (user_id, 1, expires))
            conn.commit()
            conn.close()
            return web.Response(text="Subscription activated")

        return web.Response(text="Ignored")
    except Exception as e:
        return web.Response(status=500, text=str(e))

def setup_webhook_routes(app):
    app.router.add_post('/payment_webhook', handle_payment)
