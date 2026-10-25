# Telegram-бот BOX4ONE

## Статус первой версии

Автоматизированные тесты команд, API и миграций выполняются на отдельной MariaDB
с синтетическими данными. Результаты — в [TELEGRAM-VALIDATION.md](TELEGRAM-VALIDATION.md).
Реальный токен не предоставлен: отправка настоящих сообщений не проверена.
Production в ходе разработки не обновлялся.

## Архитектура

`Telegram → aiogram 3 / long polling → внутренний FastAPI → MariaDB jobs → существующий worker`.
Бот работает только в личных чатах. Никакой отдельной очереди, базы или загрузчика.
Пять форматов используют общий `enqueue_job` с веб-интерфейсом. Polling offset,
аренда единственного polling-процесса, черновики и состояние уведомлений находятся
в MariaDB. Контейнер бота не получает WORKER_API_TOKEN, доступ к БД или файловому
тому и работает от UID/GID 65532 без capabilities, с read-only filesystem.

Отдельный stdlib HTTP/CONNECT-прокси `download-proxy` проверяет и фиксирует публичный
IP назначения для каждого соединения, включая адреса после перенаправлений и URL
сегментов плейлистов. Запрещены частные, loopback, link-local, multicast адреса,
смешанные DNS-ответы и нестандартные порты. Worker подключён только к внутренней
Docker-сети. yt-dlp всегда использует этот прокси; FFmpeg/ffprobe работают только
с локальными протоколами file/pipe/crypto/data. Это изменение защищает и веб-задания.
Некоторые источники, требующие прямого сетевого FFmpeg или нестандартных портов,
будут отклонены. Перед production требуется проверка ваших обычных источников.

Nginx и бот имеют отдельную сеть с внешним доступом. Внутренний API не публикуется
на порту хоста, а Nginx запрещает `/api/integrations/telegram`. Backend слушает только
в Docker-сети. Не добавляйте ему внешние ports и не подключайте worker к egress.

## Создание бота и User ID

1. Откройте официальный `@BotFather`, выполните `/newbot`, задайте имя и username.
2. Сохраните токен в `.env` на целевом сервере с правами 0600. Не присылайте токен в чат и не
   добавляйте его в Git. Не запускайте второй polling-процесс с тем же токеном.
3. Узнайте свой числовой Telegram User ID из поля `message.from.id` update вашего
   бота: после отдельного разрешения на реальное обращение к Telegram выполните
   getUpdates безопасным клиентом, читающим токен из окружения и выводящим только ID.
   Не помещайте токен в URL команды/историю shell и не сохраняйте полный update.
4. Укажите эти ID через запятую. Username не заменяет ID. Права выдаются только
   через конфигурацию администратора; бот не добавляет пользователей автоматически.

У бота должны быть отключены webhook и другие polling-клиенты. Приложение не
удаляет webhook автоматически: это изменение внешнего состояния требует согласования.

## Переменные

Дополните существующий `.env`, сохранив все текущие значения:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:REPLACE_WITH_BOTFATHER_TOKEN
TELEGRAM_INTERNAL_TOKEN=REPLACE_WITH_RANDOM_TOKEN_AT_LEAST_32_CHARS
TELEGRAM_ALLOWED_USERS=123456789,987654321
TELEGRAM_MAX_ACTIVE_JOBS=2
TELEGRAM_POLL_SECONDS=10
TELEGRAM_UPLOAD_LIMIT_BYTES=49000000
TELEGRAM_LINK_TTL_SECONDS=600
```

Пример не содержит действующих секретов. Внутренний токен должен отличаться от
WORKER_API_TOKEN; создайте его через `secrets.token_hex(32)`. Не выводите его в логи.
HTTPS base URL backend получает как `https://${DOMAIN}`; бот использует только
`http://backend:8000`. Версии образов/пакетов находятся в Dockerfile/requirements.
Все параметры необязательны для установщика при отключённом боте. Пустой internal
токен закрывает внутренние маршруты с 503. Allowlist проверяется и в окружении,
и в `telegram_users.enabled`; после изменения списка повторите миграцию.

