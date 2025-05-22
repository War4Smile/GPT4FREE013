# services/audio_transcribeapi.py

import os
import asyncio
import httpx
import config
import pollinations as ai
from aiogram import F, Router, types
from services.tgapi import bot
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, CallbackQuery, TelegramObject
from aiogram.enums import ParseMode, ChatAction
from alworkproviders import AVAILABLE_PROVIDERS
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states)
from speechmatics.batch_client import BatchClient
from speechmatics.models import BatchTranscriptionConfig

router = Router()

###########################################################
########### Обработчик транскрибации аудиофайла ########### 

async def is_waiting_for_audio_file(message: Message):
    return user_states.get(message.from_user.id) == "waiting_for_audio_file" and \
           message.content_type in ['audio', 'voice', 'document']

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
    existing_jobs = await get_existing_jobs(config.SPEECHMATICS_API)

    if existing_jobs:
        for job in existing_jobs.get('jobs', []):
            if job['data_name'] == os.path.basename(file_path):  # Сравниваем имя файла
                # Если задача найдена, получаем результаты
                await bot.delete_message(chat_id=message.chat.id, message_id=checking_message.message_id)  # Удаляем сообщение о проверке
                await message.answer("✅ Задача уже существует. Получаем результаты...", disable_notification=True)
                transcript = await get_transcript(job['id'], config.SPEECHMATICS_API)  # Получаем транскрипцию
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
        with BatchClient(config.SPEECHMATICS_API) as client:
            job_id = client.submit_job(file_path, BatchTranscriptionConfig(config.TRANSCRIPTION_LANGUAGE))
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
        transcript = await get_transcript(job_id, config.SPEECHMATICS_API)
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
    existing_jobs = await get_existing_jobs(config.SPEECHMATICS_API)

    if existing_jobs:
        for job in existing_jobs.get('jobs', []):
            if job['data_name'] == os.path.basename(file_path):  # Сравниваем имя файла
                # Если задача найдена, получаем результаты
                sent_message = await bot.send_message(user_id, "✅ Задача уже существует. Получаем результаты...", disable_notification=True)
                transcript = await get_transcript(job['id'], config.SPEECHMATICS_API)  # Получаем транскрипцию

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