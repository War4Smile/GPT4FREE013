# bot.py
import os
import asyncio
import logging
from PIL import Image
from io import BytesIO
from services.tgapi import bot
from aiogram import Bot, Dispatcher
from utils import cleanup, commands
from services import (admin, image_gen,audio_transcribeapi, retry,
                      audio_transcribe, imageanalysis, textmessages,
                      generateaudio, quiz)
from middlewares.user_middleware import UserMiddleware
dp = Dispatcher()

# Добавляем мидлварь
dp.update.middleware(UserMiddleware())

# Регистрируем роутеры
dp.include_router(commands.router)
dp.include_router(admin.router)
dp.include_router(audio_transcribe.router)
dp.include_router(audio_transcribeapi.router)
dp.include_router(generateaudio.router)
dp.include_router(imageanalysis.router)
dp.include_router(image_gen.router)
dp.include_router(quiz.router)
dp.include_router(textmessages.router)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создаем папку temp, если она не существует
if not os.path.exists('temp'):
    os.makedirs('temp')

# Функция для очистки папки temp
def clear_temp_folder():
    for filename in os.listdir('temp'):
        file_path = os.path.join('temp', filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Ошибка при удалении файла {file_path}: {str(e)}")
# Вызов функции очистки при запуске
clear_temp_folder()

# Запуск бота
async def main():
    # Запуск задачи очистки
    asyncio.create_task(cleanup.cleanup_temp_store())
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())