## API и миграция

Внутренние запросы требуют `X-Telegram-Token` и `X-Telegram-User`:

- `POST /api/integrations/telegram/drafts`: URL и ID исходного сообщения;
- `POST /api/integrations/telegram/jobs`: draft_id, format, message_id;
- `GET /api/integrations/telegram/jobs[?pending=true]`: только задания владельца;
- `GET /api/integrations/telegram/jobs/{code}`;
- `PUT /api/integrations/telegram/jobs/{code}/notification`;
- `GET /api/integrations/telegram/jobs/{code}/files/{id}`: поток для отправки в Telegram;
- `POST /api/integrations/telegram/jobs/{code}/links/{id|archive}`: временная HTTPS-ссылка.

`POST /runtime` использует только integration token: продлевает аренду и сохраняет
монотонный offset. Аренда 90 секунд обновляется каждые 20 секунд; новый экземпляр
после аварии ждёт её окончания. Эта роль не имеет worker/admin-прав.

`python -m app.telegram_migrate` явно создаёт три дополнительные таблицы:
`telegram_users` (Telegram ID BIGINT), `telegram_requests` (URL, связь с jobs,
unique user/message, уведомление), `telegram_runtime` (offset и аренда).
Существующие jobs/files не изменяются. Повторный запуск безопасен, но выполняйте
миграции последовательно. На startup миграция Telegram не запускается.

Создание задания блокирует строку пользователя: одновременные запросы проверяют
лимит и unique-ключ в одной транзакции. Первый выбранный формат становится
окончательным; повтор кнопки возвращает то же задание. Черновик действует час,
не более 60 новых ссылок в час на пользователя. Удалять записи дедупликации нельзя
без отдельной политики хранения: они защищают от повторного создания заданий.

## Включение после merge — только с отдельным разрешением

Сначала определите каталог установленного проекта и наличие Git checkout.
Перед установкой сопоставьте файлы с проверенным commit PR и сохраните локальные
отличия Compose, env_file и Nginx. Для конфигурации с несколькими сайтами переносите
изменения выборочно, сохраняя остальные виртуальные хосты.
1. Проверить результаты тестов и совместимость ваших источников. Для повторного
   прогона на целевом сервере используйте приведённые ниже ограничения памяти.
2. Получить разрешение на резервную копию, production-обновление, миграцию,
   изменение `.env`/Nginx и запуск настоящего бота.
3. Сделать и проверить резервную копию, сохранить образы и конфигурацию отката.
4. Перенести проверенные изменения. Не удалять volumes/каталоги данных.
5. Настроить `.env`; применить в рабочий Nginx только новые location-блоки,
   сохранив существующие виртуальные хосты. В образ backend добавлен `--no-access-log`,
   чтобы bearer-ссылки не попадали в access logs.
6. Проверить Compose без вывода раскрытых секретов:

```bash
docker compose config --quiet
docker compose --profile telegram config --quiet
docker compose build backend worker download-proxy telegram-bot
docker compose run --rm --no-deps backend python -m app.telegram_migrate
```

7. Согласованно обновить сеть и backend/worker/Nginx/cleanup: внутренняя сеть и
   egress требуют пересоздания контейнеров. Не обещается обновление без перерыва.

```bash
docker compose --profile telegram up -d --no-build
./scripts/diagnose.sh
```

8. Проверить веб-очередь и одну привычную загрузку, затем `/start`, ссылку,
   выбор формата, `/status`, `/myjobs`, завершение, маленький файл и большую ссылку.
   Проверить запрет доступа второму, неразрешённому ID.

На production эти команды в рамках разработки не выполнялись.

## Команды и выдача файлов

