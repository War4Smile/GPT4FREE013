import re
import g4f
import logging
import config
from aiogram import F, Router
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from providers.fully_working import AVAILABLE_PROVIDERS
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential
from langdetect import detect
from utils.helpers import auto_detect_language, get_user_settings, save_users
from database import (  save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests, used_questions,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states, user_quiz_data, group_quiz_data )
from utils.helpers import get_user_settings, translate_to_english
from services.tgapi import bot

router = Router()

logger = logging.getLogger(__name__)

@router.message(Command("next"))
async def cmd_next(message: Message):
    chat_type = message.chat.type
    
    if chat_type == "private":
        user_id = message.from_user.id
        if user_id not in user_quiz_data:
            return
        await ask_next_question(message, user_id)
    else:
        chat_id = message.chat.id
        if chat_id not in group_quiz_data:
            return
        quiz_data = group_quiz_data[chat_id]
        quiz_data["current_question"] += 1
        await send_group_question(message, chat_id)

@router.message(Command("quizprovider"))
async def cmd_quiz_provider(message: Message):
    user_id = message.from_user.id
    current = config.DEFAULT_QVIZ_PROVIDER  # Текущий провайдер
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🔄 {provider}{' ✅' if provider == current else ''}", 
            callback_data=f"quiz_provider_{provider}"
        )] 
        for provider in AVAILABLE_PROVIDERS
    ])
    await message.answer("Выберите провайдера для викторин:", reply_markup=keyboard)

@router.callback_query(lambda query: query.data == "finish_quiz")
async def finish_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    quiz_data = user_quiz_data.get(user_id)
    
    if not quiz_data:
        await callback.message.answer("❌ Викторина не начата.")
        return
    
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logging.warning(f"Не удалось удалить кнопки: {str(e)}")
    
    total_questions = quiz_data["current_question"]
    score = quiz_data["score"]
    
    rating = "Новичок"
    if score >= total_questions * 0.8:
        rating = "Мастер"
    elif score >= total_questions * 0.6:
        rating = "Эксперт"
    elif score >= total_questions * 0.4:
        rating = "Знаток"
    
    result_text = f"🛑 Викторина завершена досрочно!\nПравильных: {score}/{total_questions}\nВаш уровень: {rating}"
    await callback.message.answer(result_text)
    
    user_quiz_data.pop(user_id, None)

@router.callback_query(lambda query: query.data.startswith("quiz_provider_"))
async def handle_quiz_provider_selection(query: CallbackQuery):
    provider_name = query.data.split("_", 2)[2]  # Извлекаем имя провайдера
    config.DEFAULT_QVIZ_PROVIDER = provider_name  # Обновляем глобальную константу
    await query.message.edit_text(f"✅ Провайдер для викторин изменён на: {provider_name}")
    await query.answer()

def get_unique_question(user_id: int, questions: list) -> list:
    """Возвращает только уникальные вопросы для пользователя"""
    if user_id not in used_questions:
        used_questions[user_id] = set()
    
    unique = []
    for q in questions:
        question_hash = hash(q["question"])
        if question_hash not in used_questions[user_id]:
            unique.append(q)
            used_questions[user_id].add(question_hash)
    
    return unique

async def send_group_question(message: Message, chat_id: int):
    """Отправляет вопрос с инлайн-клавиатурой всем участникам"""
    quiz_data = group_quiz_data[chat_id]
    
    if quiz_data["current_question"] >= len(quiz_data["questions"]):
        await show_group_quiz_results(message, chat_id)
        return
    
    current_q = quiz_data["questions"][quiz_data["current_question"]]
    participants = quiz_data["participants"]
    current_player = participants[quiz_data["current_turn"] % len(participants)]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{key}) {value}", callback_data=f"group_quiz_answer_{key}_{chat_id}")]
        for key, value in current_q["options"].items()
    ])
    
    await message.bot.send_message(
        chat_id,
        f"👥 Вопрос {quiz_data['current_question'] + 1}:\n{current_q['question']}\n\n"
        f"Очередь игрока: {current_player} (ID: {current_player})"
    )
    await message.bot.send_message(chat_id, "Выберите ответ:", reply_markup=keyboard)

