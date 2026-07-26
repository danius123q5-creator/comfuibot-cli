# comfuibot — console-side bot

Консольный клиент к API [ComfyBot](https://t.me/comfuibot). Открыл PowerShell,
вставил ключ — и работаешь: чат с ИИ, генерация и правка фото, кодер-агент,
который **сам запускает код**.

## Установка (pip с гита)

```bash
pip install git+https://github.com/danius123q5-creator/comfuibot-cli.git
```

Зависимостей нет — только стандартная библиотека Python 3.8+.

Обновиться:

```bash
pip install --upgrade --force-reinstall git+https://github.com/danius123q5-creator/comfuibot-cli.git
```

## Быстрый старт

```bash
comfuibot api auth        # вставить API-ключ (получить в боте: /apikey)
comfuibot api status      # жив ли сервер
comfuibot photo gen "закат над морем, кинематографично"
```

## Режимы console-side

| Команда | Что делает |
|---|---|
| `comfuibot api status` | сервер жив? polling, uptime, есть ли ключ |
| `comfuibot api auth` | вставить/сменить ключ (хранится в `~/.comfuibot/config.json`) |
| `comfuibot api usage` | тариф, срок, лимиты, расход по ключу |
| `comfuibot chat` | интерактивный чат с ИИ (или `chat "вопрос"` одной строкой) |
| `comfuibot photo gen "промпт"` | сгенерировать картинку (txt2img) |
| `comfuibot photo edit файл.png "что изменить"` | переделать картинку (img2img) |
| `comfuibot coder ai "задача"` | ИИ напишет и **ЗАПУСТИТ** код, покажет вывод |
| `comfuibot enter` | открыть TG-side бота |

### Примеры

```bash
# чат: одним вопросом и интерактивно
comfuibot chat "объясни, что такое диффузионная модель"
comfuibot chat --style tehpod

# фото
comfuibot photo gen "неоновый Токио в дождь" --steps 30 --size 1024
comfuibot photo edit my.png "сделай зиму и вечер"

# кодер: код реально выполняется на сервере
comfuibot coder ai "посчитай 200-е число Фибоначчи"
comfuibot coder ai                 # интерактивный режим
```

Картинки сохраняются в `~/comfuibot-out` и открываются автоматически
(`--no-open` отключает).

## Настройки

| Переменная | Смысл |
|---|---|
| `COMFUIBOT_KEY` | ключ (приоритетнее конфига) |
| `COMFUIBOT_URL` | адрес API, по умолчанию `http://127.0.0.1:8090` |
| `COMFUIBOT_OUT` | куда складывать картинки |
| `COMFUIBOT_TG` | ссылка на TG-бота для `enter` |

Адрес можно задать и флагом: `comfuibot --url https://api.example.com api status`

## Ключи и география

Ключ выдаёт сам бот: `/apikey` → выбор языка → тариф → **Фичи API** → тип ключа.
Типы: картинки, чат/NPC, аудио, всё сразу, **Кодер** (нужен для `coder ai`).

⚠️ У API есть гео-фильтр: он не работает из ряда регионов (Африка, Китай, Иран,
Ирак, Сирия, КНДР, Палестина, Мексика, Венесуэла) — оттуда придёт
`403 REGION_BLOCKED`. Россия, Европа, СНГ, США и остальной мир работают.