Все сообщения на русском: `/start`, `/help`, `/status`, `/myjobs`. Пользователь
отправляет одну ссылку, выбирает Видео 1080/720/480 или MP3 320/128. Бот редактирует
сообщение задания; опрос не чаще раза в 5 секунд (по умолчанию 10), изменения с
одинаковым текстом не отправляются. Отправка ограничена глобально и на чат;
Telegram 429 обрабатывается с учётом retry_after.

На завершении появляется «Скачать файлы». Маленькие файлы передаются через
sendDocument ограниченными блоками по 64 КиБ, без загрузки целиком в RAM. Лимит
консервативно ограничен 49 000 000 байт. Большие файлы и ZIP выдаются по HMAC-ссылке
с владельцем, заданием, ID файла и сроком действия; срок не превышает TTL задания.
Backend повторно проверяет владельца, allowlist, готовность и TTL при скачивании.
Старые публичные `/api/jobs/{code}` и download/archive отвечают 404 для Telegram-
заданий, чтобы не обходить проверку владельца. Для веб-заданий маршруты сохранены.

Ссылка является bearer-доступом: получатель может переслать её до истечения срока.
Для защиты от пересылки нужен отдельный веб-вход пользователя; первая версия его
не реализует. Не публикуйте такие ссылки. В Nginx для них отключены access/error
логи, ответы no-store. Полученные через Telegram копии файла нельзя отозвать при
истечении TTL BOX4ONE; TTL запрещает только дальнейшее скачивание с BOX4ONE.

MP4 с типом video отправляется через sendVideo с поддержкой потокового воспроизведения;
MP3/M4A с типом audio — через sendAudio. Неизвестные и остальные форматы
отправляются документами. Размерный порог и HTTPS-ссылки сохраняются.

