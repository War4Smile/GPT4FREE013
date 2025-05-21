# bot.py
import re
import os
import tempfile
import logging
import asyncio
import g4f
import aiohttp
import base64
import requests
import random
import hashlib
import httpx
import json
import config
import urllib.parse
import pollinations as ai
from aiogram import Bot, Dispatcher, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.enums import ParseMode, ChatAction
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from alworkproviders import AVAILABLE_PROVIDERS
from bs4 import BeautifulSoup
from datetime import datetime
from g4f.client import AsyncClient
from httpx import HTTPStatusError
from io import BytesIO
from langdetect import detect
from PIL import Image
from pydub import AudioSegment
from speechmatics.batch_client import BatchClient
from speechmatics.models import BatchTranscriptionConfig
from tenacity import retry, stop_after_attempt, wait_exponential

from config import (
    BOT_TOKEN,
    API_DeepSeek,
    IMAGE_PROVIDER,
    IMAGE_MODEL,
    SPEECHMATICS_API,
    TRANSCRIPTION_LANGUAGE,
    ADMINS,
    DEFAULT_TRANSLATION_MODEL,
    DEFAULT_TRANSLATION_PROVIDER
    )

# Настройка логирования
logging.basicConfig(level=logging.INFO)

###################################################
########### Словари для хранения данных ###########

# Словарь для хранения настроек пользователей
user_settings = {}
# Словарь для хранения истории сообщений пользователей
user_history = {}
# Словарь для хранения истории запросов на генерацию изображений
image_requests = {}
user_states = {}
# Словарь для хранения информации о пользователях
user_info = {}
# Словарь для состояний админов
admin_states = {}
# Словарь для хранения данных о последнем запросе на изображение
last_image_requests = {}
# Словарь для хранения истории генерации изображений
image_history = {}
# Файл для хранения данных пользователей
USER_DATA_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'user_data.json'))
# Файл для хранения заблокированных пользователей
BLOCKED_USERS_FILE = 'blocked_users.json'
# Словарь для хранения заблокированных пользователей
blocked_users = {}
# Словарь для хранения состояний анализа изображений
user_analysis_states = {}  # Например: {user_id: "waiting_for_image_analysis"}
# Словарь для хранения настроек анализа
user_analysis_settings = {}  # Например: {user_id: {"quality": "high"}}
# Словарь для хранения текущих запросов анализа
image_analysis_requests = {}
# Словарь для хранения состояний транскрибации
user_transcribe_states = {}  # Например: {user_id: "waiting_for_audio_transcribe"}

# Списки команд для разных типов пользователей
user_commands = [    
    BotCommand(command="start", description="🔑 Запуск бота"),
    BotCommand(command="image", description="🖼 Генерация изображения"),
    BotCommand(command="analyze", description="🔍 Анализ изображения"),
    BotCommand(command="generateaudio", description="🎙️ Сгенерировать аудио из текста"),
    BotCommand(command="translatetoeng", description="🔍 Перевести текст на Английский"),
    BotCommand(command="translatetoru", description="🔍 Перевести текст на Русский"),
    BotCommand(command="clear", description="🧹 Очистка истории"),
    BotCommand(command="help", description="📝 Список команд"),
    BotCommand(command="provider", description="🔄 Изменить модель GPT"),
    BotCommand(command="imagesettings", description="⚙️ Настройки изображения")
]
admin_commands = user_commands + [
    BotCommand(command="tts", description="🎙️ Сгенерировать аудио из текста"),
    BotCommand(command="transcribe", description="🎤 Распознать речь из аудиофайла"),
    BotCommand(command="analysissettings", description="🔎 Настройки анализа"),
    BotCommand(command="adminusers", description="👥 Администрирование пользователей")
]


###############################################
########### Вспомогательные функции ###########

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
                    "height": 1920
                }
            
            save_user_info(user)
            await update_user_activity(user)  # Добавлен await
        
        return await handler(event, data)

# Функция загрузки архива истории
def migrate_old_history():
    for user_id, history in user_history.items():
        for i, entry in enumerate(history):
            if 'type' not in entry:
                # Предполагаем, что старые записи - текстовые
                history[i] = {
                    'type': 'text',
                    'role': entry.get('role', 'user'),
                    'content': entry.get('content', ''),
                    'timestamp': entry.get('timestamp', '')
                }
    save_users()


# Функция загрузки пользователей
def load_users():
    global user_info, user_history, user_settings, image_requests
    try:
        if not os.path.exists(USER_DATA_FILE):
            logging.warning("Файл данных не найден, создаем новый")
            save_users()  # Создаем файл с базовой структурой
            return
            
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Проверка структуры данных
        required_keys = ['user_info', 'user_history', 'user_settings', 'image_requests']
        for key in required_keys:
            if key not in data:
                raise KeyError(f"Отсутствует ключ {key} в файле данных")
            
            # Конвертируем строковые ключи в целые числа
            user_info = {int(k): v for k, v in data.get('user_info', {}).items()}
            user_history = {int(k): v for k, v in data.get('user_history', {}).items()}
            user_settings = {int(k): v for k, v in data.get('user_settings', {}).items()}
            image_requests = {int(k): v for k, v in data.get('image_requests', {}).items()}
            
            logging.info("Данные пользователей загружены.")
            migrate_old_history()

    except json.JSONDecodeError as e:
        logging.error(f"Ошибка формата JSON: {str(e)}")
        # Создаем резервную копию битого файла
        backup_path = f"{USER_DATA_FILE}.corrupted.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        os.rename(USER_DATA_FILE, backup_path)
        logging.warning(f"Создана резервная копия битого файла: {backup_path}")
        # Инициализируем заново
        user_info = {}
        user_history = {}
        user_settings = {}
        image_requests = {}
        save_users()
    except Exception as e:
        logging.error(f"Критическая ошибка загрузки: {str(e)}")
        logging.warning("Файл данных не найден, создаем новый")
        user_info = {}
        user_history = {}
        user_settings = {}
        image_requests = {}
        save_users()  # Создаем файл с начальными данными

# Функция сохранения пользователей
def save_users():
    try:
        # Создаем директорию, если её нет
        os.makedirs(os.path.dirname(USER_DATA_FILE), exist_ok=True)
        
        data = {
            'user_info': {str(k): v for k, v in user_info.items()},
            'user_history': {str(k): v for k, v in user_history.items()},
            'user_settings': {str(k): v for k, v in user_settings.items()},
            'image_requests': {str(k): v for k, v in image_requests.items()}
        }

        with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
             json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info("Данные пользователей сохранены.")
    except Exception as e:
        logging.error(f"Ошибка сохранения данных: {str(e)}")

# Функция загрузки заблокированных пользователей
def load_blocked_users():
    global blocked_users
    try:
        with open(BLOCKED_USERS_FILE, 'r', encoding='utf-8') as f:
            blocked_users = json.load(f)
            logging.info("Данные заблокированных пользователей загружены.")

    except FileNotFoundError:
        logging.warning("Файл заблокированных пользователей не найден. Будет создан новый.")
        blocked_users = {}

    except json.JSONDecodeError:
        logging.error("Ошибка при загрузке данных заблокированных пользователей. Файл поврежден.")
        blocked_users = {}


