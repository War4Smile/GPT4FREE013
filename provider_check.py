# utils/provider_check.py
import os
import g4f
import asyncio
import aiohttp
import logging
from typing import Dict, List, Optional

PROVIDERS_DIR = "providers"
if not os.path.exists(PROVIDERS_DIR):
    os.makedirs(PROVIDERS_DIR)

def auto_detect_language(text):
    if not text or not isinstance(text, str):
        return "unknown"
    # Простая проверка на русский
    cyrillic_ratio = sum(1 for char in text if char.isalpha() and char.isascii()) / len(text)
    if cyrillic_ratio > 0.5:
        return "ru"
    # Можно использовать langdetect или fasttext
    return "en"  # По умолчанию

class ProviderHealthChecker:
    def __init__(self):
        self.providers = self._get_all_providers()
        self.health_status = {}
        self.PROVIDERS_DIR = PROVIDERS_DIR  # Указываем папку для сохранения
        
    def _get_all_providers(self) -> List[str]:
        return [
            provider for provider in dir(g4f.Provider) 
            if not provider.startswith("_") and provider != "base_provider"
        ]
    
    async def check_provider_auth(self, provider_name: str) -> Dict:
        """Проверяет требования к авторизации для провайдера"""
        try:
            provider_class = getattr(g4f.Provider, provider_name)
            # Проверяем наличие атрибута auth в классе провайдера
            auth_required = getattr(provider_class, 'auth', False)
            
            return {
                "auth_required": auth_required,
                "auth_fields": getattr(provider_class, 'required_fields', []) if auth_required else []
            }
        except Exception as e:
            return {
                "auth_required": False,
                "error": str(e)
            }
    
    async def check_provider_availability(self, provider_name: str) -> Dict:
        try:
            provider_class = getattr(g4f.Provider, provider_name)
            domain = getattr(provider_class, "domain", None)
            
            if not domain:
                return {"domain_reachable": None, "init_success": True}
            
            # Проверяем доступность домена с таймаутом
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(domain, timeout=5) as response:
                        domain_reachable = response.status == 200
            except (aiohttp.ClientError, asyncio.TimeoutError):
                domain_reachable = False
            
            # Проверяем инициализацию провайдера
            try:
                provider_instance = provider_class()
                init_success = True
            except Exception as e:
                init_success = False
            
            return {
                "domain_reachable": domain_reachable,
                "init_success": init_success,
                "domain": domain
            }
            
        except Exception as e:
            return {
                "domain_reachable": False,
                "init_success": False,
                "error": str(e)
            }
    
    async def test_model_response(self, provider_name: str) -> Dict:
        try:
            provider_class = getattr(g4f.Provider, provider_name)
            models = getattr(provider_class, 'models', [])
            
            if not models:
                return {"models_available": False, "error": "Нет доступных моделей"}
            
            # Берем первую модель для теста
            test_model = models[0] if isinstance(models, list) else next(iter(models))
            test_messages = [{"role": "user", "content": "Привет"}]
            
            # Тестируем модель с таймаутом
            try:
                response = await asyncio.wait_for(
                    g4f.ChatCompletion.create_async(
                        model=test_model,
                        messages=test_messages,
                        provider=provider_class(),
                        api_key=None
                    ),
                    timeout=10  # Ждем не более 10 секунд
                )
            except (asyncio.TimeoutError, Exception) as e:
                return {
                    "test_success": False,
                    "error": str(e)
                }
            
            # Проверяем, не содержит ли ответ HTML-комментарии
            if "<!--" in response:
                return {
                    "test_success": False,
                    "error": "Ответ содержит HTML-комментарии"
                }
            
            return {
                "test_success": True,
                "response_sample": response[:50] + "...",
                "model": test_model
            }
            
        except Exception as e:
            return {
                "test_success": False,
                "error": str(e)
            }
    
    async def check_provider_health(self, provider_name: str) -> Dict:
        try:
            auth_check = await self.check_provider_auth(provider_name)
            availability_check = await self.check_provider_availability(provider_name)
            model_check = await self.test_model_response(provider_name)
            
            return {
                "provider": provider_name,
                "auth": auth_check,
                "availability": availability_check,
                "model_test": model_check,
                "status": self._determine_status(auth_check, availability_check, model_check)
            }
            
        except Exception as e:
            return {
                "provider": provider_name,
                "auth": {"auth_required": False},
                "availability": {"domain_reachable": False, "init_success": False},
                "model_test": {"test_success": False, "error": str(e)},
                "status": "Частично рабочий (таймаут)"
            }
    
    def _determine_status(self, auth_check, availability_check, model_check) -> str:
        if not availability_check.get("init_success", False):
            return "Неинициализируемый"
        
        if model_check.get("test_success"):
            return "Работоспособный"
        
        if auth_check.get("auth_required") and not auth_check.get("auth_fields"):
            return "Частично рабочий (требует авторизацию)"
        
        if not availability_check.get("domain_reachable"):
            return "Частично рабочий (домен недоступен)"
        
        return "Частично рабочий (ограничения)"
        
    async def run_health_check(self) -> Dict[str, Dict]:
        tasks = [self.check_provider_health(provider) for provider in self.providers]
        results = await asyncio.gather(*tasks)
        self.health_status = {result["provider"]: result for result in results}
        
        self.save_working_providers("working.py")
        self.save_providers_by_status()
        
        return self.health_status
    
    def save_working_providers(self, filename="working.py"):
        working = [f'"{provider}"' for provider, status in self.health_status.items()
                if status["status"] == "Работоспособный"]
        
        self._save_to_file(working, filename, "Работоспособные провайдеры")

    def save_providers_by_status(self):
        """Сохраняет провайдеров по категориям в папку providers"""
        fully_working = []
        partially_working = []
        non_working = []

        for provider, status in self.health_status.items():
            if status["status"] == "Работоспособный":
                fully_working.append(f'"{provider}"')
            elif status["status"] == "Частично рабочий (таймаут)":
                partially_working.append(f'"{provider}", # Таймаут')
            else:
                non_working.append(f'"{provider}", # {status["status"]}')

        # Сортировка: Qwen в начало
        def sort_key(item):
            # Извлекаем имя провайдера из строки
            name = item.split('",')[0].strip('"')
            return not name.startswith("Qwen")  # Qwen -> True, остальные -> False

        fully_working_sorted = sorted(fully_working, key=sort_key)

        # Сохраняем отсортированные списки
        self._save_to_file(fully_working_sorted, "fully_working.py", "Полностью рабочие (Qwen в приоритете)")
        self._save_to_file(partially_working, "partially_working.py", "Частично рабочие")
        self._save_to_file(non_working, "non_working.py", "Нерабочие")

    def _save_providers_list(self, providers: list, filename: str):
        content = f"# {filename}\nAVAILABLE_PROVIDERS = [\n    " + ",\n    ".join(providers) + "\n]"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Сохранено {len(providers)} провайдеров в {filename}")
        except Exception as e:
            logging.error(f"Ошибка сохранения {filename}: {str(e)}")

    def _save_to_file(self, providers: List[str], filename: str, comment: str = ""):
        full_path = os.path.join(PROVIDERS_DIR, filename)
        
        if not providers:
            logging.warning(f"Нет провайдеров для сохранения в {filename}")
            return

        content = f"# {filename} - {comment}\n"
        content += "AVAILABLE_PROVIDERS = [\n    " + ",\n    ".join(providers) + "\n]"
        
        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Сохранено {len(providers)} провайдеров в {full_path}")
        except Exception as e:
            logging.error(f"Ошибка сохранения {full_path}: {str(e)}")

    def get_summary_report(self) -> Dict[str, any]:
        """Генерирует сводный отчет о состоянии провайдеров"""
        total = len(self.health_status)
        working = sum(1 for p in self.health_status.values() if p["status"] == "Работоспособный")
        auth_required = sum(1 for p in self.health_status.values() if p["auth"]["auth_required"])
        unavailable = sum(1 for p in self.health_status.values() if p["status"] in ["Домен недоступен", "Неинициализируемый"])

        return {
            "total_providers": total,
            "working_providers": working,
            "auth_required": auth_required,
            "unavailable_providers": unavailable,
            "working_percentage": round((working/total)*100, 2) if total > 0 else 0,
            "providers_by_status": {
                status: sum(1 for p in self.health_status.values() if p["status"] == status)
                for status in set(p["status"] for p in self.health_status.values())
            }
        }

# Пример использования
async def main():
    checker = ProviderHealthChecker()
    await checker.run_health_check()
    
    # Сохранение рабочих и частично рабочих провайдеров
    checker.save_providers_by_status()
    
    # Вывод сводного отчета
    report = checker.get_summary_report()
    print("\n📊 СВОДНЫЙ ОТЧЕТ ПО ПРОВАЙДЕРАМ:")
    print(f"Всего провайдеров: {report['total_providers']}")
    print(f"Работоспособных: {report['working_providers']} ({report['working_percentage']}%)")
    print(f"Частично рабочих: {report['providers_by_status'].get('Частично рабочий (требует авторизацию)', 0) + report['providers_by_status'].get('Частично рабочий (ограничения)', 0)}")
    print(f"Недоступных: {report['unavailable_providers']}")

if __name__ == "__main__":
    asyncio.run(main())