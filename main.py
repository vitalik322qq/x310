
import asyncio
from aiohttp import web
from webhook_handler import setup_webhook_routes
from bot import run_bot

async def start():
    app = web.Application()
    setup_webhook_routes(app)

    bot_task = asyncio.create_task(run_bot())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Webhook server started on http://0.0.0.0:8080")

    await bot_task

if __name__ == "__main__":
    asyncio.run(start())
