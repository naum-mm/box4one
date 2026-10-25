# Настройка BOX4ONE

GUI-интерфейс для yt-dlp. В репозитории **нет** базы задач, скачанных файлов, рабочих `.env`, сертификатов, ключей и состояния воркера. При первом запуске MariaDB создаёт пустую базу, backend создаёт таблицы. Старые задачи и история не переносятся. Повторный запуск установщика существующую базу НЕ очищает.

## 1. Требования

Новый VPS с root-доступом и Ubuntu 24.04 LTS (amd64 или arm64), systemd, доступом в Интернет. Обычный shared hosting без Docker не подходит. Рекомендуется от 2 ГБ RAM и 20 ГБ диска, лучше больше для видео; на 1 ГБ RAM возможна нехватка памяти при обработке. Установщик не создаёт swap. Свободные порты 80 и 443. A-запись домена должна указывать на новый сервер; если есть AAAA, IPv6 тоже должен вести на него. Проверить firewall хостинга: входящие TCP 80/443 должны проходить. DNS и firewall скрипты не изменяют.

## 2. Установка и HTTP

Установка одной командой из [README](../README.md) загружает репозиторий в `/opt/ytdlp-web` и вызывает `install.sh`. Она отказывается перезаписывать существующий каталог.

Можно установить из локального checkout (от root):

```bash
git clone https://github.com/naum-mm/box4one.git /opt/ytdlp-web
cd /opt/ytdlp-web
./install.sh video.example.com
```

Заменить `video.example.com` своим доменом. Использовать ASCII-домен или punycode, без `https://`, порта и пути.

Установщик устанавливает Python/curl и, если Docker отсутствует, Docker Engine, Buildx и Compose из официального apt-репозитория Docker. Существующий Docker не переустанавливает: у него уже должен быть плагин `docker compose`. При обнаружении конфликтующих пакетов останавливается и ничего не удаляет. Включает автозапуск Docker, создаёт `.env` с новыми случайными секретами, HTTP-конфиг и каталоги данных, собирает и запускает контейнеры, выполняет диагностику. Длительность первой сборки зависит от сервера и сети. При сбое исправить причину и повторить ту же команду: секреты и база сохраняются.

После успешного запуска:

```bash
curl --fail http://video.example.com/health
nano /opt/ytdlp-web/.env
```

Пароль обычного доступа — `DOWNLOAD_ACCESS_WORD`, административного доступа к очереди — `QUEUE_ACCESS_WORD`. Скрипт не печатает их в журнал. Все значения автоматически сгенерированы, менять их для первого запуска не обязательно.

Для настройки лимитов **до запуска** можно отдельно выполнить (нужен Python 3):

```bash
python3 scripts/configure.py init --domain video.example.com
nano .env
./install.sh video.example.com
```

`.env.example` — справочник. Не копировать его заглушки как рабочие пароли.

## 3. Переменные .env

Здесь используется один файл `.env`: токен и параметры передаются Compose во все нужные контейнеры. Отдельные `worker/config.env` и `cleanup/config.env` в этой копии не нужны.

Формат строго `ИМЯ=значение`, без кавычек, пробелов и выполнения shell-команд. Основные значения: латинские буквы, цифры, `_`, `-`, `.`; URL и токен Telegram проверяются по отдельным правилам; домен — строчными буквами. Это также исключает специальные символы, требующие экранирования в SQLAlchemy URL и Docker Compose. Секреты установщика — случайные строки из 48 шестнадцатеричных символов. `.env` имеет права 600.

| Переменная | Начальное значение | Назначение |
|---|---|---|
| DOMAIN | аргумент install.sh | Домен для Nginx и сертификата; не является настройкой приложения |
| MARIADB_DATABASE | ytdlp | Имя базы; буквы/цифры/подчёркивание, начинается с буквы, до 32 символов |
| MARIADB_USER | ytdlp | Пользователь приложения; тот же формат |
| MARIADB_ROOT_PASSWORD | генерируется | Пароль root MariaDB |
| MARIADB_PASSWORD | генерируется | Пароль пользователя приложения MariaDB |
| WORKER_API_TOKEN | генерируется | Общий секрет backend, worker и cleanup; также участвует в подписи сессий |
| DOWNLOAD_ACCESS_WORD | генерируется | Пароль для скачивания |
| QUEUE_ACCESS_WORD | генерируется | Пароль доступа к очереди и скачиванию; должен отличаться от DOWNLOAD_ACCESS_WORD |
| JOB_TTL_MINUTES | 30 | Срок хранения готового задания в минутах; период очистки задаётся CLEANUP_INTERVAL_SECONDS (по умолчанию 60 секунд) |
| POLL_INTERVAL | 5 | Пауза опроса очереди воркером, секунды |
| VIDEO_PLAYLIST_LIMIT | 50 | Максимум элементов видеоплейлиста |
| AUDIO_PLAYLIST_LIMIT | 100 | Максимум элементов аудиоплейлиста |
| MIN_FREE_GB | 2 | Минимальный остаток диска в GiB; ниже него воркер приостанавливает очередь/останавливает обработку |
| MAX_JOB_GB | 2 | Максимум места одного задания в GiB, включая временные файлы; проверяется периодически |
| DISK_CHECK_SECONDS | 1 | Интервал контроля диска, секунды |
| RETRY_MAX_SECONDS | 60 | Верхняя граница паузы повторной доставки результатов и восстановления состояния через backend |

