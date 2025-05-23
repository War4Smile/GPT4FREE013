# services/imageanalysis.py
import hashlib
import aiohttp
import asyncio
import config
import logging
import urllib.parse
import base64
import uuid
from io import BytesIO
from PIL import Image
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.filters import Command
from datetime import datetime
from services.retry import generate_audio_with_retry
from services.tgapi import bot
from utils.helpers import get_user_settings, save_users, generate_short_id, remove_html_tags
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states, temp_file_store )
from utils.helpers import get_user_settings, translate_to_english

router = Router()

##################################################
######### Блок доп настроек изображения ##########

# Функция для получения настроек анализа
def get_user_analysis_settings(user_id):
    if user_id not in user_analysis_settings:
        user_analysis_settings[user_id] = {"quality": "high"}
    return user_analysis_settings[user_id]

# Обработчик команды /analyze
@router.message(Command("analyze"))
async def cmd_analyze(message: Message):
    user_id = message.from_user.id
    await message.answer("🖼 Пожалуйста, отправьте изображение для анализа.")
    user_analysis_states[user_id] = "waiting_for_image_analysis"

# Обработчик команды /analysissettings
@router.message(Command("analysissettings"))
async def cmd_analysis_settings(message: Message):
    user_id = message.from_user.id
    settings = get_user_analysis_settings(user_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Качество: {settings['quality']}", callback_data="analysis_quality")],
        [InlineKeyboardButton(text="Высокое качество", callback_data="quality_high")],
        [InlineKeyboardButton(text="Среднее качество", callback_data="quality_medium")],
        [InlineKeyboardButton(text="Низкое качество", callback_data="quality_low")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="analysis_settings_back")]
    ])
    await message.answer("🔍 Настройки анализа изображений:", reply_markup=keyboard)

# Обработчик выбора качества анализа
@router.callback_query(lambda query: query.data.startswith("quality_"))
async def handle_analysis_quality(callback: CallbackQuery):
    user_id = callback.from_user.id
    quality = callback.data.split("_")[1]
    
    if user_id not in user_analysis_settings:
        user_analysis_settings[user_id] = {}
    user_analysis_settings[user_id]["quality"] = quality
    
    await callback.message.edit_text(f"✅ Качество анализа установлено: {quality}")
    await callback.answer()

#############################################
######### Блок анализа изображения ##########

# Обработчик для изображений и других медиафайлов
@router.message(F.photo | (F.document & F.document.mime_type.startswith('image/')))
async def handle_unsolicited_image(message: Message):
    try:
        # Проверяем, не в состоянии ли пользователь ожидания
        user_id = message.from_user.id
        if user_states.get(user_id) == "waiting_for_image_description":
            return  # Игнорируем, если пользователь уже в процессе генерации
        
        if user_analysis_states.get(user_id) == "waiting_for_image_analysis":
            return  # Игнорируем, если пользователь уже в процессе анализа
        
        # Получаем file_id
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id
        else:
            logging.error("Не удалось получить file_id из изображения")
            return
        
        # Генерируем short_id
        short_id = generate_short_id(file_id)
        
        # Кнопки под изображением
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Проанализировать", callback_data=f"analyze_now_{short_id}")],
            [InlineKeyboardButton(text="🖼 Сгенерировать", callback_data="suggest_generate")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="censel_button")]
        ])
        
        # Отправляем пустое сообщение с клавиатурой
        await message.answer("Вы отправили изображение. Хотите проанализировать его содержимое?", reply_markup=keyboard)
        
    except Exception as e:
        logging.error(f"Ошибка в обработке изображения: {str(e)}")
        await message.answer("⚠️ Ошибка при обработке изображения.")


# services/imageanalysis.py
async def analyze_image(message: Message, file_id: str):
    user_id = message.from_user.id
    
    try:
        # Получаем file_info напрямую
        file_info = await bot.get_file(file_id)
        logging.info(f"Получен file_id: {file_id}, размер: {file_info.file_size} байт")
        
        # Формируем прямую ссылку
        file_url = f"https://api.telegram.org/file/bot {bot.token}/{file_info.file_path}"
        
        # Формируем payload с URL вместо base64
        payload = {
            "model": config.IMAGE_ANALYSIS_MODEL,
            "messages": [
                {"role": "system", "content": "Опишите, что изображено на этой картинке на русском языке."},
                {"role": "user", "content": file_url}  # Передаем прямую ссылку
            ],
            "max_tokens": config.ANALYSIS_QUALITY_SETTINGS.get("high", 300)
        }
        
        # Отправляем запрос в API
        async with aiohttp.ClientSession() as session:
            async with session.post("https://text.pollinations.ai/openai", json=payload, timeout=30) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"Ошибка анализа: {response.status} - {error_text}")
                    await message.answer("⚠️ Ошибка: не удалось проанализировать изображение")
                    return
                
                result = await response.json()
                analysis = result['choices'][0]['message']['content']
                analysis = remove_html_tags(analysis)
        
        # Сохраняем результат
        user_entry = {
            "type": "analysis",
            "response": analysis,
            "timestamp": datetime.now().isoformat()
        }
        user_history.setdefault(user_id, []).append(user_entry)
        save_users()
        
        # Отправляем ответ пользователю
        await message.answer(f"🔍 Результат анализа изображения:\n\n{analysis}")
        
    except TelegramBadRequest as e:
        if "invalid file_id" in str(e):
            logging.error("Недействительный file_id")
            await message.answer("❌ Не удалось получить изображение. Повторите попытку.")
        else:
            logging.error(f"Ошибка Telegram: {str(e)}")
            await message.answer("⚠️ Ошибка при анализе изображения.")
    except aiohttp.ClientError as e:
        logging.error(f"Ошибка сети при загрузке изображения: {str(e)}")
        await message.answer("⚠️ Ошибка загрузки изображения.")
    except Exception as e:
        logging.error(f"Неизвестная ошибка при анализе: {str(e)}")
        await message.answer("⚠️ Ошибка при анализе изображения.")
    finally:
        # Сбрасываем состояние пользователя
        user_analysis_states[user_id] = None

