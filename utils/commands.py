# utils/commands.py
import logging
import config
from aiogram import F, Router, types
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from providers.fully_working_providers import AVAILABLE_PROVIDERS
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states)
from services.admin import is_admin
from utils.commandlist import user_commands, admin_commands, ADMIN_HELP_TXT, USER_HELP_TXT
from utils.helpers import translate_to_english, translate_to_russian
from services.tgapi import bot

router = Router()

##########################################
########### Обработчики команд ###########

# Модифицируем обработчик /start для сохранения информации
async def set_commands_for_user(user_id: int):
    """Устанавливает меню команд в зависимости от роли пользователя"""
    if is_admin(user_id):
        await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=user_id))
    else:
        await bot.set_my_commands(user_commands, scope=BotCommandScopeChat(chat_id=user_id))


# Модифицированный обработчик /start
@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    user_states[user_id] = None  # Сбрасываем состояние
    user_analysis_states[user_id] = None  # Сбрасываем состояние
    await set_commands_for_user(user_id)
    await message.answer("Привет! Я бот с функциями AI. Могу общаться и генерировать изображения, если нужна дополнительная информация используйте /help.")

# Обработчик команды /clear
@router.message(Command("clear"))
async def cmd_clear(message: Message):
    user_id = message.from_user.id
    if user_id in user_history:
        del user_history[user_id]
    user_settings[user_id] = {
        "model": "flux",
        "width": 1080,
        "height": 1920
    }
    await message.answer("✅ История диалога очищена и настройки сброшены на значения по умолчанию.")


# Модифицированный обработчик /help
@router.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    await set_commands_for_user(user_id)  # Обновляем команды при запросе помощи
    if is_admin(user_id):
        help_text = ADMIN_HELP_TXT
    else:
        help_text = USER_HELP_TXT
    await message.answer(help_text)

@router.message(Command("translate"))
async def cmd_translate(message: Message):
    if not message.reply_to_message or not message.reply_to_message.text:
        await message.answer("❌ Ответьте на текстовое сообщение командой `/translate`")
        return
    
    original_text = message.reply_to_message.text
    translated_text = await translate_to_english(original_text)
    
    await message.answer(
        f"🔄 Перевод сообщения:\n\n"
        f"Оригинал: {original_text}\n"
        f"Перевод: {translated_text}"
    )

# Обработчик команды /image
@router.message(Command("image"))
async def cmd_image(message: Message):
    await message.answer("🖼 Пожалуйста, введите описание изображения, которое вы хотите сгенерировать:")
    user_id = message.from_user.id
    user_states[user_id] = "waiting_for_image_description"  # Устанавливаем состояние ожидания
    # Сохраняем текущее состояние пользователя
    image_requests[user_id] = []  # Инициализируем историю запросов на изображение

# Обработчик /provider
@router.message(Command("provider"))
async def cmd_provider(message: Message):
    user_id = message.from_user.id
    current = user_settings.get(user_id, {}).get("provider", config.DEFAULT_PROVIDER)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🔄 {provider}{' ✅' if provider == current else ''}", 
                            callback_data=f"provider_{provider}")]
        for provider in AVAILABLE_PROVIDERS
    ])
    await message.answer("Выберите провайдера для текста:", reply_markup=keyboard)

# Обработчик команды /maketext
@router.message(Command("maketext"))
async def cmd_maketext(message: Message):
    # Сохраняем сообщение, чтобы удалить его позже
    sent_message = await message.answer("🎤 Пожалуйста, отправьте аудиофайл (форматы: aac, amr, flac, m4a, mp3, mp4, mpeg, ogg, wav) до 512Mb.")
    user_states[message.from_user.id] = "waiting_for_audio_file"  # Устанавливаем состояние ожидания

@router.message(Command("translatetoeng"))
async def cmd_translate(message: Message):
    if not message.reply_to_message or not message.reply_to_message.text:
        await message.answer("❌ Ответьте на текстовое сообщение командой `/translatetoeng`")
        return
    
    original_text = message.reply_to_message.text
    translated_text = await translate_to_english(original_text)
    
    await message.answer(
        f"🔄 Перевод сообщения:\n\n"
        f"{translated_text}"
    )

@router.message(Command("translatetoru"))
async def cmd_translate(message: Message):
    if not message.reply_to_message or not message.reply_to_message.text:
        await message.answer("❌ Ответьте на текстовое сообщение командой `/translatetoru`")
        return
    
    original_text = message.reply_to_message.text
    translated_text = await translate_to_russian(original_text)
    
    await message.answer(
        f"🔄 Перевод сообщения:\n\n"
        f"{translated_text}"
    )

# Обработчик выбора провайдера
@router.callback_query(lambda query: query.data.startswith("provider_"))
async def handle_provider_selection(query: CallbackQuery):
    user_id = query.from_user.id
    provider_name = query.data.split("_", 1)[1]
    
    # Обновляем провайдера в настройках
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["provider"] = provider_name
    save_users()  # Сохраняем изменения
    
    await query.message.edit_text(f"✅ Провайдер изменён на: {provider_name}")
    await query.answer()