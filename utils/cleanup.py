# utils/cleanup.py
import asyncio
import logging
from datetime import datetime, timedelta
from database import temp_file_store

# Функция очистки временного хранилища
async def cleanup_temp_store():
    while True:
        now = datetime.now()
        expired = [
            key for key, data in temp_file_store.items()
            if (now - data["timestamp"]).total_seconds() > 86400  # 24 часа
        ]
        for key in expired:
            del temp_file_store[key]
        await asyncio.sleep(3600)  # Проверяем каждые 1 час