# Функция сохранения заблокированных пользователей
def save_blocked_users():
    with open(BLOCKED_USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(blocked_users, f, ensure_ascii=False, indent=4)
    logging.info("Данные заблокированных пользователей сохранены.")

# Префикс для перегенерации изображений
REGENERATE_CALLBACK_PREFIX = "regenerate:"
regenerate_cb = REGENERATE_CALLBACK_PREFIX 

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

# Создаем папку temp, если она не существует
if not os.path.exists('temp'):
    os.makedirs('temp')

# Функция для очистки папки temp
def clear_temp_folder():
    for filename in os.listdir('temp'):
        file_path = os.path.join('temp', filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Ошибка при удалении файла {file_path}: {str(e)}")

# Вызов функции очистки при запуске
clear_temp_folder()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def get_available_models():
    """Загружает список доступных моделей изображений с Pollinations AI"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://image.pollinations.ai/models", timeout=30) as response:
                if response.status == 200:
                    models = await response.json()
                    logging.info(f"✅ Получено {len(models)} моделей изображений")
                    return models
                else:
                    error_text = await response.text()
                    logging.error(f"❌ Ошибка загрузки моделей: {response.status} - {error_text}")
                    return ["flux", "flux-anime", "flux-cablyai"]  # Резервные модели
    except Exception as e:
        logging.error(f"⚠️ Не удалось загрузить модели: {str(e)}")
        return ["flux", "flux-anime", "flux-cablyai"]

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def transcribe_with_retry(payload):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://text.pollinations.ai/openai", 
            json=payload, 
            timeout=300
        ) as response:
            if response.status == 200:
                return await response.json()
            error_text = await response.text()
            logging.error(f"Pollinations API ошибка: {response.status} - {error_text}")
            raise Exception(f"Ошибка API: {error_text}")

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
                model=DEFAULT_TRANSLATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional translator. You main task translate the following text to English, give only translate:"},
                    {"role": "user", "content": text}
                ],
                provider=getattr(g4f.Provider,DEFAULT_TRANSLATION_PROVIDER),
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
                model=DEFAULT_TRANSLATION_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional translator. You main task translate the following text to Russian, give only translate:"},
                    {"role": "user", "content": text}
                ],
                provider=getattr(g4f.Provider,DEFAULT_TRANSLATION_PROVIDER),
                api_key=None
            )
        return response.strip()
    except Exception as e:
        logging.warning(f"Не удалось перевести текст: {str(e)}")
        return text  # Возвращаем оригинальный текст при ошибке

#####################################################
########### Проверка доступности Telegram ###########

# Функция проверки доступности Telegram API
async def check_telegram_api_availability():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.telegram.org", timeout=10) as response:
                return response.status == 200
    except Exception as e:
        logging.error(f"Ошибка проверки Telegram API: {e}")
        return False

# Инициализация бота
session = AiohttpSession()
bot = Bot(
    token=BOT_TOKEN,
    session=session,
    timeout=40
)
dp = Dispatcher()

dp.update.middleware(UserMiddleware())

# Текущий провайдер и настройки
current_provider = AVAILABLE_PROVIDERS[0]

###################################################
########### Дополнения для админки ################

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
    return user_id in ADMINS


async def auto_save_task():
    while True:
        await asyncio.sleep(300)
        save_users()
        logging.info("Автосохранение данных пользователей")

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
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    await set_commands_for_user(user_id)
    await message.answer("Привет! Я бот с функциями AI. Могу общаться и генерировать изображения, если нужна дополнительная информация используйте /help.")

# Модифицированный обработчик /help
@dp.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    await set_commands_for_user(user_id)  # Обновляем команды при запросе помощи
    if is_admin(user_id):
        help_text = """

📝 Команды администратора:
/adminusers - 👥 Администрирование пользователей
/image - 🖼 Генерация изображения
/clear - 🧹 Очистка истории
/help - 📝 Список команд
/provider - 🔄 Изменить модель GPT
/imagesettings - ⚙️ Настройки изображения

"""

    else:

        help_text = """

📝 Доступные команды:
/start - 🔑 Перезапуск бота
/image - 🖼 Генерация изображения
/clear - 🧹 Очистка истории
/help - 📝 Список команд
/provider - 🔄 Изменить модель GPT
/imagesettings - ⚙️ Настройки изображения

"""

    await message.answer(help_text)

# Обработчик команды /image
@dp.message(Command("image"))
async def cmd_image(message: Message):
    await message.answer("🖼 Пожалуйста, введите описание изображения, которое вы хотите сгенерировать:")
    user_id = message.from_user.id
    user_states[user_id] = "waiting_for_image_description"  # Устанавливаем состояние ожидания
    # Сохраняем текущее состояние пользователя
    image_requests[user_id] = []  # Инициализируем историю запросов на изображение

# Обработчик /provider
@dp.message(Command("provider"))
async def cmd_provider(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=provider, callback_data=f"provider_{provider}")]
        for provider in AVAILABLE_PROVIDERS
    ])
    await message.answer("Выберите провайдера для текста:", reply_markup=keyboard)

# Обработчик команды /maketext
@dp.message(Command("maketext"))
async def cmd_maketext(message: Message):
    # Сохраняем сообщение, чтобы удалить его позже
    sent_message = await message.answer("🎤 Пожалуйста, отправьте аудиофайл (форматы: aac, amr, flac, m4a, mp3, mp4, mpeg, ogg, wav) до 512Mb.")
    user_states[message.from_user.id] = "waiting_for_audio_file"  # Устанавливаем состояние ожидания

####################################################
################### Админ панель ###################

# Обработчик для статистики
@dp.callback_query(lambda query: query.data == "admin_stats")
async def handle_admin_stats(query: CallbackQuery):
    try:
        stats_text = "📊 Общая статистика:\n\n"
        total_users = len(user_info)
        total_messages = sum(len(h) for h in user_history.values())
        total_blocked = len(blocked_users)
        total_transcriptions = sum(
            1 for entries in user_history.values() 
            for entry in entries 
            if entry.get("type") == "transcribe"
        )
        total_audio = sum(
            1 for entries in user_history.values() 
            for entry in entries 
            if entry.get("type") == "audio"
        )

        stats_text += f"👥 Всего пользователей: {total_users}\n"
        stats_text += f"📨 Всего сообщений: {total_messages}\n"
        stats_text += f"🚫 Заблокированных: {total_blocked}\n\n"
        stats_text += f"\n🎤 Всего транскрибаций: {total_transcriptions}"
        stats_text += f"\n🎙️ Всего аудио: {total_audio}"
        stats_text += "Топ активных пользователей:\n"
        
        # Сортируем пользователей по количеству сообщений
        active_users = sorted(
            [(uid, len(history)) for uid, history in user_history.items()],
            key=lambda x: x[1],
            reverse=True
        )[:5]  # Топ-5
        
        for i, (uid, count) in enumerate(active_users, 1):
            user_info_str = get_user_info_str(uid)
            stats_text += f"{i}. {user_info_str} - {count} сообщ.\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Назад", callback_data="admin_main_menu")]
        ])
        
        await query.message.edit_text(stats_text, reply_markup=keyboard)
        await query.answer()
        
    except Exception as e:
        logging.error(f"Ошибка статистики: {str(e)}")
        await query.answer("❌ Ошибка загрузки статистики")

# Обработчик главного меню
@dp.callback_query(lambda query: query.data == "admin_main_menu")
async def handle_admin_main_menu(query: CallbackQuery):
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users_list")],
            [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_blocked_list")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
        ])
        
        await query.message.edit_text(
            "🛠 Админ-панель. Выберите действие:",
            reply_markup=keyboard
        )
        await query.answer()
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logging.error(f"Ошибка возврата в меню: {str(e)}")
            await query.answer("⚠️ Ошибка обновления меню")
    except Exception as e:
        logging.error(f"Ошибка главного меню: {str(e)}")
        await query.answer("❌ Ошибка загрузки меню")

# Обработчик закрытия меню
@dp.callback_query(lambda query: query.data == "admin_close")
async def handle_admin_close(query: CallbackQuery):
    await query.message.delete()
    await query.answer("🔒 Меню закрыто")

# Вспомогательная функция для форматирования времени
def format_timestamp(iso_timestamp):
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return "неизвестное время"

# Вспомогательная функция для иконок ролей
def get_role_icon(role):
    return {
        'user': '👤',
        'assistant': '🤖'
    }.get(role, '❓')

# Получаем информацию о пользователе
def get_user_info_str(user_id: int) -> str:
    if user_id in user_info:
        info = user_info.get(user_id, {})
        name = f"{info['first_name']} {info['last_name']}" if info.get('last_name') else info['first_name']
        username = f"(@{info['username']})" if info.get('username') else ""
        return f"{name} {username}" if name else f"ID: {user_id}"
    return f"ID: {user_id}"

# Обработчик для команды /adminusers
# Обработчик команды админ-панели
@dp.message(Command("adminusers"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_blocked_list")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ])
    
    await message.answer(
        "🛠 Админ-панель. Выберите действие:",
        reply_markup=keyboard
    )

# Обработчик списка пользователей
@dp.callback_query(lambda query: query.data == "admin_users_list")
async def handle_users_list(query: CallbackQuery):
    unique_users = {}
    for uid, info in user_info.items():
        unique_users[uid] = info
    
    if not unique_users:
        await query.answer("📂 База пользователей пуста")
        return
    
    sorted_users = sorted(unique_users.items(), key=lambda x: x[0])
    buttons = []
    for uid, info in sorted_users:
        user_text = f"👤 {info['first_name']} {info['last_name'] or ''} (@{info['username'] or 'нет'})"
        buttons.append([InlineKeyboardButton(text=user_text, callback_data=f"admin_user_{uid}")])
    
    buttons.append([InlineKeyboardButton(text="↩️ Назад", callback_data="admin_main_menu")])
    
    await query.message.edit_text(
        "📋 Список пользователей:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


# Обработчик для отправки сообщения пользователю
@dp.callback_query(lambda query: query.data.startswith("admin_message_"))
async def handle_admin_message(query: CallbackQuery):
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.answer("❌ Доступ запрещен")
        return
    
    user_id = int(query.data.split("_")[2])
    admin_states[admin_id] = {"action": "message", "target": user_id}
    
    await query.message.answer("📨 Введите сообщение для пользователя:")
    await query.answer()

# Обработчик текстовых сообщений для админских действий
@dp.message(lambda message: is_admin(message.from_user.id) and message.from_user.id in admin_states)
async def handle_admin_messages(message: Message):
    admin_id = message.from_user.id
    state = admin_states.get(admin_id)
    
    if not state:
        return
    
    text = message.text
    action = state.get("action")
    target = state.get("target")
    
    try:
        if action == "message" and target:
            # Создаем клавиатуру
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Принято", callback_data=f"response_accepted_{admin_id}_{target}"),
                 InlineKeyboardButton(text="❌ Не принято", callback_data=f"response_rejected_{admin_id}_{target}")]
            ])
            
            # Получаем информацию о пользователе
            user_info_str = get_user_info_str(target)
            
            # Отправляем сообщение пользователю
            user_message = await bot.send_message(
                chat_id=target,
                text=f"📨 Сообщение от администратора:\n\n{text}",
                reply_markup=keyboard
            )
            
            # Отправляем подтверждение администратору
            await bot.send_message(
                chat_id=admin_id,
                text=f"✅ Сообщение отправлено пользователю: {user_info_str}",
                reply_to_message_id=message.message_id
            )
            
            # Сохраняем контекст
            admin_states[admin_id] = {
                "action": "message",
                "target": target,
                "admin_msg_id": message.message_id,
                "user_info_str": user_info_str,
                "message_text": text
            }

    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        logging.error(f"Admin error: {str(e)}")

# Кнопки для просмотра истории и блокировки:
@dp.callback_query(lambda query: query.data.startswith("admin_user_"))
async def handle_admin_user_selection(query: CallbackQuery):
    try:
        admin_id = query.from_user.id
        if not is_admin(admin_id):
            await query.answer("❌ Доступ запрещен")
            return
        
        user_id = int(query.data.split("_")[2])
        
        # Проверяем статус блокировки
        is_blocked = user_id in blocked_users
        status_text = "🔴 Заблокирован" if is_blocked else "🟢 Активен"
        
        # Формируем кнопки управления
        action_buttons = []
        if is_blocked:
            action_buttons.append(
                InlineKeyboardButton(text="🔓 Разблокировать", callback_data=f"admin_unblock_{user_id}")
            )
        else:
            if not is_admin(user_id):  # Запрещаем блокировку админов
                action_buttons.append(
                    InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"admin_block_{user_id}")
                )
        
        # Создаем клавиатуру с кнопкой "Назад"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📜 История", callback_data=f"admin_history_{user_id}"),
                *action_buttons
            ],
            [
                InlineKeyboardButton(text="📨 Сообщение", callback_data=f"admin_message_{user_id}")
            ],
            [
                InlineKeyboardButton(text="↩️ Назад к списку", callback_data="admin_users_list"),
                InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")
            ]
        ])
        
        await query.message.edit_text(
            f"👤 Пользователь: {get_user_info_str(user_id)}\n"
            f"ID: {user_id}\n"
            f"Статус: {status_text}",
            reply_markup=keyboard
        )
        await query.answer()
        
    except TelegramBadRequest as e:
        logging.error(f"Ошибка обновления сообщения: {str(e)}")
    except Exception as e:
        logging.error(f"Ошибка в обработчике пользователя: {str(e)}")
        await query.answer("❌ Произошла ошибка")


# Обновленный обработчик истории
@dp.callback_query(lambda query: query.data.startswith("admin_history_"))
async def handle_admin_history(query: CallbackQuery):
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.answer("❌ Доступ запрещен")
        return
    
    user_id = int(query.data.split("_")[2])
    history = user_history.get(user_id, [])
    
    # Максимальная длина сообщения Telegram (4096 символов)
    MAX_MESSAGE_LENGTH = 4000  
    history_text = "📜 История диалога:\n\n"
    valid_entries = 0
    
    # Разбиваем историю на части
    for entry in history:
        entry_type = entry.get("type", "unknown")
        
        if entry_type == "text":
            role = entry.get("role", "user")
            content = entry.get("content", "")
            timestamp = entry.get("timestamp", "")
            prefix = "👤" if role == "user" else "🤖"
            
            # Формируем запись
            entry_text = f"{prefix} {role.capitalize()} ({timestamp}):\n{content}\n\n"
            
            # Проверяем длину
            if len(history_text) + len(entry_text) > MAX_MESSAGE_LENGTH:
                await query.message.answer(history_text)
                history_text = ""  # Начинаем новую часть
            
            history_text += entry_text
            valid_entries += 1
        
        elif entry_type == "image":
            prompt = entry.get("prompt", "")
            model = entry.get("model", "")
            width = entry.get("width", "")
            height = entry.get("height", "")
            history_text += f"🖼 Запрос изображения: '{prompt}'\nМодель: {model}, Размер: {width}x{height}\n\n"
            valid_entries += 1
        
        elif entry_type == "transcribe":
            response = entry.get("response", "")
            history_text += f"🎤 Транскрибация: {response[:50]}...\n\n"
            valid_entries += 1

    # Отправляем оставшуюся часть
    if history_text.strip():
        await query.message.answer(history_text)
    
    # Кнопка возврата
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data=f"admin_user_{user_id}")]
    ])
    
    if valid_entries == 0:
        await query.message.answer("История пуста")
    
    await query.message.answer("Конец истории", reply_markup=keyboard)
    await query.answer()

# Обработчик для блокировки пользователя
@dp.callback_query(lambda query: query.data.startswith("admin_block_"))
async def handle_admin_block(query: CallbackQuery):
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.answer("❌ Доступ запрещен")
        return
    
    user_id = int(query.data.split("_")[2])
    
    if is_admin(user_id):
        await query.answer("❌ Нельзя заблокировать администратора!", show_alert=True)
        return
    
    if user_id in blocked_users:
        await query.answer("❌ Пользователь уже заблокирован", show_alert=True)
        return
    
    blocked_users[user_id] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_blocked_users()
    
    await query.answer("✅ Пользователь заблокирован", show_alert=True)
    await query.message.edit_text(f"🚫 Пользователь ID: {user_id} заблокирован.")

@dp.callback_query(lambda query: query.data.startswith("admin_unblock_"))
async def handle_admin_unblock(query: CallbackQuery):
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.answer("❌ Доступ запрещен")
        return
    
    user_id = int(query.data.split("_")[2])
    
    if user_id not in blocked_users:
        await query.answer("ℹ️ Пользователь не заблокирован", show_alert=True)
        return
    
    # Удаляем из списка заблокированных
    del blocked_users[user_id]
    save_blocked_users()
    
    await query.answer("✅ Пользователь разблокирован", show_alert=True)
    await query.message.edit_text(f"🔓 Пользователь ID: {user_id} разблокирован.")

# Обработчик для инлайн-кнопок "Принято" и "Не принято"
@dp.callback_query(lambda query: query.data.startswith("response_"))
async def handle_response(callback: CallbackQuery):
    # Логируем данные callback
    logging.info(f"Callback data: {callback.data}")

    # Извлекаем идентификаторы
    parts = callback.data.split("_")
    
    if len(parts) < 4:
        await callback.answer("❌ Ошибка: неверный формат данных.", show_alert=True)
        return

    response_type = parts[1]  # "accepted" или "rejected"
    admin_id = int(parts[2])  # ID администратора
    user_id = int(parts[3])   # ID пользователя

    # Определяем новый префикс
    if response_type == "accepted":
        new_prefix = "✅ Принято"
    else:
        new_prefix = "❌ Не принято"

    try:      
        # Получаем сохраненные данные
        state = admin_states.get(admin_id, {})
        original_text = state.get("message_text", "неизвестное сообщение")
        user_info_str = state.get("user_info_str", f"ID: {user_id}")
        
        # Формируем текст уведомления
        response_text = (
            f"👤 Пользователь {user_info_str} отметил ваше сообщение:\n"
            f"▫️ Текст сообщения: \"{original_text}\"\n"
            f"▫️ Статус: {new_prefix}"
        )
        
        # Отправляем уведомление администратору
        await bot.send_message(
            chat_id=admin_id,
            text=response_text,
            reply_to_message_id=state.get("admin_msg_id")
        )

        # Удаляем кнопки
        await callback.message.edit_reply_markup(reply_markup=None)

    except Exception as e:
        logging.error(f"Ошибка: {str(e)}")
        await callback.answer("❌ Произошла ошибка при обработке", show_alert=True)
    finally:
        # Удаляем состояние только после обработки
        if admin_id in admin_states:
            del admin_states[admin_id]


# Модифицированный обработчик блокированных пользователей
@dp.callback_query(lambda query: query.data == "admin_blocked_list")
async def handle_blocked_list(query: CallbackQuery):
    if not is_admin(query.from_user.id):
        await query.answer("❌ Доступ запрещен")
        return
    
    if not blocked_users:
        await query.answer("✅ Нет заблокированных пользователей", show_alert=True)
        return
    
    text = "🚫 Заблокированные пользователи:\n\n"
    for user_id, block_date in blocked_users.items():
        if is_admin(user_id):
            continue  # Пропускаем администраторов
            
        user_info_str = get_user_info_str(user_id)
        text += f"{user_info_str}\nДата блокировки: {block_date}\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="admin_main_menu")]
    ])
    
    await query.message.edit_text(text, reply_markup=keyboard)


# Обновляем обработчик admin_cancel для поддержки возврата
@dp.callback_query(lambda query: query.data == "admin_cancel")
async def handle_admin_cancel(query: CallbackQuery):
    await query.message.edit_text("Действие отменено")
    await query.answer()

# Проверка блокировки пользователя
@dp.message(lambda message: message.from_user.id in blocked_users)
async def handle_blocked_user(message: Message):
    await message.answer("🚫 Вы заблокированы и не можете использовать бота.")

# Обработчик инлайн-кнопок админки (ИСПРАВЛЕННЫЙ ВАРИАНТ)
@dp.callback_query(lambda query: query.data.startswith("admin_"))
async def handle_admin_actions(query: CallbackQuery):
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.answer("❌ Доступ запрещен")
        return 
    data = query.data
    # Убрана обработка admin_user_ из этого обработчика
    if data == "admin_broadcast":
        admin_states[admin_id] = {"action": "broadcast"}
        await query.message.answer("Введите сообщение для рассылки всем пользователям:")
        await query.answer()
  
    elif data == "admin_cancel":
        if admin_id in admin_states:
            del admin_states[admin_id]
        await query.message.edit_reply_markup()
        await query.answer("Действие отменено")
    # Все остальные admin-действия обрабатываются в других обработчиках

####################################################
########### Выбора провайдера и настроек ###########

# Обработчик выбора провайдера
@dp.callback_query(lambda query: query.data.startswith("provider_"))
async def handle_provider_selection(query: CallbackQuery):
    global current_provider
    # Используем split с лимитом, чтобы получить всё после первого "_"
    provider_name = query.data.split("_", 1)[1]
    current_provider = provider_name
    await query.message.edit_text(f"✅ Текстовый провайдер изменён на: {provider_name}")
    await query.answer()

# Обработчик команды /clear
@dp.message(Command("clear"))
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

# Обработчик команды /imagesettings
@dp.message(Command("imagesettings"))
async def cmd_imagesettings(message: Message):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)
    
    # Загружаем список моделей
    available_models = await get_available_models()
    
    # Формируем клавиатуру с доступными моделями
    keyboard_buttons = []
    for i, model in enumerate(available_models):
        if i % 2 == 0:
            keyboard_buttons.append([])
        keyboard_buttons[-1].append(
            InlineKeyboardButton(text=model, callback_data=f"model_{model}")
        )
    
    keyboard_buttons.extend([
        [InlineKeyboardButton(text="Квадрат (1:1)", callback_data="setting_size_square")],
        [InlineKeyboardButton(text="Портрет (1:2)", callback_data="setting_size_portrait"),
         InlineKeyboardButton(text="Пейзаж (2:1)", callback_data="setting_size_landscape")],
        [InlineKeyboardButton(text="Сбросить", callback_data="setting_reset")]
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer("⚙️ Настройки генерации изображений:", reply_markup=keyboard)


# Функция для получения настроек пользователя
def get_user_settings(user_id):
    if user_id not in user_settings:
        # Настройки по умолчанию
        user_settings[user_id] = {
            "model": "flux",
            "width": 1080,
            "height": 1920
        }
    return user_settings[user_id]

# Обработчик выбора настроек
@dp.callback_query(lambda query: query.data.startswith("setting_"))
async def handle_settings_selection(query: CallbackQuery):
    user_id = query.from_user.id
    settings = get_user_settings(user_id)
    action = query.data

    if action == "setting_model":
        # Меню выбора модели
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="flux", callback_data="model_flux")],
            [InlineKeyboardButton(text="flux-anime", callback_data="model_flux-anime")],
            [InlineKeyboardButton(text="flux-cablyai", callback_data="model_flux-cablyai")],
        ])
        await query.message.edit_text("Выберите модель:", reply_markup=keyboard)
        await query.answer()

    elif action == "setting_size_square":
        settings["width"] = 1920
        settings["height"] = 1920
        await query.message.edit_text("✅ Размер изображения изменён на: Квадрат (1920x1920)")
        await query.answer()

    elif action == "setting_size_portrait":
        settings["width"] = 1080
        settings["height"] = 1920
        await query.message.edit_text("✅ Размер изображения изменён на: Портрет (1080x1920)")
        await query.answer()

    elif action == "setting_size_landscape":
        settings["width"] = 1920
        settings["height"] = 1080
        await query.message.edit_text("✅ Размер изображения изменён на: Пейзаж (1920x1080)")
        await query.answer()

    elif action == "setting_reset":
        # Сброс настроек на значения по умолчанию
        user_settings[user_id] = {
            "model": "flux",
            "width": 1080,
            "height": 1920
        }
        await query.message.edit_text("✅ Настройки сброшены на значения по умолчанию.")
        await query.answer()

# Новый обработчик для выбора модели
@dp.callback_query(lambda query: query.data.startswith("model_"))
async def handle_model_selection(callback: CallbackQuery):
    user_id = callback.from_user.id
    selected_model = callback.data.split("_", 1)[1]
    
    # Проверяем, доступна ли модель
    available_models = await get_available_models()
    if selected_model not in available_models:
        await callback.answer("❌ Модель недоступна", show_alert=True)
        return
    
    # Обновляем настройки пользователя
    user_settings[user_id]["model"] = selected_model
    save_users()
    
    # Подтверждение
    await callback.message.edit_text(f"✅ Модель изменена на: {selected_model}")
    await callback.answer()


# Функция для получения настроек пользователя
def get_user_settings(user_id):
    if user_id not in user_settings:
        user_settings[user_id] = {
            "model": "flux",
            "width": 1080,
            "height": 1920
        }
    return user_settings[user_id]

##################################################
########### Блок генерации изображений ###########

# Обработчик текстовых сообщений для генерации изображения
@dp.message(lambda message: message.text and user_states.get(message.from_user.id) == "waiting_for_image_description")
async def handle_image_description(message: Message):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)

    prompt = message.text.strip()
    if not prompt:
        await message.answer("❌ Пожалуйста, укажите описание изображения.")
        return

    # Проверяем, требует ли модель токена
    if settings["model"] == "gptimage" and not config.GPTIMAGE_API_TOKEN:
        await message.answer(
            "🔒 Модель 'gptimage' требует токен. Получите его по ссылке:\n"
            "https://github.com/pollinations/pollinations/issues/new?template=special-bee-request.yml "
        )
        return

    # Добавляем запрос в историю с оригинальным промптом
    user_history[user_id].append({
        "type": "image",
        "prompt": prompt,
        "translated_prompt": translated_prompt,
        "model": settings["model"],
        "width": settings["width"],
        "height": settings["height"],
        "timestamp": datetime.now().isoformat()
    })
    save_users()
    image_requests[user_id].append(prompt)

    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    
    try:
        # Получаем параметры генерации
        width = settings["width"]
        height = settings["height"]
        seed = random.randint(10, 99999999)
        model = settings["model"]

        # Переводим промпт на английский
        translated_prompt = await translate_to_english(prompt)
        logging.info(f"Перевод выполнен: {prompt} -> {translated_prompt}")

        # Формируем URL с переведенным промптом
        encoded_prompt = urllib.parse.quote(translated_prompt)  # ❌ Было: prompt
        params = {
            "width": width,
            "height": height,
            "seed": seed,
            "model": model,
            "nologo": "true"
        }
        
        if model == "gptimage":
            headers = {"Authorization": f"Bearer {config.GPTIMAGE_API_TOKEN}"}
        else:
            headers = {}
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?{urllib.parse.urlencode(params)}"
        
        logging.info(f"Генерация изображения: {image_url}")

        # Загружаем изображение
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=300) as response:
                if response.status == 200:
                    image_data = await response.read()
                else:
                    logging.error(f"Ошибка загрузки изображения: {response.status} - {await response.text()}")
                    await message.answer("⚠️ Ошибка: не удалось получить изображение.")
                    return
        
        # Создаем объект BufferedInputFile из данных изображения
        input_file = BufferedInputFile(image_data, filename='image.jpg')
        
        # Отправляем изображение с оригинальным описанием
        sent_message = await message.answer_photo(
            photo=input_file,
            caption=f"🖼 Результат для: '{prompt}'\nМодель: {model}, Размер: {width}x{height}, Seed: {seed}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄", callback_data=f"regenerate:{user_id}"),
                    InlineKeyboardButton(text="✅", callback_data=f"accept:{user_id}")
                ]
            ])
        )
        
        # Сохраняем оба промпта в last_image_requests
        last_image_requests[user_id] = {
            "prompt": prompt,
            "translated_prompt": translated_prompt,  # Новое поле
            "model": model,
            "width": width,
            "height": height
        }

        # Сбрасываем состояние
        user_states[user_id] = None
        image_requests[user_id] = []  # Очищаем историю запросов на изображение

    except Exception as e:
        logging.error(f"Ошибка генерации изображения: {str(e)}")
        user_states[user_id] = None  # Сбрасываем состояние при ошибке
        await message.answer(f"⚠️ Произошла ошибка при генерации: {str(e)}")

# Функция загрузки изображения с повторными попытками
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def download_image_with_retry(image_url):
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, timeout=300) as response:
            if response.status == 200:
                return await response.read()
            error_text = await response.text()
            logging.error(f"Pollinations API ошибка: {response.status} - {error_text}")
            raise Exception(f"Ошибка генерации: {error_text}")

# Обработчик для перегенерации изображения
@dp.callback_query(lambda query: query.data.startswith("regenerate:"))
async def handle_regenerate(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    
    if user_id not in last_image_requests:
        await callback.answer("❌ Ошибка: нет данных для перегенерации", show_alert=True)
        return

    # Извлекаем параметры из last_image_requests
    request_data = last_image_requests[user_id]
    original_prompt = request_data["prompt"]
    translated_prompt = request_data.get("translated_prompt", await translate_to_english(original_prompt))  # Повторный перевод при необходимости
    model = request_data["model"]
    width = request_data["width"]
    height = request_data["height"]

    # Генерируем новое значение seed
    new_seed = random.randint(10, 99999999)

    # Формируем новый URL с переведенным промптом
    encoded_prompt = urllib.parse.quote(translated_prompt)  # ❌ Было: original_prompt
    params = {
        "width": width,
        "height": height,
        "seed": new_seed,
        "model": model,
        "nologo": "true"
    }
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?{urllib.parse.urlencode(params)}"
    
    logging.info(f"Перегенерация изображения: {image_url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=300) as response:
                if response.status == 200:
                    image_data = await response.read()
                else:
                    logging.error(f"Ошибка загрузки изображения: {response.status} - {await response.text()}")
                    await callback.answer("⚠️ Ошибка: не удалось получить изображение.", show_alert=True)
                    return
        
        # Создаем объект BufferedInputFile из данных изображения
        input_file = BufferedInputFile(image_data, filename='image.jpg')
        
        # Убираем кнопки из предыдущего сообщения
        await callback.message.edit_reply_markup(reply_markup=None)

        # Отправляем новое изображение с оригинальным описанием
        await callback.message.answer_photo(
            photo=input_file,
            caption=f"🖼 Перегенерированное изображение для: '{original_prompt}'\nМодель: {model}, Размер: {width}x{height}, Seed: {new_seed}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄", callback_data=f"regenerate:{user_id}"),
                    InlineKeyboardButton(text="✅", callback_data=f"accept:{user_id}")
                ]
            ])
        )
        
        await callback.answer("✅ Изображение обновлено!")

        # Обновляем last_image_requests с новым переведенным промптом
        last_image_requests[user_id]["translated_prompt"] = await translate_to_english(original_prompt)
        
    except Exception as e:
        logging.error(f"Ошибка при перегенерации: {str(e)}")
        await callback.answer("⚠️ Ошибка при перегенерации", show_alert=True)

# Обработчик для кнопки "Готово"
@dp.callback_query(lambda query: query.data.startswith("accept:"))
async def handle_accept(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    
    if user_id in last_image_requests:
        del last_image_requests[user_id]
        await callback.answer("✅ Запрос принят, кнопки убраны.")
        await callback.message.edit_reply_markup(reply_markup=None)
    else:
        await callback.answer("❌ Ошибка: нет данных для принятия", show_alert=True)


##################################################
########### Блок анализа изображений ###########

# Функция для получения настроек анализа
def get_user_analysis_settings(user_id):
    if user_id not in user_analysis_settings:
        user_analysis_settings[user_id] = {"quality": "high"}
    return user_analysis_settings[user_id]

# Обработчик команды /analyze
@dp.message(Command("analyze"))
async def cmd_analyze(message: Message):
    user_id = message.from_user.id
    await message.answer("🖼 Пожалуйста, отправьте изображение для анализа.")
    user_analysis_states[user_id] = "waiting_for_image_analysis"

# Обработчик команды /analysissettings
@dp.message(Command("analysissettings"))
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
@dp.callback_query(lambda query: query.data.startswith("quality_"))
async def handle_analysis_quality(callback: CallbackQuery):
    user_id = callback.from_user.id
    quality = callback.data.split("_")[1]
    
    if user_id not in user_analysis_settings:
        user_analysis_settings[user_id] = {}
    user_analysis_settings[user_id]["quality"] = quality
    
    await callback.message.edit_text(f"✅ Качество анализа установлено: {quality}")
    await callback.answer()

# Обработчик возврата к основному меню
@dp.callback_query(lambda query: query.data == "analysis_settings_back")
async def handle_analysis_settings_back(callback: CallbackQuery):
    await callback.message.edit_text("⚙️ Выберите настройку:")
    # ... (ваше текущее меню настроек)

# Обработчик изображений для анализа
@dp.message(lambda message: message.photo or (message.document and message.document.mime_type.startswith('image/')))
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
            async with session.post("https://text.pollinations.ai/openai", json=payload, timeout=300) as response:
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
@dp.message(lambda message: message.photo or (message.document and message.document.mime_type.startswith('image/')))
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
@dp.callback_query(lambda query: query.data == "suggest_analyze")
async def handle_suggest_analyze(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, я могу проанализировать это изображение. Для этого используйте команду `/analyze`.")
    await callback.answer()

# Обработчик нажатия на кнопку "Сгенерировать"
@dp.callback_query(lambda query: query.data == "suggest_generate")
async def handle_suggest_generate(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, вы можете сгенерировать новое изображение. Для этого используйте команду `/image`.")
    await callback.answer()


###########################################################
####### Обработчик генерации аудиофайла Polinations #######
# Блок генерации аудио из текста
@dp.message(Command("generateaudio"))
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

# Основной обработчик команды /tts
@dp.message(Command("tts"))
async def cmd_tts(message: Message):
    user_id = message.from_user.id
    if not message.reply_to_message or not message.reply_to_message.text:
        await message.answer("❌ Ответьте на текстовое сообщение командой `/tts`")
        return
    
    # Сохраняем текст в состоянии
    user_states[user_id] = {
        "action": "tts",
        "text": message.reply_to_message.text
    }
    
    # Отправляем клавиатуру выбора голоса
    await message.answer("🎙️ Выберите голос для озвучивания:", reply_markup=voice_selection_keyboard())

def voice_selection_keyboard():
    """Клавиатура для выбора голоса"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=voice, callback_data=f"tts_voice_{voice}") for voice in config.SUPPORTED_VOICES],
        [InlineKeyboardButton(text="↩️ По умолчанию", callback_data="tts_voice_default")]
    ])

