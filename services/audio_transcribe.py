# services/audio_transcribe.py
import os
import base64
import logging
import tempfile
import config
from datetime import datetime
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from utils.helpers import is_admin, save_users
from services.tgapi import bot
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states)
from utils.helpers import ( get_user_settings, convert_to_mp3, split_audio,
                            encode_audio_base64, remove_html_tags,
                            auto_detect_language, format_response)
from services.retry import (transcribe_with_retry, download_image_with_retry,
                            generate_audio_with_retry)

router = Router()

class AudioState(StatesGroup):
    waiting_for_audio = State()

###########################################################
##### Обработчик транскрибации аудиофайла Polinations ##### 

# Обработчик команды /transcribe
@router.message(Command("transcribe"))
async def cmd_transcribe(message: Message):
    user_id = message.from_user.id
    await message.answer("🎤 Пожалуйста, отправьте аудиофайл для распознавания.")
    user_transcribe_states[user_id] = "waiting_for_audio_transcribe"

# Обработчик аудиофайлов для транскрибации
@router.message(lambda message: message.audio or message.voice or message.document and message.document.mime_type.startswith('audio/'))
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


#####################################################
# Фильтр для проверки состояния ожидания аудиофайла #
async def is_waiting_for_audio_file(message: Message):
    return user_states.get(message.from_user.id) == "waiting_for_audio_file" and \
           (message.content_type == 'audio' or message.content_type == 'voice' or message.content_type == 'document')