Официальные ограничения: [Telegram Bot API](https://core.telegram.org/bots/api#senddocument)
и [FAQ](https://core.telegram.org/bots/faq). Для обычного облачного API отправка
документа ограничена 50 MB; локальный Bot API Server не используется.
Версия библиотеки: [aiogram 3.31](https://docs.aiogram.dev/en/latest/).

## Диагностика, перезапуск и отключение

```bash
./scripts/diagnose.sh
docker compose --profile telegram ps telegram-bot
docker compose --profile telegram logs --tail=100 telegram-bot
docker compose --profile telegram restart telegram-bot
```

Restart восстанавливает cursor и неотправленные уведомления из MariaDB.
Для отключения явно остановите профиль: исключение `--profile telegram` из команды
не останавливает уже существующий контейнер.

```bash
docker compose --profile telegram stop telegram-bot
```

Backend/веб-очередь и worker продолжают работать. Для отзыва integration API удалите
токен из production-конфигурации и пересоздайте backend после согласования.

Типовые ошибки:

- «Доступ запрещён»: проверьте числовой ID в обоих allowlist, повторите миграцию;
- 503: token пустой/короткий/совпадает с worker, миграция не выполнена или backend недоступен;
- бот уже запущен: остановите только лишний экземпляр; после crash подождите 90 секунд;
- Telegram 409/webhook: другой клиент использует токен или активен webhook;
- «Ссылка истекла»: закончился срок ссылки/хранения или черновик старше часа;
- 429: лимит активных заданий или ссылок; дождитесь завершения задания;
- источник не скачивается: проверьте публичный DNS/порты и совместимость с прокси;
- Telegram недоступен: polling повторится с паузой; обновления Telegram хранятся
  ограниченное время, длительный простой может привести к их потере;
- 413/ошибка отправки: используйте HTTPS-ссылку. Повторный ручной запрос файла
  может создать ещё одно сообщение, но никогда не создаёт ещё одно задание.

Текст ошибок upstream, токены и URL Telegram не пишутся ботом в логи. Существующие
логи worker могут содержать адреса источников: не публикуйте их без редактирования.

## Backup, восстановление, обновление и откат

После разрешения используйте `mariadb-dump --single-transaction` внутри контейнера:
пароль передавать из уже существующего окружения, не в argv. Сохраняйте все таблицы,
включая telegram_*, конфигурацию `.env`/config.env/Nginx (права 0600), а для
восстановления файлов — jobs. Согласуйте время снимка БД/файлов с worker и cleanup.
Не считайте копию проверенной, пока она не восстановлена в отдельной тестовой БД.
Восстановление production выполняется только по отдельному разрешению; бот в это
время должен быть остановлен, иначе offset/уведомления могут расходиться.

Обновление: проверить новый commit и тесты, сделать backup, собрать образы на целевом сервере,
выполнить только требуемую миграцию и согласованно пересоздать сервисы.
Откат: остановить telegram-bot, вернуть сохранённые образы, Compose и конфигурацию
сети/Nginx, закрыть internal token; дополнительные telegram_* таблицы сохранить.
**Старая версия backend не скрывает Telegram-задания в публичном API.** При откате
на такую версию сначала запретите через Nginx публичный доступ к кодам Telegram-
заданий либо согласуйте временное закрытие публичной выдачи; не выполняйте слепой
откат, открывающий файлы. Не восстанавливайте старую БД поверх новых заданий.
Автоочистка jobs и файлов не изменена.

## Изолированные тесты

Тесты запускаются на целевом сервере с отдельными сетью, volume и MariaDB.
Тестовая БД ограничена 128 МБ RAM, runner — 192 МБ; отдельно ограничены swap и CPU.
При недостатке RAM администратор должен заранее настроить swap на этом же сервере.
Не используйте production-БД и не переносите тесты на другой сервер.

```bash
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml build tests
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml up -d db
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml run --rm tests
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml stop db
```

Реальные образы можно проверить отдельным дополнением
`tests/compose.telegram.runtime.yaml`. Собирайте сервисы backend, download-proxy,
telegram-bot и worker последовательно. В этом дополнении бот выполняет только
импорт модуля: настоящий polling и обращения к Telegram не запускаются.
`check_telegram_runtime.py` проверяет HTTP API, живой прокси и обработку задания
существующим worker. `check_worker_runtime.py` проверяет локальную обработку
синтетического аудио и запрет сетевого FFmpeg. Запускайте их отдельно от основного
набора: основной набор очищает только свою тестовую БД между тестами.

Команды дополнительных проверок после последовательной сборки:

```bash
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml -f tests/compose.telegram.runtime.yaml up -d --no-build backend download-proxy worker
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml -f tests/compose.telegram.runtime.yaml run --rm --no-deps tests python tests/check_telegram_runtime.py
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml -f tests/compose.telegram.runtime.yaml run --rm --no-deps -v "$PWD/tests:/checks:ro" worker python /checks/check_worker_runtime.py
docker compose -p box4one-telegram-test -f tests/compose.telegram.yaml -f tests/compose.telegram.runtime.yaml stop worker backend download-proxy db
```

У тестовой конфигурации нет внешних портов, production bind mounts и фиксированных
container_name. Используется отдельный `test-db` volume. Не подменяйте `DB_HOST`
на production. Тестовые токены фиктивные, все Telegram-вызовы mock/stub. Тесты
проверяют команды, форматы, повтор update/callback, конкурентные запросы и лимит,
изоляцию владельцев, приватные URL, выдачу/подпись/TTL, ZIP, состояния уведомлений,
offset/lease, миграцию повторно на MariaDB и старые веб/worker маршруты.

Честные границы гарантии: задания создаются один раз по ключу пользователя/сообщения.
Telegram sendMessage/sendDocument не поддерживают транзакцию с MariaDB: при crash
между отправкой нового сообщения и сохранением его ID возможно повторное сообщение.
Редактирование существующего сообщения и сохранённые состояния сокращают это окно.