@dp.callback_query(lambda query: query.data.startswith("tts_voice_"))
async def handle_tts_voice(callback: CallbackQuery):
    user_id = callback.from_user.id
    state = user_states.get(user_id)
    
    if not state or state["action"] != "tts":
        await callback.answer("❌ Нет активного запроса TTS")
        return
    
    voice = callback.data.split("_")[2]
    if voice == "default":
        voice = config.DEFAULT_VOICE
    
    text = state["text"]
    await callback.message.edit_text(f"🔄 Генерация аудио с голосом: {voice}")
    
    try:
        # Определяем метод (GET или POST)
        if len(text) > 4096:
            audio_binary = await generate_audio_post(text, voice)
        else:
            audio_binary = await generate_audio_get(text, voice)
        
        input_file = BufferedInputFile(audio_binary, filename='tts.mp3')
        await callback.message.answer_audio(input_file, caption=f"🎙️ Аудио сгенерировано с голосом: {voice}")
        await callback.message.delete()
    
    except Exception as e:
        logging.error(f"Ошибка генерации TTS: {str(e)}")
        await callback.message.answer(f"⚠️ Ошибка: {str(e)}")
    
    finally:
        user_states.pop(user_id, None)

@dp.callback_query(lambda query: query.data.startswith("voice_"))
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

