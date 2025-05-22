# middlewares/user_middleware.py
import config
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Awaitable, Dict, Any
from database import user_history, user_settings
from utils.helpers import save_user_info, update_user_activity

# Мидлварь для обработки пользователей

class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if user:
            user_id = user.id
            if user_id not in user_history:
                user_history[user_id] = []
            if user_id not in user_settings:
                user_settings[user_id] = {
                    "model": "flux",
                    "width": 1080,
                    "height": 1920,
                    "provider": config.DEFAULT_PROVIDER  # Добавлен провайдер
                }
            save_user_info(user)
            await update_user_activity(user)
        return await handler(event, data)