# TGparser

Telegram-бот, который превращает ссылку с маркетплейса в карточку товара: фото, цена,
скидка, рейтинг, продавец и кнопка «Открыть в магазине».

Поддерживаются **Wildberries**, **Ozon** и **Яндекс Маркет**.

## Стек

| Задача | Инструмент |
| --- | --- |
| Бот | `aiogram 3.x` (long polling) |
| Пакеты | `uv` |
| Обход TLS-фингерпринта | `curl_cffi` (Chrome impersonate) |
| HTTP / HTML | `httpx`, `selectolax` |
| Валидация | `pydantic v2`, `pydantic-settings` |
| Кэш | `redis.asyncio`, ключ `product:<md5(url)>`, TTL 1 час |

## Архитектура

```
main.py                 точка входа: DI-сборка и dp.start_polling()
config.py               Settings (pydantic-settings, .env)

bot/
  __init__.py           create_bot() / create_dispatcher()
  session.py            HTTP-сессия Telegram: прокси + маппинг ошибок прокси
  texts.py              копирайт и рендер HTML-подписи
  keyboards.py          inline-кнопка на страницу товара
  filters/marketplace.py  вытаскивает поддерживаемую ссылку из сообщения
  middlewares/throttling.py  пер-юзерный rate limit на Redis
  handlers/             /start, /help, обработка ссылки, глобальный error handler

parsers/
  base.py               абстрактный BaseParser (паттерн «Стратегия»)
  wb.py                 Wildberries через card.wb.ru JSON API
  ozon.py               Ozon через composer API + curl_cffi impersonate
  yandex.py             Яндекс Маркет через JSON-LD на server-rendered странице
  dispatcher.py         маршрутизация URL → стратегия
  http.py               общий транспорт (httpx + curl_cffi), маппинг ошибок
  utils.py              парсинг цен/рейтингов, JSON-LD, чистка URL

schemas/product.py      ProductInfo (pydantic v2)
services/
  cache.py              ProductCache поверх redis.asyncio
  images.py             скачивание картинки и конвертация WebP → JPEG
  product_service.py    cache-aside фасад для хэндлеров
```

Поток обработки одной ссылки:

```
Message → MarketplaceLinkFilter → ProductService.get_card()
             ├─ Redis hit  → ProductInfo
             └─ Redis miss → ParserDispatcher.resolve() → BaseParser.parse() → Redis set
          → ImageFetcher (WebP → JPEG) → reply_photo(caption HTML + inline button)
```

Пока идёт парсинг, бот держит статус `ChatAction.UPLOAD_PHOTO`.

## Запуск локально