# Функция для генерации аудио через GET (короткие тексты)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def generate_audio_get(text, voice):
    """Генерация аудио через GET-запрос"""
    encoded_text = urllib.parse.quote(text)
    url = f"https://text.pollinations.ai/ {encoded_text}"
    params = {
        "model": config.TTS_MODEL,
        "voice": voice
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=300) as response:
            if response.status == 200 and 'audio/mpeg' in response.headers.get('Content-Type', ''):
                return await response.read()
            error_text = await response.text()
            raise Exception(f"Ошибка TTS (GET): {response.status} - {error_text}")

# Функция для генерации аудио через POST (длинные тексты)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def generate_audio_post(text, voice):
    """Генерация аудио через POST-запрос"""
    payload = {
        "model": config.TTS_MODEL,
        "messages": [{"role": "user", "content": text}],
        "voice": voice
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post("https://text.pollinations.ai/openai ", json=payload, timeout=300) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Ошибка TTS (POST): {response.status} - {error_text}")
            
            result = await response.json()
            try:
                return base64.b64decode(result['choices'][0]['message']['audio']['data'])
            except (KeyError, IndexError, base64.binascii.Error) as e:
                raise Exception(f"Ошибка обработки ответа: {str(e)}")

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

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
async def generate_audio_with_retry(payload, method="POST"):
    """Генерация аудио с повторными попытками"""
    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(payload["url"], timeout=300) as response:
                if response.status == 200:
                    return await response.read()
                error_text = await response.text()
                raise Exception(f"Ошибка API: {error_text}")
        else:
            async with session.post("https://text.pollinations.ai/openai", json=payload, timeout=300) as response:
                if response.status == 200:
                    return await response.json()
                error_text = await response.text()
                raise Exception(f"Ошибка API: {error_text}")


###########################################################
##### Обработчик транскрибации аудиофайла Polinations ##### 

# Обработчик команды /transcribe
@dp.message(Command("transcribe"))
async def cmd_transcribe(message: Message):
    user_id = message.from_user.id
    await message.answer("🎤 Пожалуйста, отправьте аудиофайл для распознавания.")
    user_transcribe_states[user_id] = "waiting_for_audio_transcribe"

# Обработчик аудиофайлов для транскрибации
@dp.message(lambda message: message.audio or message.voice or message.document and message.document.mime_type.startswith('audio/'))
async def handle_audio_transcribe(message: Message):
    user_id = message.from_user.id
    
    # Проверяем, ожидаем ли мы аудиофайл для транскрибации
    if user_transcribe_states.get(user_id) != "waiting_for_audio_transcribe":
        return  # Игнорируем, если не запрашивали транскрибацию
    
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    
    try:
        # Получаем файл
        audio_file = message.audio or message.voice or message.document
        file_info = await bot.get_file(audio_file.file_id)
        file_path = file_info.file_path
        
        # Создаём временную директорию
        temp_dir = tempfile.gettempdir()
        temp_input_path = os.path.join(temp_dir, f"{file_info.file_id}.{file_path.split('.')[-1]}")
        
        # Скачиваем файл
        await bot.download_file(file_path, temp_input_path)
        
        # Проверка формата
        file_extension = temp_input_path.split('.')[-1].lower()
        if file_extension not in config.SUPPORTED_AUDIO_FORMATS:
            await message.answer(f"❌ Формат {file_extension} не поддерживается. Поддерживаются: {', '.join(config.SUPPORTED_AUDIO_FORMATS)}")
            return
        
        # Проверка размера
        file_size = os.path.getsize(temp_input_path)
        if file_size > config.MAX_AUDIO_SIZE:
            await message.answer("⏳ Файл слишком большой. Попробую сжать...")
            
            # Конвертируем в MP3
            mp3_path = os.path.join(temp_dir, f"{file_info.file_id}.mp3")
            
            if not convert_to_mp3(temp_input_path, mp3_path):
                await message.answer("❌ Не удалось конвертировать файл в MP3")
                return
            
            # Проверяем размер после конвертации
            mp3_size = os.path.getsize(mp3_path)
            if mp3_size > config.MAX_AUDIO_SIZE:
                await message.answer("⏳ Файл всё ещё слишком большой. Разбиваю на части...")
                
                # Разбиваем на части
                chunks = split_audio(mp3_path)
                if not chunks:
                    await message.answer("❌ Не удалось разбить аудиофайл на части")
                    return
                
                full_transcription = ""
                progress_msg = await message.answer("🔄 Обработка частей файла:")
                
                for i, chunk_path in enumerate(chunks):
                    # Обновляем статус прогресса
                    await progress_msg.edit_text(f"🔄 Обработка части {i+1}/{len(chunks)}")
                    
                    # Кодируем часть в base64
                    with open(chunk_path, "rb") as f:
                        encoded_audio = base64.b64encode(f.read()).decode('utf-8')
                    
                    # Формируем запрос к API
                    payload = {
                        "model": config.TRANSCRIBE_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "Пожалуйста, распознайте речь из этой части файла:"},
                                    {
                                        "type": "input_audio",
                                        "input_audio": {
                                            "data": encoded_audio,
                                            "format": "mp3"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                    
                    try:
                        result = await transcribe_with_retry(payload)
                        transcription = result['choices'][0]['message']['content']
                        full_transcription += f"Часть {i+1}:\n{transcription}\n\n"
                    except Exception as e:
                        logging.error(f"Ошибка при транскрибации части {i+1}: {str(e)}")
                        full_transcription += f"Часть {i+1}: ОШИБКА - {str(e)}\n\n"
                
                # Удаляем сообщение прогресса
                await progress_msg.delete()
                
                # Сохраняем в историю
                user_entry = {
                    "type": "transcribe",
                    "prompt": "Распознайте речь из этого аудиофайла (разбит на части)",
                    "timestamp": datetime.now().isoformat()
                }
                user_history.setdefault(user_id, []).append(user_entry)
                
                assistant_entry = {
                    "type": "transcribe",
                    "response": full_transcription,
                    "timestamp": datetime.now().isoformat()
                }
                user_history[user_id].append(assistant_entry)
                save_users()
                
                # Отправляем результат
                await message.answer(f"🎤 Результат транскрибации (файл разбит на части):\n\n{full_transcription}")
                return
        
        else:
            # Файл нормального размера - используем напрямую
            with open(temp_input_path, "rb") as f:
                encoded_audio = base64.b64encode(f.read()).decode('utf-8')
        
        # Формируем запрос к API
        payload = {
            "model": config.TRANSCRIBE_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Пожалуйста, распознайте речь из этого аудиофайла:"},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": encoded_audio,
                                "format": file_extension
                            }
                        }
                    ]
                }
            ]
        }
        
        logging.info(f"Транскрибация аудио от {user_id}")
        
        # Отправляем запрос
        result = await transcribe_with_retry(payload)
        transcription = result['choices'][0]['message']['content']
        
        # Сохраняем в историю
        user_entry = {
            "type": "transcribe",
            "prompt": "Распознайте речь из этого аудиофайла",
            "timestamp": datetime.now().isoformat()
        }
        user_history.setdefault(user_id, []).append(user_entry)
        
        assistant_entry = {
            "type": "transcribe",
            "response": transcription,
            "timestamp": datetime.now().isoformat()
        }
        user_history[user_id].append(assistant_entry)
        save_users()
        
        # Отправляем результат
        await message.answer(f"🎤 Результат транскрибации:\n\n{transcription}")
        
    except Exception as e:
        logging.error(f"Ошибка транскрибации: {str(e)}")
        await message.answer(
            "⚠️ Ошибка при транскрибации:\n"
            "1. Проверьте, что файл не превышает 512 MB\n"
            "2. Попробуйте использовать формат MP3\n"
            "3. Для длинных записей используйте более короткие фрагменты"
        )
    
    finally:
        # Очищаем временные файлы
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.startswith(f"{file_info.file_id}."):
                    try:
                        os.remove(os.path.join(root, file))
                    except:
                        pass
        
        # Сбрасываем состояние
        user_transcribe_states[user_id] = None

