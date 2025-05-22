# database.py
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

###################################################
########### Словари для хранения данных ###########
# Словарь для хранения информации о пользователях
user_info = {}
# Словарь для хранения истории сообщений пользователей
user_history = {}
# Словарь для хранения настроек пользователей
user_settings = {}
# Словарь для состояний пользователей
user_states = {}
# Словарь для состояний админов
admin_states = {}

# Словарь для хранения истории запросов на генерацию изображений
image_requests = {}
# Словарь для хранения данных о последнем запросе на изображение
last_image_requests = {}
# Словарь для хранения истории генерации изображений
image_history = {}
# Словарь для хранения заблокированных пользователей
blocked_users = {}

# Словарь для хранения состояний анализа изображений
user_analysis_states = {}
# Словарь для хранения настроек анализа
user_analysis_settings = {}
# Словарь для хранения текущих запросов анализа
image_analysis_requests = {}
# Словарь для хранения состояний транскрибации
user_transcribe_states = {}

# Файл для хранения данных пользователей
USER_DATA_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), 'user_data.json'))
# Файл для хранения заблокированных пользователей
BLOCKED_USERS_FILE = 'blocked_users.json'

# Префикс для перегенерации изображений
REGENERATE_CALLBACK_PREFIX = "regenerate:"
regenerate_cb = REGENERATE_CALLBACK_PREFIX 

###################################################
############# Функция загрузки данных #############
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