Нужен Python 3.12, [`uv`](https://docs.astral.sh/uv/) и запущенный Redis.

```bash
cp .env.example .env         # и подставить BOT_TOKEN от @BotFather
uv sync                      # создаст .venv и поставит зависимости
uv run main.py
```

Redis для локального запуска проще всего поднять отдельно:

```bash
docker run -d --name tgparser-redis -p 6379:6379 redis:7-alpine
```

## Запуск в Docker

```bash
cp .env.example .env         # BOT_TOKEN обязателен; REDIS_URL переопределит compose
docker compose up -d --build
docker compose logs -f bot
```

Compose поднимает два сервиса: `redis` (healthcheck, `allkeys-lru`, кэш без записи на
диск) и `bot` (собирается из `Dockerfile`, работает от непривилегированного пользователя).
У обоих `restart: unless-stopped` и ротация логов, так что после перезагрузки сервера они
поднимаются сами, а логи не забивают диск.

Порты наружу не публикуются: бот работает через long polling и входящих соединений не
принимает.

## Деплой на VPS

Пошаговая инструкция для Ubuntu-сервера — от первого входа по SSH до автозапуска и
разбора типовых поломок: **[DEPLOY.md](DEPLOY.md)**.

Коротко, если сервер уже настроен и Docker установлен:

```bash
git clone <репозиторий> ~/TGparser && cd ~/TGparser
cp .env.example .env && nano .env    # вписать BOT_TOKEN
chmod 600 .env
docker compose up -d --build
docker compose logs -f bot
```

Обновление до новой версии:

```bash
cd ~/TGparser && git pull
docker compose up -d --build && docker image prune -f
```

> **Локация сервера важна, причём в обе стороны.** Маркетплейсы фильтруют трафик по IP:
> с зарубежных дата-центров их API часто отвечают `403`. А с некоторых российских хостингов,
> наоборот, не открывается `api.telegram.org` — тогда в логах будет
> `Telegram API is unreachable`, и нужен `TELEGRAM_PROXY` (прокси применяется только к
> Telegram, парсинг остаётся с локального IP). Обе проверки — одной командой до деплоя,
> см. [DEPLOY.md](DEPLOY.md#шаг-8-проверка-сетевого-доступа).

## Конфигурация

Все переменные читаются из окружения или `.env` (см. `.env.example`):

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `BOT_TOKEN` | — | токен от @BotFather, обязателен |
| `TELEGRAM_PROXY` | не задан | прокси **только** для вызовов Telegram API: `socks5://user:pass@host:1080` или `http://host:3128`. Парсеры маркетплейсов всегда ходят напрямую |
| `REDIS_URL` | `redis://localhost:6379/0` | адрес Redis |
| `CACHE_TTL` | `3600` | время жизни карточки, секунды |
| `REQUEST_TIMEOUT` | `15.0` | таймаут запроса к маркетплейсу |
| `IMPERSONATE` | `chrome124` | профиль браузера для `curl_cffi` |
| `MAX_IMAGE_BYTES` | `10485760` | лимит на скачиваемое фото |
| `THROTTLE_RATE` | `1.5` | минимальный интервал между сообщениями от юзера |
| `LOG_LEVEL` | `INFO` | уровень логирования |

## Обработка ошибок

Все сбои парсинга — подклассы `ParserError` с полем `user_message`, которое безопасно
показать пользователю:

| Ошибка | Что видит пользователь |
| --- | --- |
| `UnsupportedURLError` | ссылка не с поддерживаемого маркетплейса |
| `ProductNotFoundError` | товар снят с продажи / 404 |
| `MarketplaceBlockedError` | сработал антибот (403, captcha) |
| `ParserTimeoutError` | маркетплейс не ответил вовремя |
| `ParserResponseError` | вёрстка или API изменились |

Неперехваченные исключения ловит error handler на корневом роутере: он пишет трейсбек в
лог, отвечает пользователю нейтральным текстом и не останавливает polling.

Инфраструктурные сбои тоже не роняют процесс:

- **Redis недоступен** — кэш и троттлинг отключаются (fail-open), бот продолжает работать.
- **Telegram недоступен** — на старте это пишется в лог одной понятной строкой, бот уходит
  в polling с ретраями и восстанавливается сам, когда сеть вернётся. Под Docker это
  принципиально: падение на старте превращается в бесконечный рестарт-луп контейнера.
- **Прокси лёг** — `bot/session.py` переводит ошибки `aiohttp_socks` в `TelegramNetworkError`,
  иначе они проходят мимо обработчиков aiogram и убивают процесс.

## Как добавить маркетплейс

1. Создать `parsers/<name>.py` с наследником `BaseParser`: задать `marketplace`, `domains`,
   при необходимости `path_pattern`, реализовать `async def parse(url) -> ProductInfo`.
2. Добавить значение в `Marketplace` (`schemas/product.py`) и подпись кнопки в
   `bot/keyboards.py`.
3. Зарегистрировать класс в `PARSER_CLASSES` (`parsers/dispatcher.py`).

Бот-слой при этом не меняется.

## Разработка

```bash
uv run ruff check .
uv run ruff format .
uv run mypy .
```

## Оговорка

Внутренние API маркетплейсов не документированы и меняются без предупреждения — при
изменении схемы ответа парсер вернёт `ParserResponseError`, и соответствующий модуль
в `parsers/` нужно будет поправить. Парсинг рассчитан на единичные пользовательские
запросы, а не на массовый сбор данных.