###########################################################
########### Обработчик транскрибации аудиофайла ########### 

# Функция для получения существующих задач
async def get_existing_jobs(api_key):
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get("https://asr.api.speechmatics.com/v2/jobs/", headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            return None

# Функция для получения транскрипции по job_id
async def get_transcript(job_id, api_key):
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://asr.api.speechmatics.com/v2/jobs/{job_id}/transcript?format=txt", headers=headers)
        if response.status_code == 200:
            return response.text
        else:
            return None

# Обработчик аудиофайлов
async def handle_audio_file(message: Message):
    user_id = message.from_user.id
    audio_file = message.audio or message.voice or message.document

    # Проверяем формат файла
    if audio_file.mime_type not in ["audio/aac", "audio/amr", "audio/flac", "audio/m4a", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav"]:
        await message.answer("❌ Неподдерживаемый формат файла. Пожалуйста, отправьте файл в одном из поддерживаемых форматов: aac, amr, flac, m4a, mp3, mp4, mpeg, ogg, wav, объемом до 512Mb.")
        return

    # Получаем информацию о файле
    file_info = await bot.get_file(audio_file.file_id)

    # Загружаем файл в папку temp
    file_path = os.path.join('temp', f"{audio_file.file_id}.audio")
    await bot.download_file(file_info.file_path, destination=file_path)

    # Проверяем, был ли файл успешно загружен
    if not os.path.exists(file_path):
        await message.answer("❌ Ошибка: файл не был загружен. Пожалуйста, попробуйте еще раз.")
        return

    # Удаляем сообщение с просьбой отправить аудиофайл
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id - 1)

    # Уведомление о начале проверки существующих задач
    checking_message = await message.answer("🔄 Проверяем существующие задачи, пожалуйста, подождите...", disable_notification=True)

    # Получаем существующие задачи
    existing_jobs = await get_existing_jobs(SPEECHMATICS_API)

    if existing_jobs:
        for job in existing_jobs.get('jobs', []):
            if job['data_name'] == os.path.basename(file_path):  # Сравниваем имя файла
                # Если задача найдена, получаем результаты
                await bot.delete_message(chat_id=message.chat.id, message_id=checking_message.message_id)  # Удаляем сообщение о проверке
                await message.answer("✅ Задача уже существует. Получаем результаты...", disable_notification=True)
                transcript = await get_transcript(job['id'], SPEECHMATICS_API)  # Получаем транскрипцию
                if transcript:
                    # Создаем файл в папке temp
                    temp_file_path = os.path.join('temp', f"{job['id']}.txt")
                    with open(temp_file_path, 'w', encoding='utf-8') as temp_file:
                        temp_file.write(transcript)  # Записываем текст в файл

                    # Отправляем файл пользователю с использованием FSInputFile
                    audio_file = FSInputFile(temp_file_path)
                    await bot.send_document(user_id, audio_file, disable_notification=True)
                else:
                    await bot.delete_message(chat_id=message.chat.id, message_id=checking_message.message_id)  # Удаляем сообщение о результатах
                    await message.answer("⚠️ Не удалось получить результаты транскрипции.", disable_notification=True)
                return

    # Уведомление о начале обработки аудиофайла
    await bot.delete_message(chat_id=message.chat.id, message_id=checking_message.message_id)  # Удаляем сообщение о проверке
    processing_message = await message.answer("🔄 Обрабатываем аудиофайл, пожалуйста, подождите...", disable_notification=True)

    # Отправляем файл на преобразование
    try:
        with BatchClient(SPEECHMATICS_API) as client:
            job_id = client.submit_job(file_path, BatchTranscriptionConfig(TRANSCRIPTION_LANGUAGE))
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_message.message_id)  # Удаляем сообщение о обработке
            await message.answer("✅ Задача отправлена на распознавание. Ожидайте результатов...", disable_notification=True)

            # Запускаем фоновую задачу для проверки статуса
            asyncio.create_task(check_job_status(job_id, user_id))

    except Exception as e:
        await bot.delete_message(chat_id=message.chat.id, message_id=processing_message.message_id)  # Удаляем сообщение о обработке
        await message.answer(f"⚠️ Произошла ошибка: {str(e)}", disable_notification=True)

