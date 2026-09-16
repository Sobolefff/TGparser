# Деплой на VPS (Ubuntu + Docker)

Пошаговая инструкция: от только что купленного сервера до работающего бота, который
переживает перезагрузку. Все команды рассчитаны на **Ubuntu 22.04 / 24.04 LTS**.

---

## 0. Что понадобится

| Что | Минимум | Комментарий |
| --- | --- | --- |
| VPS | 1 vCPU, 1 GB RAM, 10 GB SSD | бот лёгкий: Redis-кэш ограничен 256 MB, картинки не пишутся на диск |
| ОС | Ubuntu 22.04 или 24.04 LTS | на других дистрибутивах поменяется только установка Docker |
| Локация сервера | **Россия или СНГ** | см. предупреждение ниже |
| Токен | от [@BotFather](https://t.me/BotFather) | команда `/newbot` |

> **Важно про локацию.** Wildberries, Ozon и Яндекс Маркет фильтруют трафик по IP.
> С зарубежных дата-центров (особенно Hetzner, DigitalOcean, OVH) их API часто отвечают
> `403 Forbidden` от WAF — бот при этом не падает, но на каждую ссылку отвечает
> «Маркетплейс закрыл доступ к карточке». Это не лечится настройками: берите VPS в
> российской локации (Timeweb, Selectel, Beget, Reg.ru, VDSina и т. п.) либо заранее
> проверьте доступ командой из [шага 8](#шаг-8-проверка-доступа-к-маркетплейсам).

Порты наружу открывать **не нужно**: бот работает через long polling и сам ходит к
Telegram. Входящих соединений он не принимает.

---

## Шаг 1. Первый вход по SSH

Хостер присылает IP-адрес и пароль root. С Windows подойдёт встроенный OpenSSH
(PowerShell) или PuTTY.

```bash
ssh root@ВАШ_IP
```

При первом входе подтвердите отпечаток ключа (`yes`) и, если хостер потребует, смените
пароль root.

---

## Шаг 2. Обновление системы и базовая настройка

```bash
apt update && apt upgrade -y
apt install -y curl git ca-certificates nano

# часовой пояс — чтобы время в логах совпадало с вашим
timedatectl set-timezone Europe/Moscow
```

Если на сервере 1 GB RAM, добавьте swap — это страховка на время сборки образа:

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h            # проверка: в строке Swap должно быть 2.0Gi
```

---

## Шаг 3. Отдельный пользователь вместо root

Работать под root постоянно не стоит. Создаём пользователя `deploy`:

```bash
adduser deploy                  # задайте пароль, остальные поля можно пропустить (Enter)
usermod -aG sudo deploy
```

Скопируйте на него свой SSH-ключ, чтобы заходить без пароля.

**С локальной машины (Windows PowerShell), в новом окне:**

```powershell
# если ключа ещё нет
ssh-keygen -t ed25519 -C "tgparser"

# скопировать публичный ключ на сервер
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@ВАШ_IP "mkdir -p /home/deploy/.ssh && cat >> /home/deploy/.ssh/authorized_keys && chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys"
```

Проверьте вход в отдельном окне, **не закрывая текущую root-сессию**:

```bash
ssh deploy@ВАШ_IP
```

Получилось — запрещаем вход по паролю и под root (на сервере, под root):

```bash
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

> Если вход по ключу не заработал — **не выполняйте** эти две команды, иначе потеряете
> доступ к серверу. Сначала разберитесь с ключом.

---

## Шаг 4. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable            # подтвердите 'y'
sudo ufw status verbose
```

Исходящие соединения открыты — этого боту достаточно. Порт для Redis наружу не нужен:
он доступен только внутри docker-сети.

> Docker умеет обходить ufw для **опубликованных** портов (`ports:`). В нашем
> `docker-compose.yml` публикаций нет, так что конфликта не возникает. Если будете
> добавлять `ports:` — не забудьте про это.

---

## Шаг 5. Установка Docker

Ставим из официального репозитория Docker (в репозитории Ubuntu версия устаревшая):

```bash
# ключ репозитория
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# сам репозиторий
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Разрешаем запускать Docker без `sudo` и включаем автозапуск:

```bash
sudo usermod -aG docker $USER
sudo systemctl enable --now docker
```

Перезайдите по SSH (`exit`, затем снова `ssh deploy@ВАШ_IP`), чтобы новая группа
применилась, и проверьте:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

---

## Шаг 6. Загрузка кода на сервер

**Вариант А — через Git (рекомендуется):**

```bash
cd ~
git clone https://github.com/ВАШ_АККАУНТ/TGparser.git
cd TGparser
```

**Вариант Б — скопировать с локальной машины.** В PowerShell из папки с проектом:

```powershell
scp -r . deploy@ВАШ_IP:~/TGparser
```

> В репозиторий и в копию не должен попадать файл `.env` — он уже в `.gitignore`.

---

## Шаг 7. Настройка `.env`

```bash
cd ~/TGparser
cp .env.example .env
nano .env
```

Обязательно заполнить одну строку:

```ini
BOT_TOKEN=123456789:AA...ваш_токен_от_BotFather
```

`REDIS_URL` править не нужно: в `docker-compose.yml` он принудительно выставлен в
`redis://redis:6379/0`. Остальные параметры описаны в [README](README.md#конфигурация).

Сохранить: `Ctrl+O`, `Enter`, выйти: `Ctrl+X`. Закрываем файл от посторонних:

```bash
chmod 600 .env
```

---

## Шаг 8. Проверка доступа к маркетплейсам

Разовая проверка прямо с сервера — стоит сделать **до** сборки, чтобы не удивляться
потом:

```bash
curl -s -o /dev/null -w "WB:     %{http_code}\n" "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm=171796130"
curl -s -o /dev/null -w "Ozon:   %{http_code}\n" "https://www.ozon.ru/"
curl -s -o /dev/null -w "Yandex: %{http_code}\n" "https://market.yandex.ru/"
```

`200` — всё в порядке. `403` — IP сервера заблокирован маркетплейсом, нужен VPS в другой
локации (см. предупреждение в начале). Бот при этом запустится и будет отвечать на
команды, но карточки собирать не сможет.

---

## Шаг 9. Сборка и запуск

```bash
cd ~/TGparser
docker compose up -d --build
```

Первая сборка занимает 2–5 минут. Дальше:

```bash
docker compose ps                    # оба сервиса должны быть Up, redis — healthy
docker compose logs -f bot           # выход из просмотра логов — Ctrl+C
```

В логах при успешном старте будет:

```
redis is reachable, cards are cached for 3600 seconds
bot @ваш_бот is up and polling
```

Теперь откройте бота в Telegram, отправьте `/start` и ссылку на товар.

---

## Шаг 10. Автозапуск после перезагрузки

Дополнительная настройка не нужна: у обоих сервисов стоит `restart: unless-stopped`, а
демон Docker включён через `systemctl enable`. Проверить можно честно:

```bash
sudo reboot
# подождать ~30 секунд, зайти снова
ssh deploy@ВАШ_IP
docker compose -f ~/TGparser/docker-compose.yml ps
```

Оба контейнера должны подняться сами.

---

## Ежедневная эксплуатация

Все команды выполняются из каталога `~/TGparser`.

| Задача | Команда |
| --- | --- |
| Статус сервисов | `docker compose ps` |
| Логи в реальном времени | `docker compose logs -f bot` |
| Последние 200 строк | `docker compose logs --tail=200 bot` |
| Перезапустить бота | `docker compose restart bot` |
| Остановить всё | `docker compose down` |
| Поднять обратно | `docker compose up -d` |
| Потребление ресурсов | `docker stats --no-stream` |
| Зайти внутрь контейнера | `docker compose exec bot sh` |
| Очистить кэш карточек | `docker compose exec redis redis-cli FLUSHDB` |
| Сколько карточек в кэше | `docker compose exec redis redis-cli DBSIZE` |

### Обновление до новой версии

```bash
cd ~/TGparser
git pull
docker compose up -d --build
docker image prune -f          # удалить старые слои, освободить диск
```

Перерыв в работе — несколько секунд на пересоздание контейнера.

### Изменение настроек

После правки `.env` контейнер нужно пересоздать (перезапуск не перечитывает файл):

```bash
nano .env
docker compose up -d
```

---

## Диагностика

| Симптом в логах / поведении | Причина | Что делать |
| --- | --- | --- |
| `TelegramUnauthorizedError` при старте | неверный `BOT_TOKEN` | проверить токен в `.env`, затем `docker compose up -d` |
| `TelegramConflictError: terminated by other getUpdates` | тот же токен уже используется другим запущенным ботом | остановить вторую копию (локальную или на другом сервере) — на один токен допустим один polling |
| «Маркетплейс закрыл доступ к карточке» на любую ссылку | IP сервера заблокирован WAF маркетплейса | выполнить проверку из [шага 8](#шаг-8-проверка-доступа-к-маркетплейсам); помогает только смена локации VPS |
| «Маркетплейс вернул неожиданный ответ» | маркетплейс поменял схему API или вёрстку | посмотреть трейс `docker compose logs bot`, поправить соответствующий модуль в `parsers/` |
| `redis is unreachable — running without cache` | контейнер `redis` не поднялся | `docker compose ps`, `docker compose logs redis`, затем `docker compose up -d redis` |
| Контейнер `bot` в статусе `Restarting` | падение на старте | `docker compose logs --tail=50 bot` — там будет исключение |
| Сборка падает с `Killed` / нехватка памяти | мало RAM | добавить swap ([шаг 2](#шаг-2-обновление-системы-и-базовая-настройка)) |
| Кончилось место на диске | накопились старые образы | `docker system df`, затем `docker system prune -a -f` (осторожно: удалит неиспользуемые образы) |

Полезное для разбора:

```bash
docker compose logs --since 30m bot     # что было за последние полчаса
journalctl -u docker --since "1 hour ago" --no-pager | tail -50
df -h && free -h                        # диск и память
```

---

## Гигиена и безопасность

- **`.env` не коммитим и не копируем в публичные места** — там токен бота. Утёк токен —
  отозвать через `/revoke` у @BotFather.
- **Ротация логов настроена** в `docker-compose.yml` (`max-size: 10m`, `max-file: 3`),
  так что логи не съедят диск.
- **Автообновления безопасности** для системы:

  ```bash
  sudo apt install -y unattended-upgrades
  sudo dpkg-reconfigure -plow unattended-upgrades
  ```

- **Защита SSH от перебора** (после шага 3 вход по паролю уже отключён, но не помешает):

  ```bash
  sudo apt install -y fail2ban
  sudo systemctl enable --now fail2ban
  ```

- **Бэкап не нужен**: Redis используется только как кэш (`--save ""`, `appendonly no`),
  данные восстанавливаются сами. Ценность представляет только `.env` — сохраните токен
  в своём менеджере паролей.

---

## Запуск нескольких ботов на одном сервере

Каждому боту — свой каталог, свой `.env` с отдельным токеном и своё имя проекта, чтобы
контейнеры и сети не конфликтовали:

```bash
cd ~/TGparser-second
docker compose -p tgparser-second up -d --build
```