Числовые значения должны быть положительными; TTL, интервал опроса и лимиты плейлистов — целые числа. Дисковые ограничения не являются жёсткой квотой: между проверками возможен небольшой выход за лимит.

Фиксированные внутренние переменные в `compose.yaml`:

| Переменная | Значение | Где используется |
|---|---|---|
| DB_HOST | mariadb | backend: имя сервиса БД |
| DB_PORT | 3306 | backend: внутренний порт БД, не публикуется наружу |
| JOBS_ROOT | /data/ytdlp/jobs | backend: файлы заданий, read-only |
| API_URL | http://backend:8000 | worker и cleanup: внутренний API, не менять на публичный HTTPS-домен |
| JOBS_DIR | /data/ytdlp/jobs | worker и cleanup: каталог файлов внутри контейнера |

Фактические файлы на хосте находятся в `./jobs`, база — в `./mariadb/data`; оба пути относительно проекта. Менять внутренние пути следует согласованно с Docker volumes и Nginx alias. Воркер использует `/usr/local/bin/yt-dlp`, `/usr/bin/ffmpeg` и `/usr/bin/ffprobe` внутри образа; Node.js 22 и JS runtime уже настроены для yt-dlp.

`frontend/config.js`: `ENABLE_WEBSITE_PLAY=false` управляет отображением воспроизведения в обычном браузере; это настройка JavaScript, а не переменная `.env`.

После изменения обычных параметров:

```bash
python3 scripts/configure.py domain   # проверяет .env, выводит домен
# restart недостаточно: новые environment применяются при пересоздании
 docker compose up -d
```

Пароли MariaDB из environment используются при **первичной инициализации пустой базы**. Изменение `.env` не меняет пароль уже существующего пользователя БД; для этого нужна отдельная SQL-операция. Смена токена/паролей доступа сбрасывает действительность сохранённых сессий.

## 4. HTTPS — автоматическая настройка

Сначала должен работать HTTP из шага 2. Отдельный `compose.bootstrap.yaml` не требуется: текущий HTTP-конфиг уже обслуживает `/.well-known/acme-challenge/`.

Установить Certbot (как на исходном сервере):

```bash
apt update
apt install -y snapd
snap install --classic certbot
# Если команда certbot ещё недоступна и путь свободен:
command -v certbot >/dev/null || ln -s /snap/bin/certbot /usr/local/bin/certbot
```

Если Certbot уже установлен, повторная установка не нужна. Затем:

```bash
cd /opt/ytdlp-web
./enable-https.sh
```

Скрипт вызывает `certbot certonly --webroot`, интерактивно запрашивает email/согласие средствами Certbot, проверяет сертификаты, сохраняет резервную копию Nginx, генерирует HTTPS-конфиг, проверяет его одноразовым контейнером и **пересоздаёт Nginx**. При ошибке теста возвращает старый конфиг. При ошибке запуска пытается восстановить старый контейнер. Ошибка внешней проверки HTTPS не откатывает уже применённую конфигурацию: смотреть логи и DNS.

Порты 80/443 и read-only volume `/etc/letsencrypt` уже включены в compose.yaml. До включения HTTPS на 443 нет TLS-listener. HTTP после включения HTTPS перенаправляет на домен, кроме ACME-проверки. Внутренние worker/cleanup продолжают работать через backend напрямую.

Скрипт устанавливает `/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx`, запускает его и проверяет продление с `--dry-run --run-deploy-hooks`. Обычный dry-run без последнего флага не проверяет deploy hook. Snap Certbot настраивает расписание продления; проверить его командой `snap services certbot` и `systemctl list-timers --all`.

## 5. HTTPS — ручные команды / восстановление

Если хочется выполнить шаги отдельно:

```bash
cd /opt/ytdlp-web
# Заменить домен своим
certbot certonly --webroot -w /opt/ytdlp-web/certbot/www \
  --cert-name video.example.com -d video.example.com

cp nginx/nginx.conf nginx/nginx.conf.before-https
python3 scripts/configure.py https

docker compose config --quiet
docker compose run --rm --no-deps nginx nginx -t
# Только после успешной проверки:
docker compose up -d --no-deps --force-recreate nginx
curl --fail https://video.example.com/health
```

