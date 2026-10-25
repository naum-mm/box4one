# Проверки Telegram-интеграции

## Пройдено

- `python3 -m unittest discover -s tests -p test_deployment.py -v`: **3/3**.
- `python3 -m unittest discover -s tests -p test_download_proxy.py -v`: **4/4**.
- Синтаксическая компиляция backend, bot, proxy, scripts и tests.
- `git diff --check`.
- Compose configuration без Telegram, с Telegram и изолированная тестовая конфигурация.
- Профиль Telegram исключает telegram-bot из списка сервисов по умолчанию.

## Написано, но не выполнено

- **18** API-тестов: отдельная MariaDB, повторная миграция, конкурентные запросы,
  лимиты, ACL/файлы, подпись/TTL/ZIP, уведомления, offset/lease, веб/worker API.
- **12** bot-тестов: aiogram Dispatcher со stub Telegram session, команды,
  URL/форматы, безопасные ошибки, отправка файла/ссылки, retry_after, потеря backend.
- Полная сборка образов и проверка сетей в runtime.
- Nginx `-t` для изменённых шаблонов и реальные источники yt-dlp через прокси.
- Реальные Telegram-вызовы не выполнялись.

Подробности ограничений среды сохранены в приватном отчёте владельцу.
Production-миграция и deployment не выполнялись. Тесты не подключались к production-БД.

**PR является draft; считать интеграцию готовой к приёмке до перечисленных
непроведённых проверок нельзя.** Не выполнять merge/auto-merge/release/deploy.