# Фоновая задача для проверки статуса
async def check_job_status(job_id, user_id):
    while True:
        await asyncio.sleep(15)  # Проверяем статус каждые 15 секунд

        # Получаем статус задачи
        transcript = await get_transcript(job_id, SPEECHMATICS_API)
        if transcript:
            # Создаем файл в папке temp
            try:
                temp_file_path = os.path.join('temp', f"{job_id}.txt")
                with open(temp_file_path, 'w', encoding='utf-8') as temp_file:
                    temp_file.write(transcript)  # Записываем текст в файл

                # Отправляем файл пользователю с использованием FSInputFile
                audio_file = FSInputFile(temp_file_path)
                sent_message = await bot.send_document(user_id, audio_file, disable_notification=True)
                await bot.delete_message(chat_id=user_id, message_id=sent_message.message_id - 1)  # Удаляем предыдущее сообщение со статусом
            except Exception as e:
                await bot.send_message(user_id, f"⚠️ Ошибка при создании файла: {str(e)}", disable_notification=True)
            break
        else:
            # Если задача еще не завершена, продолжаем проверять
            continue

# Обработчик существующих задач
async def handle_existing_jobs(user_id, file_path):
    existing_jobs = await get_existing_jobs(SPEECHMATICS_API)

    if existing_jobs:
        for job in existing_jobs.get('jobs', []):
            if job['data_name'] == os.path.basename(file_path):  # Сравниваем имя файла
                # Если задача найдена, получаем результаты
                sent_message = await bot.send_message(user_id, "✅ Задача уже существует. Получаем результаты...", disable_notification=True)
                transcript = await get_transcript(job['id'], SPEECHMATICS_API)  # Получаем транскрипцию

                # Создаем файл в папке temp
                try:
                    temp_file_path = os.path.join('temp', f"{job['id']}.txt")
                    with open(temp_file_path, 'w', encoding='utf-8') as temp_file:
                        temp_file.write(transcript)  # Записываем текст в файл

                    # Отправляем файл пользователю с использованием FSInputFile
                    audio_file = FSInputFile(temp_file_path)
                    await bot.send_document(user_id, audio_file, disable_notification=True)
                    await bot.delete_message(chat_id=user_id, message_id=sent_message.message_id)  # Удаляем сообщение со статусом
                except Exception as e:
                    await bot.send_message(user_id, f"⚠️ Ошибка при создании файла: {str(e)}", disable_notification=True)
                break

