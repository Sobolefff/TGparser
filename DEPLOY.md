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
> «Маркетплейс закрыл доступ к карточке». Берите VPS в российской локации (Timeweb,
> Selectel, Beget, Reg.ru, VDSina и т. п.) и заранее проверьте доступ командой из
> [шага 8](#шаг-8-проверка-сетевого-доступа). Учтите: серверные диапазоны часто
> блокируются и в России — тогда понадобится `PARSER_PROXY`, см.
> [раздел про 403](#маркетплейсы-отвечают-403).

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

## Шаг 8. Проверка сетевого доступа

Боту нужны две разные сети: **Telegram** — чтобы вообще заработать, и **маркетплейсы** —
чтобы собирать карточки. Проверьте обе **до** сборки, это экономит час на разборе логов.

```bash
# 1. Telegram
curl -s -m 10 -o /dev/null -w "Telegram: %{http_code}\n" https://api.telegram.org

# 2. Маркетплейсы
curl -s -m 10 -o /dev/null -w "WB:       %{http_code}\n" "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm=171796130"
curl -s -m 10 -o /dev/null -w "Ozon:     %{http_code}\n" "https://www.ozon.ru/"
curl -s -m 10 -o /dev/null -w "Yandex:   %{http_code}\n" "https://market.yandex.ru/"
```

Как читать результат:

| Строка | Значение | Что делать |
| --- | --- | --- |
| `Telegram: 200` | всё хорошо | ничего |
| `Telegram: 000`, команда висела все 10 секунд | исходящее соединение до Telegram не проходит | [настроить прокси](#если-telegram-недоступен-прокси) |
| `WB` / `Ozon` / `Yandex: 200` | всё хорошо | ничего |
| `WB` / `Ozon` / `Yandex: 403` | IP сервера заблокирован WAF маркетплейса | [раздел про 403](#маркетплейсы-отвечают-403) |

Бот запустится в любом случае, но без Telegram он не ответит вообще, а без маркетплейсов
будет отвечать «Маркетплейс закрыл доступ к карточке» на каждую ссылку.

Если Telegram отдал `000`, проверьте заодно, не в сломанном ли IPv6 дело:

```bash
curl -s -m 10 -4 -o /dev/null -w "IPv4: %{http_code}\n" https://api.telegram.org
curl -s -m 10 -6 -o /dev/null -w "IPv6: %{http_code}\n" https://api.telegram.org
```

Если по IPv4 приходит `200`, а IPv6 висит — у сервера объявлен нерабочий IPv6-маршрут.
Проще всего попросить хостера убрать его или отключить IPv6 на интерфейсе.

---

## Если Telegram недоступен: прокси

Симптом — в логах при старте:

```
ERROR | tgparser | Telegram API is unreachable (HTTP Client says - Request timeout error).
Polling will keep retrying. Check outbound access to api.telegram.org from this host,
or set TELEGRAM_PROXY in .env.
```

Бот при этом не падает: он продолжает стучаться к Telegram с нарастающими паузами и оживёт
сам, когда связь появится. Но пока соединения нет, в чате он молчит.

Здесь возникает неприятная вилка: маркетплейсам нужен российский IP, а с части российских
хостингов не открывается `api.telegram.org`. Поэтому прокси в боте применяется **только к
Telegram** — парсеры продолжают ходить напрямую и сохраняют локальный IP.

### Настройка

1. Возьмите SOCKS5- или HTTP-прокси на зарубежном адресе: поднимите свой на дешёвом
   зарубежном VPS (`3proxy`, `dante-server`), купите готовый или спросите у хостера.

2. Проверьте его прямо с сервера бота:

   ```bash
   curl -s -m 10 --socks5-hostname ЛОГИН:ПАРОЛЬ@IP:ПОРТ \
        -o /dev/null -w "via proxy: %{http_code}\n" https://api.telegram.org
   ```

   Нужен `200`. Для HTTP-прокси вместо `--socks5-hostname` используйте `-x http://IP:ПОРТ`.

3. Пропишите прокси в `.env`:

   ```bash
   nano .env
   ```

   ```ini
   TELEGRAM_PROXY=socks5://ЛОГИН:ПАРОЛЬ@IP:ПОРТ
   ```

   **Про порт:** подставляйте тот, что выдал провайдер, — любой. `1080` и `3128` в примерах
   это просто традиция, ничего в коде к ним не привязано:

   ```ini
   TELEGRAM_PROXY=socks5://1.2.3.4:41234
   TELEGRAM_PROXY=socks5://user:pass@proxy.example.com:9999
   TELEGRAM_PROXY=http://1.2.3.4:58080
   ```

   Ограничения ровно два:

   - **порт обязателен** — по умолчанию он не подставляется;
   - **схема** только `socks5://`, `socks4://` или `http://`. Варианты `socks5h://`,
     `https://` и адрес без схемы (`1.2.3.4:1080`) не принимаются — если провайдер выдал
     `socks5h://`, просто замените на `socks5://`, поведение то же (DNS резолвится на
     стороне прокси).

   Логин с паролем нужны, только если прокси их требует.

   Ошибку в этой строке бот показывает на старте понятным текстом, например
   `TELEGRAM_PROXY must include an explicit port`.

4. Пересоздайте контейнер — простой `restart` не перечитывает `.env`:

   ```bash
   docker compose up -d
   docker compose logs -f bot
   ```

В логах должно появиться `Telegram API calls are routed through a proxy`, а следом —
`bot @ваш_бот is up and polling`.

> Если прокси со временем ляжет, бот не упадёт: ошибки прокси транслируются в обычную
> сетевую ошибку Telegram и попадают в штатные ретраи polling.

---

## Маркетплейсы отвечают 403

Симптом — бот жив и отвечает на `/start`, но на любую ссылку пишет:

> Маркетплейс закрыл доступ к карточке (антибот). Попробуйте через пару минут.

### 1. Выясните, какой именно маркетплейс и что он отдаёт

```bash
cd ~/TGparser
docker compose exec bot python scripts/check_access.py
```

Скрипт прогоняет настоящие парсеры по эталонным ссылкам и печатает вердикт:

```
Доступность хостов
card.wb.ru        BLOCKED     HTTP 403 at https://card.wb.ru/... (server=wbaas)
www.ozon.ru       BLOCKED     HTTP 403 at https://www.ozon.ru/ (server=nginx)
market.yandex.ru  HTTP 200    2129218 bytes

Разбор карточек
Wildberries    BLOCKED     HTTP 403 ...
Ozon           BLOCKED     HTTP 403 ...
```

- `BLOCKED` — WAF маркетплейса отклоняет IP сервера. Это **не** лечится настройками бота.
- `ERROR` / `ParserResponseError` — доступ есть, но изменилась схема ответа: чинить нужно
  соответствующий модуль в `parsers/`.
- `OK` — парсинг работает, проблема в конкретной ссылке.

### 2. Если `BLOCKED` — почему так происходит

Wildberries и Ozon блокируют диапазоны хостинг-провайдеров целиком. IP дата-центра
выглядит для их WAF как бот независимо от заголовков и TLS-отпечатка, поэтому `403`
приходит одинаково и на дешёвый зарубежный VPS, и на российский серверный IP.

Обходные пути на уровне кода проверены и не работают — не тратьте на них время:

| Что пробовали | Результат |
| --- | --- |
| `card.wb.ru` v1 / v2 / v4, `u-card.wb.ru` | `403 wbaas` |
| `search.wb.ru`, `catalog.wb.ru`, `recom.wb.ru` | `403 wbaas` |
| `www.wildberries.ru` (HTML) | `498` (антибот-челлендж) |
| `api.ozon.ru`, `www.ozon.ru/api/composer-api.bx`, `entrypoint-api.bx` | `403 nginx` + `incidentId` |
| `ir.ozone.ru` (картинки Ozon) | `403` |
| Смена профиля impersonate, User-Agent Googlebot | без изменений |
| `basket-NN.wbbasket.ru` (статический CDN WB) | `200` — но там только название и фото, **без цены** |

Блокировка по IP, а не по отпечатку клиента. Вариантов ровно два.

**Вариант А — прокси для парсинга.** В боте это отдельная настройка, не связанная с
`TELEGRAM_PROXY`: Telegram может ходить через один выход, маркетплейсы через другой.
Нужен российский резидентный или мобильный прокси — серверный не поможет, он упрётся в тот
же блок.

```bash
nano .env
```

```ini
PARSER_PROXY=socks5://ЛОГИН:ПАРОЛЬ@IP:ПОРТ
```

Требования к строке те же, что у `TELEGRAM_PROXY`: схема `socks5://`, `socks4://` или
`http://`, порт обязателен. Маршрутизация проверена: через `PARSER_PROXY` уходят все
запросы к маркетплейсам (включая скачивание фото), а Telegram продолжает ходить своим
путём. Затем:

```bash
docker compose up -d
docker compose exec bot python scripts/check_access.py   # должно стать OK
```

**Вариант Б — сменить хостинг.** Помогают провайдеры, выдающие IP из «жилых» диапазонов.
Гарантий нет: проверяйте через `scripts/check_access.py` сразу после установки.

> Проверить прокси, не трогая бота:
>
> ```bash
> curl -s -m 15 --socks5-hostname ЛОГИН:ПАРОЛЬ@IP:ПОРТ -o /dev/null \
>      -w "WB via proxy: %{http_code}\n" \
>      "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm=171796130"
> ```
>
> Нужен `200`.

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
| `Telegram API is unreachable` / `TelegramNetworkError: Request timeout error` | с сервера не проходит соединение до `api.telegram.org` | [настроить прокси](#если-telegram-недоступен-прокси) |
| `proxy failure — ProxyConnectionError` | прокси из `TELEGRAM_PROXY` не отвечает | проверить его командой `curl` из раздела про прокси, поправить `.env`, `docker compose up -d` |
| `TelegramConflictError: terminated by other getUpdates` | тот же токен уже используется другим запущенным ботом | остановить вторую копию (локальную или на другом сервере) — на один токен допустим один polling |
| «Маркетплейс закрыл доступ к карточке» на любую ссылку | IP сервера заблокирован WAF маркетплейса | `docker compose exec bot python scripts/check_access.py`, далее [раздел про 403](#маркетплейсы-отвечают-403) |
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
