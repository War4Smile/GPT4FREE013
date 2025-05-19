import g4f
import asyncio
import aiohttp
import logging
from typing import Dict, List, Optional

class ProviderHealthChecker:
    def __init__(self):
        self.providers = self._get_all_providers()
        self.health_status = {}
        
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
        """Проверяет доступность провайдера"""
        try:
            provider_class = getattr(g4f.Provider, provider_name)
            
            # Проверяем базовую доступность домена
            domain = getattr(provider_class, 'url', None)
            if domain:
                async with aiohttp.ClientSession() as session:
                    async with session.get(domain, timeout=10) as response:
                        domain_reachable = response.status == 200
            else:
                domain_reachable = None
            
            # Проверяем возможность инициализации провайдера
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
        """Тестовый запрос к модели для проверки работоспособности"""
        try:
            provider_class = getattr(g4f.Provider, provider_name)
            
            # Проверяем, есть ли модели у провайдера
            models = getattr(provider_class, 'models', [])
            if not models:
                return {"models_available": False, "error": "Нет доступных моделей"}
            
            # Берем первую модель для теста
            test_model = models[0] if isinstance(models, list) else next(iter(models))
            
            # Формируем тестовый запрос
            test_messages = [{"role": "user", "content": "Привет"}]
            
            # Пытаемся получить ответ
            async with aiohttp.ClientSession() as session:
                response = await g4f.ChatCompletion.create_async(
                    model=test_model,
                    messages=test_messages,
                    provider=provider_class(),
                    api_key=None  # Не используем ключ для теста
                )
            
            # Проверка на наличие HTML-комментариев (<!--)
            if "<!--" in response:
                return {
                    "models_available": True,
                    "test_model": test_model,
                    "test_success": False,
                    "error": "Ответ содержит HTML-комментарии (<!--)"
                }
                
            return {
                "models_available": True,
                "test_model": test_model,
                "test_success": True,
                "response_sample": response[:50] + "..." if isinstance(response, str) and len(response) > 50 else response
            }
            
        except Exception as e:
            return {
                "models_available": False,
                "error": str(e)
            }
    
    async def check_provider_health(self, provider_name: str) -> Dict:
        """Полная проверка состояния провайдера"""
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
    
    def _determine_status(self, auth_check, availability_check, model_check) -> str:
        """Определяет общий статус провайдера"""
        if not availability_check.get("init_success", False):
            return "Неинициализируемый"
        
        if availability_check.get("domain_reachable") is False:
            return "Домен недоступен"
            
        if auth_check.get("auth_required") and not auth_check.get("auth_fields"):
            return "Частично рабочий (требует авторизацию)"
            
        # Проверка на HTML-комментарии в ответе
        if model_check.get("error") and "HTML-комментарии" in model_check["error"]:
            return "Недоступный (HTML-комментарии)"
            
        if model_check.get("test_success"):
            return "Работоспособный"
            
        if model_check.get("models_available") is False:
            return "Нет доступных моделей"
            
        return "Частично рабочий (ограничения)"
    
    async def run_health_check(self) -> Dict[str, Dict]:
        tasks = [self.check_provider_health(provider) for provider in self.providers]
        results = await asyncio.gather(*tasks)
        self.health_status = {result["provider"]: result for result in results}
        return self.health_status
    
    def save_working_providers(self, filename="alworkproviders.py"):
        working_providers = []
        for provider, status in self.health_status.items():
            # Учитываем и полностью рабочих, и частично рабочих
            if status["status"] in ["Работоспособный", "Частично рабочий"]:
                auth_required = status["auth"]["auth_required"]
                if auth_required:
                    working_providers.append(f'"{provider}",  # Требует авторизацию')
                else:
                    working_providers.append(f'"{provider}"')
        
        content = "# availableproviders.py\nAVAILABLE_PROVIDERS = [\n    " + ",\n    ".join(working_providers) + "\n]\n"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Рабочие провайдеры сохранены в {filename}")
        except Exception as e:
            logging.error(f"Ошибка сохранения файла: {str(e)}")

    def save_providers_by_status(self):
        """Сохраняет рабочие и частично рабочие провайдеры в отдельные файлы"""
        fully_working = []
        partially_working = []
        
        for provider, status in self.health_status.items():
            # Полностью рабочие
            if status["status"] == "Работоспособный":
                fully_working.append(f'"{provider}"')
            # Частично рабочие
            elif status["status"] in ["Частично рабочий (требует авторизацию)", "Частично рабочий (ограничения)"]:
                reason = "Требует авторизацию" if status["auth"]["auth_required"] else "Ограничения"
                partially_working.append(f'"{provider}",  # {reason}')
            # Исключаем провайдеров с HTML-комментариями
            elif status["status"] == "Недоступный (HTML-комментарии)":
                logging.warning(f"{provider} исключен из-за HTML-комментариев в ответе")
        
        # Сохранение полностью рабочих
        self._save_to_file(fully_working, "alworkproviders.py", comment="РАБОЧИЕ")
        # Сохранение частично рабочих
        self._save_to_file(partially_working, "alworkproviders_partial.py", comment="ЧАСТИЧНО РАБОЧИЕ")

    def _save_to_file(self, providers: List[str], filename: str, comment: str):
        if not providers:
            logging.warning(f"Нет провайдеров для сохранения в {filename}")
            return
        
        content = f"# alworkproviders.py - {comment}\nAVAILABLE_PROVIDERS = [\n    " + ",\n    ".join(providers) + "\n]\n"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Провайдеры сохранены в {filename}")
        except Exception as e:
            logging.error(f"Ошибка сохранения {filename}: {str(e)}")

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