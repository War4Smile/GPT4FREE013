# services/image_gen.py
import aiohttp
import random
import logging
import urllib.parse
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from utils.helpers import get_user_settings, save_users
from database import (
                        save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states )
from utils.helpers import get_user_settings, translate_to_english
from services.tgapi import bot

router = Router()

logger = logging.getLogger(__name__)


####################################################
########### Выбора провайдера и настроек ###########

# Обработчик команды /imagesettings
@router.message(Command("imagesettings"))
async def cmd_imagesettings(message: Message):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)

    # Создаем инлайн-клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Модель: {settings['model']}", callback_data="setting_model")],
        [
            InlineKeyboardButton(text="Квадрат (1:1)", callback_data="setting_size_square"),
            InlineKeyboardButton(text="Портрет (1:2)", callback_data="setting_size_portrait"),
        ],
        [
            InlineKeyboardButton(text="Пейзаж (2:1)", callback_data="setting_size_landscape"),
            InlineKeyboardButton(text="Сбросить настройки", callback_data="setting_reset"),
        ]
    ])

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
@router.callback_query(lambda query: query.data.startswith("setting_"))
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
@router.callback_query(lambda query: query.data.startswith("model_"))
async def handle_model_selection(query: CallbackQuery):
    user_id = query.from_user.id
    settings = get_user_settings(user_id)
    model_name = query.data.split("_", 1)[1]
    settings["model"] = model_name
    await query.message.edit_text(f"✅ Модель изменена на: {model_name}")
    await query.answer()


# Функция для получения настроек пользователя
def get_user_settings(user_id):
    if user_id not in user_settings:
        user_settings[user_id] = {
            "model": "flux",
            "width": 1080,
            "height": 1920
        }
    return user_settings[user_id]

class ImageState(StatesGroup):
    description = State()

##################################################
########### Блок генерации изображений ###########
# Обработчик текстовых сообщений для генерации изображения
@router.message(lambda message: message.text and user_states.get(message.from_user.id) == "waiting_for_image_description")
async def handle_image_description(message: Message):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)

    prompt = message.text.strip()
    if not prompt:
        await message.answer("❌ Пожалуйста, укажите описание изображения.")
        return

    # Добавляем запрос в историю с оригинальным промптом
    user_history[user_id].append({
        "type": "image",
        "prompt": prompt,
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


# Обработчик для перегенерации изображения
@router.callback_query(lambda query: query.data.startswith("regenerate:"))
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
@router.callback_query(lambda query: query.data.startswith("accept:"))
async def handle_accept(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    
    if user_id in last_image_requests:
        del last_image_requests[user_id]
        await callback.answer("✅ Запрос принят, кнопки убраны.")
        await callback.message.edit_reply_markup(reply_markup=None)
    else:
        await callback.answer("❌ Ошибка: нет данных для принятия", show_alert=True)

async def generate_image(prompt: str, user_id: int) -> bytes:
    settings = get_user_settings(user_id)
    params = {
        "private": False,
        "enhance": True,
        "safe": True,
        "referrer": "MyTestBot",
        "nologo": True,
        "width": settings["width"],
        "height": settings["height"]
    }
    encoded_prompt = urllib.parse.quote(prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?{urllib.parse.urlencode(params)}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url, timeout=300) as response:
            if response.status == 200:
                return await response.read()
            else:
                error_text = await response.text()
                logger.error(f"Ошибка генерации изображения: {response.status} - {error_text}")
                return None