@router.message(Command("stopquiz"))
async def cmd_stop_quiz(message: Message):
    user_id = message.from_user.id
    if user_id in user_quiz_data:
        user_quiz_data.pop(user_id)
        await message.answer("🛑 Викторина остановлена.")
    else:
        await message.answer("❌ Вы не в викторине.")

@router.message(Command("quiz"))
async def cmd_quiz(message: Message):
    chat_type = message.chat.type
    
    if chat_type == "private":
        # Личный чат — викторина для одного
        await start_private_quiz(message)
    else:
        # Группа — викторина для всех
        await start_group_quiz(message)

async def start_private_quiz(message: Message):
    user_id = message.from_user.id
    
    if user_states.get(user_id) == "in_quiz":
        await message.answer("⚠️ Вы уже в викторине!")
        return
    
    # Категории
    categories = {
        1: "История",
        2: "Наука и технологии",
        3: "Культура и искусство",
        4: "География",
        5: "Спорт",
        6: "Литература",
        7: "Фильмы и сериалы",
        8: "Музыка",
        9: "Разное"
    }
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=cat, callback_data=f"quiz_category_{i}")]
        for i, cat in categories.items()
    ])
    
    await message.answer("Выберите категорию:", reply_markup=keyboard)
    user_states[user_id] = "quiz_category_selection"

async def start_group_quiz(message: Message):
    chat_id = message.chat.id
    
    if chat_id in group_quiz_data:
        await message.answer("⚠️ Викторина уже идет в этом чате!")
        return
    
    # Создаем данные для группы
    group_quiz_data[chat_id] = {
        "participants": [],  # Список ID участников
        "current_question": 0,
        "score": {},          # Счет участников
        "current_turn": 0,    # Индекс текущего игрока
        "questions": []       # Список вопросов
    }
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Присоединиться", callback_data="join_quiz")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_quiz")]
    ])
    await message.answer("🎮 Начинается викторина для группы! Нажмите «Присоединиться», чтобы принять участие.", reply_markup=keyboard)

@router.callback_query(lambda query: query.data == "join_quiz")
async def join_group_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    if chat_id not in group_quiz_data:
        await callback.message.answer("❌ Викторина не начата.")
        return
    
    participants = group_quiz_data[chat_id]["participants"]
    
    if user_id in participants:
        await callback.answer("Вы уже участвуете!", show_alert=True)
        return
    
    participants.append(user_id)
    group_quiz_data[chat_id]["score"][user_id] = 0
    await callback.answer("Вы присоединились к викторине!", show_alert=True)

@router.callback_query(lambda query: query.data.startswith("group_quiz_answer_"))
async def handle_group_quiz_answer(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[-1])
    answer = callback.data.split("_")[2].upper()
    user_id = callback.from_user.id
    
    quiz_data = group_quiz_data.get(chat_id)
    if not quiz_data or user_id not in quiz_data["participants"]:
        await callback.message.answer("❌ Вы не участвуете в викторине.")
        return
    
    current_turn = quiz_data["current_turn"]
    participants = quiz_data["participants"]
    
    if user_id != participants[current_turn % len(participants)]:
        await callback.answer("❌ Сейчас не ваша очередь отвечать.", show_alert=True)
        return
    
    current_q = quiz_data["questions"][quiz_data["current_question"]]
    await callback.message.edit_reply_markup(reply_markup=None)
    
    if answer == current_q["correct"]:
        quiz_data["score"][user_id] += 1
        await callback.message.bot.send_message(chat_id, "✅ Правильно!")
    else:
        await callback.message.bot.send_message(chat_id, f"❌ Неверно. Правильный ответ: {current_q['correct']}")
    
    quiz_data["current_turn"] += 1
    quiz_data["current_question"] += 1
    await send_group_question(callback.message, chat_id)

async def show_group_quiz_results(message: Message, chat_id: int):
    quiz_data = group_quiz_data.get(chat_id)
    if not quiz_data:
        await message.answer("❌ Викторина не найдена.")
        return
    
    results = sorted(
        [(user, score) for user, score in quiz_data["score"].items()],
        key=lambda x: x[1],
        reverse=True
    )
    
    result_text = "🏆 Результаты групповой викторины:\n\n"
    for user_id, score in results[:5]:
        result_text += f"{user_id}: {score} очков\n"
    
    await message.answer(result_text)
    group_quiz_data.pop(chat_id, None)


