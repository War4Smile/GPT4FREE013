# services/textmessages.py
import logging
import asyncio
import g4f
import config
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from alworkproviders import AVAILABLE_PROVIDERS
from services.tgapi import bot
from services.admin import (is_admin)
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states)
from utils.helpers import (get_user_settings, convert_to_mp3, split_audio,
                            encode_audio_base64, remove_html_tags,
                            auto_detect_language, format_response)
from datetime import datetime

router = Router()


################################################
########### Запрос инфы о провайдере ###########
@router.message(Command("aihelp"))
async def cmd_aihelp(message: Message):
    user_id = message.from_user.id
    user_input = "Раскажи доступното ты такой и что ты умеешь!?"

    try:
        # Получаем провайдера из настроек пользователя
        provider_name = user_settings.get(user_id, {}).get("provider", config.DEFAULT_PROVIDER)
        provider_class = getattr(g4f.Provider, provider_name)

        # Формируем сообщение для AI
        api_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in user_history.get(user_id, [])
            if msg.get("type") == "text" and "role" in msg and "content" in msg
        ]
        api_messages.append({"role": "user", "content": user_input})

        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        response = await g4f.ChatCompletion.create_async(
            model=g4f.models.default,
            messages=api_messages,
            provider=provider_class(),
            api_key=config.API_DeepSeek
        )

        # Сохраняем в историю
        user_entry = {
            "type": "text",
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        }
        user_history.setdefault(user_id, []).append(user_entry)

        assistant_entry = {
            "type": "text",
            "role": "assistant",
            "content": remove_html_tags(response),
            "timestamp": datetime.now().isoformat()
        }
        user_history[user_id].append(assistant_entry)
        save_users()

        # Отправляем ответ
        formatted_response = format_response(response)
        await message.answer(formatted_response, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Ошибка AI: {str(e)}")
        current_provider = AVAILABLE_PROVIDERS[0]
        await message.answer(
            f"⚠️ Ошибка: {str(e)}\n"
            f"Провайдер автоматически сброшен на {current_provider}\n"
            "Попробуйте повторить запрос"
        )

################################################
########### Блок текстовых сообщений ###########
# Обработчик текстовых сообщений для ответов на сообщения администраторов
@router.message(lambda message: message.reply_to_message and is_admin(message.reply_to_message.from_user.id))
async def handle_admin_reply(message: Message):
    admin_id = message.reply_to_message.from_user.id
    user_id = message.from_user.id
    user_message = message.text

    # Отправляем сообщение администратору
    await bot.send_message(admin_id, f"👤 Ответ от пользователя {user_id}:\n\n{user_message}")

    # Уведомление пользователю о том, что его сообщение отправлено
    await message.answer("✅ Ваш ответ отправлен администратору.")

# Обработчик текстовых сообщений для общения с ИИ
@router.message(lambda message: message.text is not None and not (message.reply_to_message and is_admin(message.reply_to_message.from_user.id)))
async def handle_message(message: Message):
    global current_provider
    # Проверяем, существует ли from_user
    if message.from_user is None:
        logging.warning("Получено сообщение без from_user (например, от канала).")
        await message.answer("❌ Не удалось определить пользователя.")
        return
    user_id = message.from_user.id
    # Получаем провайдера из настроек пользователя
    provider_name = user_settings.get(user_id, {}).get("provider",config.DEFAULT_PROVIDER)
    user_input = message.text
    
    try:
        # Фильтруем историю для API
        api_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in user_history.get(user_id, [])
            if msg.get("type") == "text"  # Только текстовые сообщения
            and "role" in msg
            and "content" in msg
        ]
        
        # Добавляем текущий запрос
        user_entry = {
            "type": "text",
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now().isoformat()
        }
        api_messages.append({"role": "user", "content": user_input})
        
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        
        provider_class = getattr(g4f.Provider,provider_name)
        response = await g4f.ChatCompletion.create_async(
            model=g4f.models.default,
            messages=api_messages,  # Используем отфильтрованные сообщения
            provider=provider_class(),
            api_key=config.API_DeepSeek
        )
        
        # Сохраняем в историю
        user_history.setdefault(user_id, []).append(user_entry)
        assistant_entry = {
            "type": "text",
            "role": "assistant",
            "content": remove_html_tags(response),
            "timestamp": datetime.now().isoformat()
        }
        user_history[user_id].append(assistant_entry)
        save_users()
        
        formatted_response = format_response(response)
        await message.answer(formatted_response, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Ошибка AI: {str(e)}")
        current_provider = AVAILABLE_PROVIDERS[0]
        await message.answer(
            f"⚠️ Ошибка: {str(e)}\n"
            f"Провайдер автоматически сброшен на {current_provider}\n"
            "Попробуйте повторить запрос"
        )

# Обработчик аудиофайлов без команды
@router.message(lambda message: message.audio or message.voice or message.document and message.document.mime_type.startswith('audio/'))
async def handle_unsolicited_audio(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, не находится ли пользователь в процессе транскрибации
    if user_transcribe_states.get(user_id) == "waiting_for_audio_transcribe":
        return  # Игнорируем, если пользователь уже в процессе
    
    # Отправляем предложение проанализировать аудиофайл
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎤 Распознать речь", callback_data="suggest_transcribe")],
        [InlineKeyboardButton(text="🖼 Сгенерировать изображение", callback_data="suggest_generate")]
    ])
    
    await message.answer(
        "Вы отправили аудиофайл. Хотите распознать речь или сгенерировать изображение?",
        reply_markup=keyboard
    )

# Обработчик нажатия на кнопку "Распознать"
@router.callback_query(lambda query: query.data == "suggest_transcribe")
async def handle_suggest_transcribe(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, я могу распознать речь в этом аудиофайле. Для этого используйте команду `/transcribe`.")
    await callback.answer()

# Обработчик для изображений и других медиафайлов
@router.message(lambda message: message.content_type in ['photo', 'document'])
async def handle_media(message: Message):
    if message.photo:
        # Если это фото и не в состоянии анализа
        if user_analysis_states.get(message.from_user.id) == "waiting_for_image_analysis":
            return  # Пропускаем - будет обработано в handle_image_analysis
        
        await message.answer("🖼 Для генерации изображений используйте команду /image")
    elif message.document:
        # Если это документ
        if message.document.mime_type.startswith('image/'):
            if user_analysis_states.get(message.from_user.id) == "waiting_for_image_analysis":
                return  # Пропускаем - будет обработано в handle_image_analysis
            else:
                await message.answer("🖼 Для анализа изображений используйте команду /analyze")
        else:
            await message.answer("❌ Я пока не умею работать с этим типом файлов")