#####################################################
########### Обработчик первода сообщений ############
@dp.message(Command("translatetoeng"))
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

@dp.message(Command("translatetoru"))
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

#####################################################
# Фильтр для проверки состояния ожидания аудиофайла #
async def is_waiting_for_audio_file(message: Message):
    return user_states.get(message.from_user.id) == "waiting_for_audio_file" and \
           (message.content_type == 'audio' or message.content_type == 'voice' or message.content_type == 'document')

# Регистрация фильтра
dp.message(is_waiting_for_audio_file)(handle_audio_file)

################################################
########### Блок текстовых сообщений ###########

# Обработчик текстовых сообщений для ответов на сообщения администраторов
@dp.message(lambda message: message.reply_to_message and is_admin(message.reply_to_message.from_user.id))
async def handle_admin_reply(message: Message):
    admin_id = message.reply_to_message.from_user.id
    user_id = message.from_user.id
    user_message = message.text

    # Отправляем сообщение администратору
    await bot.send_message(admin_id, f"👤 Ответ от пользователя {user_id}:\n\n{user_message}")

    # Уведомление пользователю о том, что его сообщение отправлено
    await message.answer("✅ Ваш ответ отправлен администратору.")

# Обработчик текстовых сообщений для общения с ИИ
@dp.message(lambda message: message.text is not None and not (message.reply_to_message and is_admin(message.reply_to_message.from_user.id)))
async def handle_message(message: Message):
    global current_provider
    user_id = message.from_user.id
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
        
        provider_class = getattr(g4f.Provider, current_provider)
        response = await g4f.ChatCompletion.create_async(
            model=g4f.models.default,
            messages=api_messages,  # Используем отфильтрованные сообщения
            provider=provider_class(),
            api_key=API_DeepSeek
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
@dp.message(lambda message: message.audio or message.voice or message.document and message.document.mime_type.startswith('audio/'))
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
@dp.callback_query(lambda query: query.data == "suggest_transcribe")
async def handle_suggest_transcribe(callback: CallbackQuery):
    await callback.message.edit_text("Хорошо, я могу распознать речь в этом аудиофайле. Для этого используйте команду `/transcribe`.")
    await callback.answer()

# Обработчик для изображений и других медиафайлов
@dp.message(lambda message: message.content_type in ['photo', 'document'])
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

######################################################
########### Функция повторного подключения ###########

async def wait_for_telegram_api():
    while True:
        if await check_telegram_api_availability():
            logging.info("Telegram API доступен")
            return
        logging.error("Telegram API недоступен. Повтор через 10 сек...")
        await asyncio.sleep(10)

async def auto_save_task():
    while True:
        await asyncio.sleep(300)  # Сохраняем каждые 5 минут
        save_users()
        logging.info("Данные пользователей автоматически сохранены")

# Запуск бота
async def main():
    # Запускаем задачу автосохранения
    asyncio.create_task(auto_save_task())
    await wait_for_telegram_api()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        # Загружаем данные о пользователях при старте
        load_users()
        # Запускаем бота
        asyncio.run(main())
        
    except KeyboardInterrupt:
        # Обработка завершения работы (Ctrl+C)
        print("\nБот завершает работу...")
        
    except Exception as e:
        # Обработка других исключений
        logging.error(f"Ошибка: {str(e)}")
        
    finally:
        # Сохраняем данные о пользователях перед завершением
        save_users()
        print("Данные пользователей сохранены.")