# config.py
import os
from dotenv import load_dotenv

# Загрузка переменных из .env
load_dotenv()

# ID администраторов
ADMINS = [int(uid) for uid in os.getenv("ADMINS", "").split(",") if uid.strip()]

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "TG_TOKEN")

# API ключ для DeepSeek
API_DeepSeek = os.getenv("API_DeepSeek", "")


# Дополнительные параметры генерации изображений
IMAGE_PARAMS = {
    "private": False,
    "enhance": True,
    "safe": True,
    "referrer": "MyTestBot",  # Идентификатор вашего бота
    "nologo": True
}
# Провайдер для генерации изображений
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "PollinationsAI")
DEFAULT_TRANSLATION_PROVIDER = "FreeGpt"
DEFAULT_TRANSLATION_MODEL = "gpt-3.5-turbo"
# Модель для генерации изображений
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "flux")
DEFAULT_IMAGE_MODELS = ["flux", "flux-anime", "flux-cablyai"]
GPTIMAGE_API_TOKEN = "ВАШ_ТОКЕН"

# FusionBrain API
FUSIONBRAIN_APIKEY = os.getenv("FUSIONBRAIN_APIKEY", "FUSIONBRAIN_APIKEY")
FUSIONBRAIN_APISECRET = os.getenv("FUSIONBRAIN_APISECRET", "FUSIONBRAIN_APISECRET")

# Speechmatics API
SPEECHMATICS_API = os.getenv("SPEECHMATICS_API", "SPEECHMATICS_APIKEY")
TRANSCRIPTION_LANGUAGE = os.getenv("TRANSCRIPTION_LANGUAGE", "ru")

# Настройки анализа изображений
ANALYZE_SUGGESTION = "Вы хотите проанализировать изображение? Используйте команду /analyze"
IMAGE_ANALYSIS_MODEL = "openai"
MAX_IMAGE_SIZE = 512 * 1024 * 1024  # 512 MB
SUPPORTED_IMAGE_FORMATS = ['image/jpeg', 'image/png', 'image/webp']
ANALYSIS_QUALITY_SETTINGS = {
    "high": 500,   # Высокая детализация
    "medium": 300, # Средняя детализация
    "low": 150     # Низкая детализация
}

# Настройки генерации аудио
TTS_MODEL = "openai-audio"
SUPPORTED_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
AUDIO_FORMAT = "mp3"  # Поддерживаемый формат

# Настройки транскрибации через Pollinations AI
TRANSCRIBE_MODEL = "openai-audio"
SUPPORTED_AUDIO_FORMATS = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'webm']
MAX_AUDIO_SIZE = 200 * 1024 * 1024  # 200 MB

# Настройки генерации аудио
TTS_MODEL = "openai-audio"
DEFAULT_VOICE = "alloy"  # Голос по умолчанию
SUPPORTED_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
AUDIO_FORMAT = "mp3"  # Формат выходного аудио