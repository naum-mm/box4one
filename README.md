# BOX4ONE

**GUI-интерфейс для yt-dlp.** Скачивание видео и аудио через браузер: выбор качества, очередь заданий, выдача готовых файлов и автоматическая очистка.

- Видео: 480p, 720p, 1080p. Аудио: MP3 128/320 кбит/с.
- Доступ по паролю, отдельный пароль для просмотра очереди.
- Docker Compose: веб-интерфейс, API, база задач, воркер и очистка.
- HTTPS через Certbot, диагностика и просмотр ошибок по коду задания.

## Установка одной командой

Нужен VPS с **Ubuntu 24.04**, root/sudo, установленным `curl`, доменом и открытыми портами 80/443. Рекомендуется минимум 2 ГБ RAM и 20 ГБ диска. Обычный shared hosting без Docker не подходит.

Замените `video.example.com` своим доменом и выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/naum-mm/box4one/main/bootstrap.sh -o /tmp/box4one-install.sh && sudo bash /tmp/box4one-install.sh video.example.com
```

Скрипт загрузит проект в `/opt/ytdlp-web`, установит недостающий Docker, создаст новые секреты и пустую базу, затем запустит приложение по HTTP. Существующий каталог не перезаписывается. Если `curl` отсутствует: `sudo apt update && sudo apt install -y curl`.

Пароли находятся в `/opt/ytdlp-web/.env`: `DOWNLOAD_ACCESS_WORD` — скачивание, `QUEUE_ACCESS_WORD` — очередь. Рабочих паролей и пользовательских файлов в репозитории нет.

## HTTPS

Направьте A-запись домена на VPS; если есть AAAA, проверьте её тоже. Установите Certbot, если он ещё не установлен:

```bash
sudo apt update && sudo apt install -y snapd
sudo snap install --classic certbot
cd /opt/ytdlp-web
sudo ./enable-https.sh
```

Скрипт выпустит сертификат, включит HTTPS, настроит перечитывание сертификата Nginx после продления и проверит продление вместе с hook. Certbot спросит email и согласие с условиями.

## Настройка и управление

- [Telegram-бот: настройка, ограничения и включение](docs/TELEGRAM.md)
- [Полное руководство и все переменные](docs/SETUP.md)
- [Диагностика, логи и ошибки заданий](scripts/README.md)
- [Остановка и полное удаление](docs/UNINSTALL.md)

```bash
cd /opt/ytdlp-web
sudo ./scripts/diagnose.sh
sudo ./scripts/logs.sh worker -f
sudo ./scripts/queue-errors.sh
sudo ./scripts/queue-errors.sh abc123def4
```

## Удаление

Остановить проект с сохранением файлов и базы:

```bash
cd /opt/ytdlp-web && sudo ./uninstall.sh
```

Удалить также проект, пароли, базу и загрузки (потребуется ввести `DELETE`):

```bash
cd /opt/ytdlp-web && sudo ./uninstall.sh --purge
```

Docker, его образы, Certbot и сертификаты автоматически не удаляются. Подробности — в [инструкции удаления](docs/UNINSTALL.md).
