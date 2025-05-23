# utils/cleanup.py
import asyncio
import logging
from datetime import datetime, timedelta
from database import temp_file_store

# Функция очистки временного хранилища
async def cleanup_temp_store():
    while True:
        # Очищаем записи старше 5 минут
        now = datetime.now()
        expired = [
            key for key, (timestamp, _) in temp_file_store.items()
            if (now - timestamp) > timedelta(minutes=5)
        ]
        for key in expired:
            del temp_file_store[key]
            logging.info(f"Очищен устаревший short_id: {key}")
        await asyncio.sleep(3600)  # Проверяем каждые 1 час