# services/retry.py
import aiohttp
import asyncio
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from services.tgapi import check_telegram_api_availability
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def transcribe_with_retry(payload):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://text.pollinations.ai/openai", 
            json=payload, 
            timeout=300
        ) as response:
            if response.status == 200:
                return await response.json()
            error_text = await response.text()
            logging.error(f"Pollinations API ошибка: {response.status} - {error_text}")
            raise Exception(f"Ошибка API: {error_text}")

# Функция загрузки изображения с повторными попытками
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def download_image_with_retry(image_url):
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, timeout=300) as response:
            if response.status == 200:
                return await response.read()
            error_text = await response.text()
            logging.error(f"Pollinations API ошибка: {response.status} - {error_text}")
            raise Exception(f"Ошибка генерации: {error_text}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def generate_audio_with_retry(payload, method="POST"):
    """Генерация аудио с повторными попытками"""
    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(payload["url"], timeout=300) as response:
                if response.status == 200:
                    return await response.read()
                error_text = await response.text()
                raise Exception(f"Ошибка API: {error_text}")
        else:
            async with session.post("https://text.pollinations.ai/openai", json=payload, timeout=300) as response:
                if response.status == 200:
                    return await response.json()
                error_text = await response.text()
                raise Exception(f"Ошибка API: {error_text}")


######################################################
########### Функция повторного подключения ###########

async def wait_for_telegram_api():
    while True:
        if await check_telegram_api_availability():
            logging.info("Telegram API доступен")
            return
        logging.error("Telegram API недоступен. Повтор через 10 сек...")
        await asyncio.sleep(10)

async def auto_save_task():
    while True:
        await asyncio.sleep(300)  # Сохраняем каждые 5 минут
        save_users()
        logging.info("Данные пользователей автоматически сохранены")
