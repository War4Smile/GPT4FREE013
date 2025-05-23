# utils/helpers.py
import aiohttp
import hashlib
import asyncio
import base64
import config 
import g4f
import os
import tempfile
import re
import logging
from aiogram import types
from datetime import datetime
from bs4 import BeautifulSoup
from pydub import AudioSegment
from database import user_history, user_settings
from langdetect import detect
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states, temp_file_store )


# Сохранение истории пользователя
def save_user_info(user: types.User):
    user_id = user.id
    if user_id not in user_info:
        user_info[user_id] = {
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'date_joined': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'last_activity': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    else:
        # Обновляем только изменяемые поля
        user_info[user_id].update({
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name
        })

# Обновление истории активности пользователя
async def update_user_activity(user: types.User):
    user_id = user.id
    if user_id in user_info:
        user_info[user_id]['last_activity'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        save_user_info(user)

def is_admin(user_id: int):
    return user_id in config.ADMINS

async def auto_save_task():
    while True:
        await asyncio.sleep(300)
        save_users()
        logging.info("Автосохранение данных пользователей")

def get_user_settings(user_id: int):
    return user_settings.get(user_id, {"model": "flux", "width": 1080, "height": 1920})

def convert_to_mp3(input_path, output_path):
    """Конвертация аудио в MP3 для уменьшения размера"""
    try:
        audio = AudioSegment.from_file(input_path)
        # Уменьшаем битрейт и количество каналов
        audio.export(output_path, format="mp3", bitrate="64k", parameters=["-ac", "1"])
        return True
    except Exception as e:
        logging.error(f"Ошибка конвертации в MP3: {str(e)}")
        return False

def encode_audio_base64(audio_path: str) -> str | None:
    try:
        with open(audio_path, "rb") as audio_file:
            return base64.b64encode(audio_file.read()).decode('utf-8')
    except Exception as e:
        logging.error(f"Ошибка кодирования в Base64: {str(e)}")
        return None

def split_audio(file_path, chunk_length_ms=300000):  # 5 минут
    """Разделение аудиофайла на части"""
    try:
        audio = AudioSegment.from_file(file_path)
        chunks = []
        
        for i in range(0, len(audio), chunk_length_ms):
            chunk = audio[i:i+chunk_length_ms]
            chunk_path = os.path.join(tempfile.gettempdir(), f"chunk_{i//1000}.mp3")
            chunk.export(chunk_path, format="mp3", bitrate="64k", parameters=["-ac", "1"])
            chunks.append(chunk_path)
        
        return chunks
    except Exception as e:
        logging.error(f"Ошибка разбиения аудио: {str(e)}")
        return []

###############################################
########### Вспомогательные функции ###########
# Функция для очистки HTML-тегов
def remove_html_tags(text):
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text().strip()

# Функция для автоматического определения языка
def auto_detect_language(text):
    try:
        return detect(text)
    except:
        return "Не удалось определить язык"

# Функция для форматирования ответа
def format_response(response):
    response = re.sub(r'\n\s*\n', '\n\n', response.strip())
    
    code_block_pattern = re.compile(r'(```(.*?)```|!\[.*?\]\((.*?)\))', re.DOTALL)
    formatted_response = []
    last_end = 0
    
    for match in code_block_pattern.finditer(response):
        plain_text = response[last_end:match.start()].strip()
        if plain_text:
            formatted_response.append(f"<b>{plain_text}</b>")
        
        code_content = match.group(2) or match.group(3)
        if code_content:
            if match.group(2):  # Это кодовый блок
                formatted_response.append(f"<pre><code>{code_content.strip()}</code></pre>")
            elif match.group(3):  # Это изображение
                formatted_response.append(f"![{plain_text}]({code_content})")
        
        last_end = match.end()
    
    remaining_text = response[last_end:].strip()
    if remaining_text:
        formatted_response.append(f"<b>{remaining_text}</b>")
    
    return "\n".join(formatted_response)

def generate_short_id(file_id: str) -> str:
    """Генерирует short_id и сохраняет file_id во временное хранилище"""
    timestamp = datetime.now()
    short_id = hashlib.md5(file_id.encode()).hexdigest()[:8]
    temp_file_store[short_id] = {"file_id": file_id, "timestamp": timestamp}
    return short_id


#####################################################
########### Обработчик первода сообщений ############

# Функция перевода на английский
async def translate_to_english(text):
    """Перевод текста на английский с помощью доступной ИИ-модели"""
    if not text:
        return text
    
    # Определяем язык
    from langdetect import detect
    detected_lang = detect(text)
    if detected_lang != "ru":
        return text  # Не переводим, если не русский
    
    try:
        # Используем рабочую модель
        async with aiohttp.ClientSession() as session:
            response = await g4f.ChatCompletion.create_async(
                model=config.DEFAULT_TRANSLATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional translator. You main task translate the following text to English, give only translate:"},
                    {"role": "user", "content": text}
                ],
                provider=getattr(g4f.Provider,config.DEFAULT_TRANSLATION_PROVIDER),
                api_key=None
            )
        return response.strip()
    except Exception as e:
        logging.warning(f"Не удалось перевести текст: {str(e)}")
        return text  # Возвращаем оригинальный текст при ошибке

# Функция перевода на Русский
async def translate_to_russian(text):
    """Перевод текста на русский с помощью доступной ИИ-модели"""
    if not text:
        return text
    
    try:
        # Используем рабочую модель
        async with aiohttp.ClientSession() as session:
            response = await g4f.ChatCompletion.create_async(
                model=config.DEFAULT_TRANSLATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional translator. You main task translate the following text to Russian, give only translate:"},
                    {"role": "user", "content": text}
                ],
                provider=getattr(g4f.Provider,config.DEFAULT_TRANSLATION_PROVIDER),
                api_key=None
            )
        return response.strip()
    except Exception as e:
        logging.warning(f"Не удалось перевести текст: {str(e)}")
        return text  # Возвращаем оригинальный текст при ошибке