Ожидается `{"status":"ok"}`. В конфиге должны быть два server-блока: HTTP/ACME/redirect и HTTPS с `ssl_certificate`, `ssl_certificate_key`, TLSv1.2/TLSv1.3 и всеми существующими location для API, SPA, отдельных файлов и ZIP. Шаблоны находятся в `nginx/`. Обычный запуск `certbot` не настраивает наш контейнер автоматически. Сообщение entrypoint `can not modify ... read-only` допустимо: файл намеренно read-only; важен результат `nginx -t`.

Установить hook (при стандартном пути проекта):

```bash
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx <<'HOOK'
#!/bin/sh
set -eu
cd /opt/ytdlp-web
/usr/bin/docker compose exec -T nginx nginx -t
/usr/bin/docker compose exec -T nginx nginx -s reload
HOOK
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx
/etc/letsencrypt/renewal-hooks/deploy/ytdlp-nginx
certbot renew --cert-name video.example.com --dry-run --run-deploy-hooks
```

Первая строка — строго `#!/bin/sh`, без обратного слеша перед `#`. Каталог — `renewal-hooks/deploy`, без повторения фрагментов. Если проект расположен иначе, исправить `cd`. Если переносить проект после установки, обновить hook.

Сертификаты: `/etc/letsencrypt/live/ДОМЕН/fullchain.pem` и `privkey.pem`. Монтируется весь `/etc/letsencrypt`, потому что файлы в `live` — ссылки на `archive`. Не копировать только два symlink-файла.

## 6. Управление и диагностика

```bash
cd /opt/ytdlp-web
docker compose ps
./scripts/diagnose.sh
./scripts/logs.sh worker -f
./scripts/queue-errors.sh
./scripts/queue-errors.sh abc123def4 --since 24h --tail 10000
```

Поиск ошибок — по 10-символьному коду задания. Описание в [scripts/README.md](../scripts/README.md). После HTTPS диагностика использует внутренний backend health endpoint (отдельно проверять публичный HTTPS через curl). Логи контейнеров ограничены 3 файлами по 10 МБ на сервис; старые сообщения могут быть ротированы.

Контейнеры: MariaDB, backend/FastAPI, Nginx, worker/yt-dlp/FFmpeg, cleanup. Очистка в контейнере каждые 300 секунд, systemd-службы старого проекта не нужны. `docker compose down` останавливает также worker и cleanup. `restart: unless-stopped` обеспечивает запуск контейнеров после перезапуска Docker, если они не были вручную остановлены.

Переустановка не обнуляет базу. Для нового пустого развёртывания устанавливать проект на чистый сервер в пустой каталог. Не удалять `mariadb/data` работающего сервера ради «очистки».

## 7. Ограничения и источники

В обычном браузере HTTP доступен; сохранённый вход установленного Web App требует HTTPS (Secure cookie). Доступность конкретного сайта для yt-dlp зависит от IP сервера и ограничений источника. Проект может отклонить прямой MP4 без метаданных высоты из-за своего фильтра качества. Приложение сохраняет это поведение.

Образы используют теги, Python-зависимости и yt-dlp устанавливаются при сборке без полной фиксации версий; повторная сборка позднее может получить обновления. После обновления проверять скачивание и логи.

Официальные инструкции:
- Docker: https://docs.docker.com/engine/install/ubuntu/
- Certbot webroot и renew: https://eff-certbot.readthedocs.io/en/stable/using.html

## Необязательный Telegram-бот

Профиль `telegram` добавляет личного Telegram-бота через общий API, MariaDB и worker.
По умолчанию он отключён; установщик не требует Telegram-токенов. Существующие
.env не переписываются. Настройка переменных, явная добавочная миграция, безопасность
исходящих загрузок, включение/отключение и откат описаны в [TELEGRAM.md](TELEGRAM.md).
Перед обновлением production обязательно проверьте тесты, резервную копию и
локальные отличия Compose/Nginx. Для production FINN нужны отдельные разрешения.

## Единый файл настроек

Настройки всех сервисов, включая Telegram и лимиты, задаются только в корневом
`.env`; полный перечень приведён в [`.env.example`](../.env.example).
Файлы worker/config.env, cleanup/config.env и отдельные Telegram env-файлы не
используются. Проверка: `python3 scripts/configure.py check`, затем
`docker compose config --quiet`. Применение: `docker compose up -d --wait`.
Telegram включается значением `COMPOSE_PROFILES=telegram`.
[Справка и параметры Telegram](TELEGRAM.md).