@router.callback_query(lambda query: query.data.startswith("quiz_category_"))
async def handle_quiz_category(callback: CallbackQuery):
    user_id = callback.from_user.id
    category_number = int(callback.data.split("_")[2])
    categories = {
        1: "История",
        2: "Наука и технологии",
        3: "Культура и искусство",
        4: "География",
        5: "Спорт",
        6: "Литература",
        7: "Фильмы и сериалы",
        8: "Музыка",
        9: "Разное"
    }
    selected_category = categories.get(category_number, "Разное")
    
    user_quiz_data[user_id] = {
        "category": selected_category,
        "current_question": 0,
        "score": 0,
        "questions": [],
        "awaiting_answer": False
    }
    
    await callback.message.edit_text(f"🧠 Категория: {selected_category}. Готовы начать?")
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Начать", callback_data="start_quiz")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_quiz")]
    ]))


@router.callback_query(lambda query: query.data == "start_quiz")
async def start_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_quiz_data:
        await callback.message.answer("❌ Вы ещё не выбрали категорию.")
        return
    
    user_quiz_data[user_id]["current_question"] = 0
    await callback.message.delete()
    await ask_next_question(callback.message, user_id)


def parse_quiz_questions(raw_text: str) -> list:
    """Парсит текстовый ответ модели в структуру вопроса"""
    logging.info(f"Парсинг ответа: {raw_text}")
    lines = [line.strip() for line in raw_text.strip().split('\n') if line.strip()]
    questions = []
    current_question = None
    
    for line in lines:
        # Обнаружение вопроса с префиксом "Вопрос:" или номером
        if line.startswith("Вопрос:") or re.match(r'^\d+\.', line):
            if current_question:
                questions.append(current_question)
            question_text = line.split(":", 1)[1].strip() if ":" in line else line.split(".", 1)[1].strip()
            current_question = {
                "question": question_text,
                "options": {},
                "correct": ""
            }
        # Обнаружение вопроса без префикса (например, "Какой фильм...?")
        elif "?" in line and not line.startswith(('A)', 'B)', 'C)', 'D)')) and len(line) > 10:
            if current_question:
                questions.append(current_question)
            current_question = {
                "question": line,
                "options": {},
                "correct": ""
            }
        # Обнаружение вариантов ответов
        elif re.match(r'^[A-D]\)', line):
            if not current_question:
                current_question = {
                    "question": "Неизвестный вопрос",
                    "options": {},
                    "correct": ""
                }
            key = line[0]
            value = line[3:].strip()
            current_question["options"][key] = value
        # Обнаружение правильного ответа
        elif line.startswith("Правильный ответ:"):
            if current_question:
                correct = line.split(":")[-1].strip().upper()
                current_question["correct"] = correct
                questions.append(current_question)
                current_question = None
    
    # Добавляем последний вопрос, если он есть
    if current_question:
        current_question["options"] = {k: v for k, v in current_question["options"].items() if v}
        if len(current_question["options"]) >= 4:
            questions.append(current_question)
    
    # Проверка валидности
    if not questions or any(len(q["options"]) < 4 for q in questions):
        logging.warning("Не удалось распознать структуру вопроса")
        return []
    
    return questions