async def analyze_and_respond(message: Message, file_id: str):
    try:
        # Запускаем анализ изображения
        await analyze_image(message, file_id)
    except Exception as e:
        logging.error(f"Ошибка при анализе изображения: {str(e)}")
        await message.answer("⚠️ Ошибка при анализе изображения.")

@router.callback_query(lambda query: query.data.startswith("analyze_now_"))
async def handle_analyze_now(callback: CallbackQuery):
    try:
        # Подтверждаем нажатие кнопки и убираем клавиатуру
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)  # Убираем кнопки
        await callback.message.delete()
        
        # Извлекаем short_id
        short_id = callback.data.split("analyze_now_", 1)[1]
        
        # Проверяем, существует ли short_id
        if short_id not in temp_file_store:
            await callback.message.answer("❌ Срок действия запроса истек.")
            return
        
        # Получаем оригинальный file_id
        stored = temp_file_store[short_id]
        file_id = stored["file_id"]
        
        # Проверяем возраст записи
        if (datetime.now() - stored["timestamp"]).total_seconds() > 86400:  # 24 часа
            del temp_file_store[short_id]
            await callback.message.answer("⏳ Ссылка на изображение устарела. Повторите запрос.")
            return
        
        # Запускаем анализ
        await analyze_image(callback.message, file_id)
        
    except Exception as e:
        logging.error(f"Ошибка при анализе изображения: {str(e)}")
        await callback.message.answer("⚠️ Ошибка при анализе изображения.")

# Обработчик изображений для анализа
@router.message(lambda message: message.photo or (message.document and message.document.mime_type.startswith('image/')))
async def handle_image_analysis(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, ожидаем ли мы изображение для анализа
    if user_analysis_states.get(user_id) != "waiting_for_image_analysis":
        return  # Игнорируем, если не запрашивали анализ
    
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    
    try:
        # Получаем файл
        photo = message.photo[-1] if message.photo else message.document
        file_info = await bot.get_file(photo.file_id)
        file_path = file_info.file_path
        image_data = await bot.download_file(file_path)
        
        # Проверка размера
        if file_info.file_size > config.MAX_IMAGE_SIZE:
            await message.answer("❌ Размер изображения превышает 512 MB")
            return
        
        # Преобразуем изображение в base64
        image = Image.open(BytesIO(image_data.getvalue()))
        image_format = image.format.lower() or "jpeg"
        image_data.seek(0)
        base64_image = base64.b64encode(image_data.read()).decode('utf-8')
        
        # Получаем настройки пользователя
        analysis_settings = get_user_analysis_settings(user_id)
        quality = analysis_settings["quality"]
        max_tokens = config.ANALYSIS_QUALITY_SETTINGS[quality]
        
        # Формируем запрос к API
        payload = {
            "model": config.IMAGE_ANALYSIS_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Вы — опытный аналитик изображений, опишите содержимое картинки подробно на русском языке."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{image_format};base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": max_tokens
        }
        
        logging.info(f"Анализ изображения от {user_id}")
        
        # Отправляем запрос
        async with aiohttp.ClientSession() as session:
            async with session.post("https://text.pollinations.ai/openai", json=payload, timeout=300) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"Ошибка анализа: {response.status} - {error_text}")
                    await message.answer("⚠️ Ошибка: не удалось проанализировать изображение")
                    return
                
                result = await response.json()
                analysis = result['choices'][0]['message']['content']
                analysis = remove_html_tags(analysis)  # Очищаем ответ от HTML-тегов
                
                # Сохраняем в историю
                user_entry = {
                    "type": "analysis",
                    "prompt": "Опишите, что изображено на этой картинке",
                    "timestamp": datetime.now().isoformat()
                }
                user_history.setdefault(user_id, []).append(user_entry)
                
                assistant_entry = {
                    "type": "analysis",
                    "response": analysis,
                    "quality": quality,
                    "timestamp": datetime.now().isoformat()
                }
                user_history[user_id].append(assistant_entry)
                save_users()
                
                # Отправляем результат
                await message.answer(f"🔍 Результат анализа изображения:\n\n{analysis}")
                
    except Exception as e:
        logging.error(f"Ошибка анализа изображения: {str(e)}")
        await message.answer(f"⚠️ Ошибка при анализе изображения: {str(e)}")
    
    finally:
        # Сбрасываем состояние
        user_analysis_states[user_id] = None

# Обработчик нажатия на кнопку "Сгенерировать"
@router.callback_query(lambda query: query.data == "suggest_generate")
async def handle_suggest_generate(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, вы можете сгенерировать новое изображение. Для этого используйте команду `/image`.")
    await callback.answer()

# Обработчик нажатия на кнопку "Отмена"
@router.callback_query(lambda query: query.data == "censel_button")
async def handle_censel_button(callback: CallbackQuery):
    await callback.message.edit_text("При необходимости вы можете проанализировать изображение. Для этого используйте команду `/analyse`.")
    await callback.answer()

