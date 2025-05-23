# utils/commandlist.py
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
##########################################################
# Списки команд для разных типов пользователей
user_commands = [    
    BotCommand(command="start", description="🔑 Запуск бота"),
    BotCommand(command="image", description="🖼 Генерация изображения"),
    BotCommand(command="clear", description="🧹 Очистка истории"),
    BotCommand(command="provider", description="🔄 Изменить модель GPT"),
    BotCommand(command="translatetoru", description="🔍 Перевести текст на Русский")
]
admin_commands = user_commands + [
    BotCommand(command="translatetoeng", description="🔍 Перевести текст на Английский"),
    BotCommand(command="generateaudio", description="🎙️ Сгенерировать аудио из текста"),
    BotCommand(command="transcribe", description="🎤 Распознать речь из аудиофайла"),
    BotCommand(command="imagesettings", description="⚙️ Настройки изображения"),
    BotCommand(command="analysissettings", description="🔎 Настройки анализа"),
    BotCommand(command="help", description="📝 Список команд"),
    BotCommand(command="aihelp", description="📝 Запрос информации о провайдере GPT"),
    BotCommand(command="adminusers", description="👥 Администрирование пользователей")
]

##########################################################
ADMIN_HELP_TXT = """
📝 Команды администратора:
/adminusers - 👥 Админка
============================
/start - 🔑 Перезапуск бота
/translatetoru - 🔍 Перевести
/translatetoeng - 🔍 Перевод
/image - 🖼 Генерация изображения
/helpai - 📝 Возможности модели
============================
/provider - 🔄 Модель GPT
/imagesettings - ⚙️ Настройки изображения
============================
/clear - 🧹 Очистка истории
"""
##########################################################
USER_HELP_TXT = """
📝 Доступные команды:
/start - 🔑 Перезапуск бота
============================
/translatetoru - 🔍 Перевести
/image - 🖼 Генерация изображения
/helpai - 📝 Возможности модели
============================
/provider - 🔄 Модель GPT
============================
/clear - 🧹 Очистка истории
"""
##########################################################