async def ask_next_question(message: Message, user_id: int):
    quiz_data = user_quiz_data.get(user_id)
    
    if not quiz_data:
        quiz_data = {
            "category": "Разное",
            "current_question": 0,
            "score": 0,
            "questions": [],
            "awaiting_answer": False
        }
        user_quiz_data[user_id] = quiz_data

    if quiz_data["current_question"] >= len(quiz_data["questions"]):
        new_question = await generate_quiz_questions(quiz_data["category"])
        if not new_question:
            await message.answer("⚠️ Не удалось загрузить вопрос.")
            return
        quiz_data["questions"].extend(new_question)

    current_q = quiz_data["questions"][quiz_data["current_question"]]
    quiz_data["awaiting_answer"] = True

    # Генерируем кнопки с вариантами ответов
    keyboard_buttons = [
        [InlineKeyboardButton(text=f"{key}) {value}", callback_data=f"quiz_answer_{key}")]
        for key, value in current_q["options"].items()
    ]
    # Добавляем кнопку "Завершить"
    keyboard_buttons.append([
        InlineKeyboardButton(text="🛑 Завершить", callback_data="finish_quiz")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    # Отправляем вопрос и кнопки
    question_message = await message.answer(f"❓ Вопрос {quiz_data['current_question'] + 1}:\n{current_q['question']}")
    answer_message = await message.answer("Выберите ответ:", reply_markup=keyboard)

    # Сохраняем ID сообщения с кнопками
    quiz_data["last_question_message_id"] = answer_message.message_id
    quiz_data["last_question_message_chat_id"] = answer_message.chat.id

@router.callback_query(lambda query: query.data.startswith("quiz_answer_"))
async def handle_quiz_answer(callback: CallbackQuery):
    user_id = callback.from_user.id
    answer = callback.data.split("_")[-1].upper()
    quiz_data = user_quiz_data.get(user_id)

    if not quiz_data:
        await callback.message.answer("❌ Сначала начните викторину.")
        return

    current_q = quiz_data["questions"][quiz_data["current_question"]]

    # Удаляем сообщение с кнопками
    try:
        chat_id = quiz_data.get("last_question_message_chat_id")
        msg_id = quiz_data.get("last_question_message_id")
        if chat_id and msg_id:
            await bot.delete_message(chat_id, msg_id)
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение с кнопками: {str(e)}")

    # Обработка ответа
    correct_answer = current_q["options"].get(current_q["correct"], "")
    
    if answer == current_q["correct"]:
        quiz_data["score"] += 1
        await callback.message.answer(f"✅ Правильно, это: {correct_answer}")
    else:
        await callback.message.answer(f"❌ Неверно. Правильный ответ: {correct_answer}")

    quiz_data["current_question"] += 1
    await ask_next_question(callback.message, user_id)

@router.callback_query(lambda query: query.data == "next_question")
async def next_question_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    await ask_next_question(callback.message, user_id)


async def show_quiz_results(message: Message, user_id: int):
    quiz_data = user_quiz_data.get(user_id)
    if not quiz_data:
        await message.answer("❌ Викторина не найдена.")
        return
    
    total_questions = len(quiz_data["questions"])
    score = quiz_data["score"]
    rating = "Новичок"
    
    if score >= total_questions * 0.8:
        rating = "Мастер"
    elif score >= total_questions * 0.6:
        rating = "Эксперт"
    elif score >= total_questions * 0.4:
        rating = "Знаток"
    
    result_text = f"🎉 Викторина завершена!\nПравильных: {score}/{total_questions}\nВаш уровень: {rating}"
    await message.answer(result_text)
    
    # Очистка данных
    user_quiz_data.pop(user_id, None)


@router.callback_query(lambda query: query.data == "answer_quiz")
async def answer_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.message.answer("Введите ваш ответ (A, B, C или D):")


@router.callback_query(lambda query: query.data == "cancel_quiz")
async def cancel_quiz(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_quiz_data:
        user_quiz_data.pop(user_id)
    await callback.message.edit_text("❌ Викторина отменена.")
    await callback.answer()

from tenacity import retry, stop_after_attempt, wait_exponential
from langdetect import detect

async def generate_quiz_questions(category: str, count: int = 1):
    """Генерирует один вопрос по выбранной категории"""
    prompt = f"""
    Сгенерируйте {count} вопрос по теме '{category}' на русском языке в формате:
    Вопрос: [Вопрос]
    A) [Вариант 1]
    B) [Вариант 2]
    C) [Вариант 3]
    D) [Вариант 4]
    Правильный ответ: [Буква]

    Пример:
    Вопрос: Какой фильм режиссера Квентина Тарантино получил «Оскар» за лучший сценарий?
    A) Убить Билла
    B) Django Освобожденный
    C) Pulp Fiction
    D) Бешеные псы
    Правильный ответ: B

    ВАЖНО: 
    - Ответ должен быть только на русском языке.
    - Строго соблюдайте указанный формат.
    - Не добавляйте дополнительный текст.
    """
    try:
        import config
        provider_class = getattr(g4f.Provider, config.DEFAULT_QVIZ_PROVIDER)
        
        raw_questions = await g4f.ChatCompletion.create_async(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Вы отвечаете на русском языке."},
                {"role": "user", "content": prompt}
            ],
            provider=provider_class
        )
        
        logging.info(f"Получен ответ от модели: {raw_questions}")
        return parse_quiz_questions(raw_questions)
    except Exception as e:
        logging.error(f"Ошибка генерации вопроса: {str(e)}", exc_info=True)
        return []