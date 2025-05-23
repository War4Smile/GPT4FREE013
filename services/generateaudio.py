# services/generateaudio.py

import aiohttp
import config
import logging
import urllib.parse
import base64
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.filters import Command
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