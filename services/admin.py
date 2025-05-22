# services/admin.py
import logging
import asyncio
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, BufferedInputFile, FSInputFile, BotCommand, BotCommandScopeChat, TelegramObject
from services.tgapi import bot
from database import (
                        save_users, load_users, save_blocked_users,
                        user_history, user_settings, user_info,
                        image_requests, last_image_requests,
                        user_states, admin_states, blocked_users,
                        user_analysis_states, user_analysis_settings,
                        user_transcribe_states
                    )
from datetime import datetime
from config import ADMINS

router = Router()

def is_admin(user_id: int):
    return user_id in ADMINS

@router.message(Command("adminusers"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Доступ запрещен")
        return
        
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="🚫 Заблокированные", callback_data="admin_blocked_list")]
    ])
    await message.answer("🔐 Админ-панель", reply_markup=keyboard)

@router.callback_query(lambda c: c.data.startswith("admin_block_"))
async def block_user(query: CallbackQuery):
    if not is_admin(query.from_user.id):
        await query.answer("❌ Доступ запрещен")
        return
        
    user_id = int(query.data.split("_")[2])
    if is_admin(user_id):
        await query.answer("❌ Невозможно заблокировать администратора")
        return
        
    blocked_users[str(user_id)] = datetime.now().isoformat()
    save_blocked_users()
    await query.message.edit_text("✅ Пользователь заблокирован")


###################################################
########### Дополнения для админки ################




####################################################
################### Админ панель ###################

# Обработчик для статистики
@router.callback_query(lambda query: query.data == "admin_stats")
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
@router.callback_query(lambda query: query.data == "admin_main_menu")
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
@router.callback_query(lambda query: query.data == "admin_close")
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
@router.message(Command("adminusers"))
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
@router.callback_query(lambda query: query.data == "admin_users_list")
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
@router.callback_query(lambda query: query.data.startswith("admin_message_"))
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
@router.message(lambda message: is_admin(message.from_user.id) and message.from_user.id in admin_states)
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
@router.callback_query(lambda query: query.data.startswith("admin_user_"))
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
@router.callback_query(lambda query: query.data.startswith("admin_history_"))
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
@router.callback_query(lambda query: query.data.startswith("admin_block_"))
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

@router.callback_query(lambda query: query.data.startswith("admin_unblock_"))
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
@router.callback_query(lambda query: query.data.startswith("response_"))
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
@router.callback_query(lambda query: query.data == "admin_blocked_list")
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
@router.callback_query(lambda query: query.data == "admin_cancel")
async def handle_admin_cancel(query: CallbackQuery):
    await query.message.edit_text("Действие отменено")
    await query.answer()

# Проверка блокировки пользователя
@router.message(lambda message: message.from_user.id in blocked_users)
async def handle_blocked_user(message: Message):
    await message.answer("🚫 Вы заблокированы и не можете использовать бота.")

# Обработчик инлайн-кнопок админки (ИСПРАВЛЕННЫЙ ВАРИАНТ)
@router.callback_query(lambda query: query.data.startswith("admin_"))
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
