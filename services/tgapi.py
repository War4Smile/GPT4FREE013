# services/tgapi.py
import aiohttp
import logging
import config
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from providers.fully_working import AVAILABLE_PROVIDERS
from middlewares.user_middleware import UserMiddleware


#####################################################
########### Проверка доступности Telegram ###########

# Функция проверки доступности Telegram API
async def check_telegram_api_availability():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.telegram.org", timeout=10) as response:
                return response.status == 200
    except Exception as e:
        logging.error(f"Ошибка проверки Telegram API: {e}")
        return False

# Инициализация бота
session = AiohttpSession()
bot = Bot(
    token=config.BOT_TOKEN,
    session=session,
    timeout=40
)
