import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.filters import CommandStart

BOT_TOKEN = "PLACE_YOUR_BOT_TOKEN_HERE"

dp = Dispatcher()
@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("✅ Bot is running!")

async def main():
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
