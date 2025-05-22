# services/imageanalysis.py
import aiohttp
import config
import logging
import urllib.parse
import base64
from io import BytesIO
from PIL import Image
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from services.retry import generate_audio_with_retry
from utils.helpers import get_user_settings, save_users
from database import (
                        save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states )
from utils.helpers import get_user_settings, translate_to_english
from services.tgapi import bot

router = Router()

##################################################
########### Блок анализа изображений ###########

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

# Обработчик возврата к основному меню
@router.callback_query(lambda query: query.data == "analysis_settings_back")
async def handle_analysis_settings_back(callback: CallbackQuery):
    await callback.message.edit_text("⚙️ Выберите настройку:")
    # ... (ваше текущее меню настроек)

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
                        {"type": "text", "text": "Опишите, что изображено на этой картинке"},
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
            async with session.post("https://text.pollinations.ai/openai ", json=payload, timeout=300) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"Ошибка анализа: {response.status} - {error_text}")
                    await message.answer("⚠️ Ошибка: не удалось проанализировать изображение")
                    return
                
                result = await response.json()
                analysis = result['choices'][0]['message']['content']
                
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

# Обработчик для изображений без команды
@router.message(lambda message: message.photo or (message.document and message.document.mime_type.startswith('image/')))
async def handle_unsolicited_image(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, не находится ли пользователь в процессе генерации изображения
    if user_states.get(user_id) == "waiting_for_image_description":
        return  # Игнорируем, если пользователь уже в процессе генерации
    
    # Проверяем, не запрашиваем ли мы анализ изображения
    if user_analysis_states.get(user_id) == "waiting_for_image_analysis":
        return  # Игнорируем, если пользователь уже в процессе анализа

    # Отправляем предложение проанализировать изображение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проанализировать", callback_data="suggest_analyze")],
        [InlineKeyboardButton(text="🖼 Сгенерировать", callback_data="suggest_generate")]
    ])
    
    await message.answer(
        "Вы отправили изображение. Хотите проанализировать его содержимое или сгенерировать новое?",
        reply_markup=keyboard
    )

# Обработчик нажатия на кнопку "Проанализировать"
@router.callback_query(lambda query: query.data == "suggest_analyze")
async def handle_suggest_analyze(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, я могу проанализировать это изображение. Для этого используйте команду `/analyze`.")
    await callback.answer()

# Обработчик нажатия на кнопку "Сгенерировать"
@router.callback_query(lambda query: query.data == "suggest_generate")
async def handle_suggest_generate(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, вы можете сгенерировать новое изображение. Для этого используйте команду `/image`.")
    await callback.answer()


###########################################################
####### Обработчик генерации аудиофайла Polinations #######
# Блок генерации аудио из текста
@router.message(Command("generateaudio"))
async def cmd_generate_audio(message: Message):
    user_id = message.from_user.id
    reply = message.reply_to_message
    
    if not reply or not reply.text:
        await message.answer("❌ Ответьте на текстовое сообщение командой `/generateaudio`")
        return
    
    await message.answer("🎙️ Выберите голос для генерации аудио:", reply_markup=voice_selection_keyboard())
    user_states[user_id] = {
        "action": "generating_audio",
        "text": reply.text,
        "message_id": reply.message_id
    }

def voice_selection_keyboard():
    """Клавиатура для выбора голоса"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=voice, callback_data=f"voice_{voice}") for voice in config.SUPPORTED_VOICES],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="voice_cancel")]
    ])

@router.callback_query(lambda query: query.data.startswith("voice_"))
async def handle_voice_selection(callback: CallbackQuery):
    user_id = callback.from_user.id
    state = user_states.get(user_id)
    
    if not state or state.get("action") != "generating_audio":
        await callback.answer("❌ Нет активного запроса генерации аудио")
        return
    
    voice = callback.data.split("_")[1]
    
    if voice == "cancel":
        await callback.message.delete()
        user_states.pop(user_id, None)
        return
    
    text = state.get("text", "")
    
    # Проверяем длину текста
    if len(text) > 4096:
        # Используем POST-метод для длинных текстов
        await generate_audio_post(user_id, text, voice, callback)
    else:
        # Используем GET-метод для коротких текстов
        await generate_audio_get(user_id, text, voice, callback)

async def generate_audio_get(user_id, text, voice, callback):
    try:
        encoded_text = urllib.parse.quote(text)
        payload = {
            "url": f"https://text.pollinations.ai/ {encoded_text}?model={config.TTS_MODEL}&voice={voice}"
        }
        
        audio_data = await generate_audio_with_retry(payload, method="GET")
        
        # Сохраняем в историю
        save_audio_history(user_id, text, voice, "GET")
        
        # Создаем и отправляем аудиофайл
        input_file = BufferedInputFile(audio_data, filename='generated_audio.mp3')
        await callback.message.answer_audio(input_file, caption=f"🎙️ Аудио сгенерировано с голосом: {voice}")
        await callback.message.delete()
    
    except Exception as e:
        logging.error(f"Ошибка генерации через GET: {str(e)}")
        await callback.message.answer(f"⚠️ Ошибка: {str(e)}")

async def generate_audio_post(user_id, text, voice, callback):
    try:
        payload = {
            "model": config.TTS_MODEL,
            "messages": [{"role": "user", "content": text}],
            "voice": voice
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post("https://text.pollinations.ai/openai ", json=payload, timeout=300) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logging.error(f"Ошибка генерации аудио: {response.status} - {error_text}")
                    await callback.message.answer("⚠️ Не удалось сгенерировать аудио")
                    return
                
                result = await response.json()
        
        # Извлекаем base64-аудио
        try:
            audio_data_base64 = result['choices'][0]['message']['audio']['data']
            audio_binary = base64.b64decode(audio_data_base64)
        except (KeyError, IndexError, base64.binascii.Error) as e:
            logging.error(f"Ошибка обработки ответа: {str(e)}")
            await callback.message.answer("❌ Ошибка: не удалось получить аудио из ответа")
            return
        
        # Сохраняем в историю
        save_audio_history(user_id, text, voice, "POST")
        
        # Создаем и отправляем аудиофайл
        input_file = BufferedInputFile(audio_binary, filename='generated_audio.mp3')
        await callback.message.answer_audio(input_file, caption=f"🎙️ Аудио сгенерировано с голосом: {voice}")
        await callback.message.delete()
    
    except Exception as e:
        logging.error(f"Ошибка генерации через POST: {str(e)}")
        await callback.message.answer(f"⚠️ Ошибка: {str(e)}")

def save_audio_history(user_id, text, voice, method):
    """Сохранение генерации аудио в историю"""
    entry = {
        "type": "audio",
        "prompt": text[:100],  # Обрезаем длинные тексты
        "voice": voice,
        "method": method,
        "timestamp": datetime.now().isoformat()
    }
    user_history.setdefault(user_id, []).append(entry)
    save_users()

def split_text_into_chunks(text, max_length=4096):
    """Разделение текста на части для генерации аудио"""
    words = text.split()
    chunks = []
    current_chunk = ""
    
    for word in words:
        if len(current_chunk) + len(word) + 1 <= max_length:
            current_chunk += " " + word
        else:
            chunks.append(current_chunk.strip())
            current_chunk = word
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks