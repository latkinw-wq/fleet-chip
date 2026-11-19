# fleet-chip

Приборка канбан-воркеров и кронов: чип в статусбаре Hermes Desktop. Клик по чипу открывает
панель со списком карточек, правый клик — обновить.

Что он ловит: карточку в `running` с живым heartbeat, у сессии которой `api_call_count = 0` —
процесс воркера жив, но до модели он не дошёл.

## Что читает

Каждый источник открывается только на чтение (`file:<path>?mode=ro`): без записи, без сети,
без запуска процессов. Отсутствующая, заблокированная или битая база становится строкой в
`sources_errors`, а не исключением.

| Источник | Что даёт |
|---|---|
| `kanban.db` и `kanban/*.db` | карточки, статус, heartbeat, исполнитель |
| `profiles/*/state.db` | `api_call_count`, `message_count`, модель сессии воркера |
| `kanban.db` → `task_events` | привязка карточки к сессии, причина блока, последнее событие |
| `cron/jobs.json`, `cron/executions.db` | крон-задачи и исход последнего прогона |

## Состояния

| Состояние | Правило | Проблема |
|---|---|---|
| `lost` | `running`, heartbeat старше `lost_minutes`, либо воркера снял диспетчер | да |
| `never_model` | `running`, heartbeat свежий, `api_call_count = 0` дольше `model_minutes` | да |
| `api_unknown` | `running`, сессии воркера нет в `state.db` — вызовы модели неизмеримы | да |
| `running_silent` | `running`, heartbeat нет дольше `silent_minutes` | да |
| `blocked_ask` | блок с просьбой о решении (свободная причина, `needs_input`, блок после claim) | да |
| `blocked_stale` | блок по зависимостям, все родители закрыты — авто-продвижение не сработало | да |
| `parked_stale` | отложено маркером `[wait]` или статусом `scheduled` дольше `wait_days` | да |
| `not_started` | карточка `ready`/`todo` старше `ready_minutes` | да |
| `working` | `running`, heartbeat свежий, вызовы модели идут | нет |
| `blocked_wait` | ждёт живых родителей, либо создана сразу блокированной | нет |
| `parked` | отложена сознательно, срок не истёк | нет |

`api_unknown` — случай, когда сессии воркера нет в `state.db`: главный сигнал не измерить, а
назвать такую карточку `working` значило бы спрятать зависание.

Крон считается сбоящим, когда последний прогон упал **и** отчёт о нём не дошёл. Задачи-мониторы
выходят с кодом 1, когда нашли проблему: их прогон помечен failed, но доставлен — это работа,
а не поломка.

## Установка

```bash
git clone https://github.com/latkinw-wq/fleet-chip.git
cp -r fleet-chip ~/.hermes/plugins/fleet-chip
mkdir -p ~/.hermes/desktop-plugins/fleet-chip
cp fleet-chip/desktop/plugin.js ~/.hermes/desktop-plugins/fleet-chip/
```

Добавить `fleet-chip` в `plugins.enabled` в `~/.hermes/config.yaml` и перезапустить Hermes
Desktop. Python-половину обслуживает gateway, поэтому обновление плагина требует ещё и
перезапуска gateway.

## Инструмент и команда

- `fleet_status` (toolset `kanban`) отдаёт снимок в JSON.
- `/fleet` печатает тот же снимок текстом.
- `GET /api/plugins/fleet-chip/board` отдаёт тот же JSON Desktop-чипу.

## Настройки

`plugins.entries.fleet-chip.settings`:

| Ключ | По умолчанию | Смысл |
|---|---|---|
| `language` | `auto` | `auto`, `ru` или `en` |
| `ready_minutes` | 15 | `ready`/`todo` старше — не стартовала |
| `silent_minutes` | 3 | `running` без heartbeat дольше — тишина |
| `model_minutes` | 2 | `running` с `api_call_count = 0` дольше — не доехал до модели |
| `lost_minutes` | 20 | `running` без heartbeat дольше — потерян |
| `done_hours` | 6 | окно счётчика «закрыто за N часов» |
| `wait_days` | 14 | отложено дольше — просрочено |
| `board_rows` | 100 | максимум строк в снимке |

## Язык

`language: auto` следует локали системы (`LANG`/`LC_ALL`, затем язык интерфейса Windows, затем
`locale`); незнакомая локаль даёт английский. `ru` и `en` задают язык принудительно.

Настройка покрывает всё, что плагин рисует сам: чип и тултип, панель, подсказки в снимке,
`/fleet`. Смена языка применяется со следующим обновлением — backend читает настройки `/board`
на каждый запрос, перезапуск не нужен.

Форму настроек в Plugins hub она не покрывает: Hermes рисует подписи полей прямо из
`plugin.yaml` на одном языке, поэтому там не проза, а нейтральные формулы
(`running, no heartbeat N min → running_silent`).

## Тесты

```bash
python -m pytest -q
```

Тесты собирают синтетическую `kanban.db`, базы сессий и таблицы кронов во временном каталоге и
проверяют все состояния, счётчики, сортировку, гарантию «только чтение» и оба языка.

## Лицензия

MIT — см. [LICENSE](LICENSE). English README: [README.md](README.md).
