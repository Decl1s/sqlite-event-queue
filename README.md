# Очередь событий: повтор после сбоя без двойного результата

Я разработал учебную очередь событий на Python 3.12 и встроенной SQLite,
**без сторонних библиотек, сервера и облака**. Начат 23.10, завершён 24.10.2026.
Новое в серии — выдача сообщения на срок (lease), повтор, номер выдачи (fencing),
DLQ и атомарные результат + ACK. Все примеры синтетические.

Начни с [карты кода](START_HERE.md), [конспекта](NOTES.md),
[решений](DECISIONS.md) и [пяти заданий](EXERCISES.md).

[Публичный репозиторий](https://github.com/Decl1s/sqlite-event-queue) ·
[Проверки Linux и Windows](https://github.com/Decl1s/sqlite-event-queue/actions) ·
[Архив релиза v0.1.0 и SHA256SUMS](https://github.com/Decl1s/sqlite-event-queue/releases/tag/v0.1.0).

## Запуск за пять минут

Команды выполняются в корне отдельного проекта. Нужен Python 3.12 со SQLite >=3.37.
Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m event_queue demo
.venv/bin/python -m event_queue --db workspace/demo/queue.sqlite3 status
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m event_queue demo
.venv/Scripts/python.exe -m event_queue --db workspace/demo/queue.sqlite3 status
```

Устанавливать пакеты через pip не требуется. Всё работает локально.
Повторный demo откажется заменить существующую папку. Для нового опыта:
`python -m event_queue demo --output workspace/demo-2` через Python своей .venv.

Демонстрация принимает пять строк, из них четыре уникальных события и один дубль.
E0002 временно отказывает, отдельный процесс для E0003 реально завершается кодом 86
после INSERT до COMMIT, E0004 имеет неизвестную категорию toy и попадает в DLQ.
После восстановления ожидается:

```text
states: ready=0, leased=0, done=3, dead=1
effect_count=3, total_cents=17000
by_category: book=15000, pen=2000
```

Повторная загрузка тех же пяти строк даёт inserted=0, duplicates=5 и прежние суммы.
Файл `workspace/demo/result.json` показывает снимок после аварии и окончательный итог.
В demo время модельное: 1000..1003 секунды; ждать истечения срока не приходится.
Это время данных демонстрации, история Git датируется фактическим временем работы.

## Свой редактируемый пример

В Linux; для Windows замени `.venv/bin/python` на `.venv/Scripts/python.exe`:

```bash
.venv/bin/python -m event_queue init
.venv/bin/python -m event_queue sample
.venv/bin/python -m event_queue enqueue --input workspace/input/events.jsonl
.venv/bin/python -m event_queue work
.venv/bin/python -m event_queue status
.venv/bin/python -m event_queue dead
```

По умолчанию используется **другая** БД `workspace/queue.sqlite3`. Глобальный `--db`
передаётся **до** команды. Повтор init сохраняет события, sample не перезаписывает
файл. Редактируй копию JSONL до enqueue. После принятия содержимое event_id неизменно:
для исправленного события нужен новый ID. Один конфликт откатывает всю входную партию.

`work --fail-once E0002` моделирует временную ошибку на первой выдаче цикла.
Задержка повторов — 1 и 2 секунды. Повторный work после задержки продолжит обработку.
`--limit 1` выдаёт не более одного сообщения; `--lease-seconds 30` увеличивает срок.
Worker конечный: не ждёт будущих retry и не остаётся фоновой службой. Код 0 означает
выполненную команду; он не гарантирует пустую DLQ — проверяй status/dead.

`work --crash-before-commit` намеренно завершит **только этот процесс** кодом 86;
проверка и demo используют эту возможность в отдельном дочернем процессе.
После такого сбоя сообщение остаётся leased, пока следующий work не обработает
истечение срока. status сам сроки не обновляет.

`redrive E0004` возвращает только dead-сообщение в ready. Его payload остаётся тем же,
поэтому неизвестная категория снова даст dead, пока правило обработчика не исправлено.
Номер выдачи продолжает расти, бюджет attempts начинается заново.

## Проверки и архив

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/smoke.py
.venv/bin/python scripts/check_public.py
.venv/bin/python scripts/package_release.py
```

Smoke проверяет настоящий CLI, аварийный дочерний процесс, откат конфликтной партии,
повторную загрузку, сохранность demo и восстановление DLQ после поддержки toy.
Ожидаемый JSON: `smoke=passed`, `crash_exit=86`, `total_cents=17000`,
`after_redrive_cents=20000`. Исходный ZIP и SHA256SUMS собираются в artifacts
по явному PUBLIC_FILES.txt. Базы, входы, логи, результаты и .venv туда не входят.
Сканер секретов — дополнительная проверка, не полноценный аудит безопасности.

## Контракт и границы

JSONL UTF-8 (BOM/CRLF допустимы), до 1 000 000 байт и 1000 непустых строк.
У строки ровно `event_id` и `payload`; ID вида E0001, payload — JSON-объект до
10 000 байт. Повтор ключа JSON и NaN/Infinity запрещены. Одинаковый ID с другим
каноническим JSON отклоняется; порядок ключей и пробелы не создают новое событие.

Транспорт хранит и проблемный payload. Обработчик принимает ровно `category`
(book/pen/bag) и `amount_cents` — int от 1 до 9999999. bool и дробь не принимаются.

Три выдачи на цикл, затем dead. Доставка может повториться; результат не удваивается
потому, что он и подтверждение находятся в одной транзакции одного файла SQLite.
Эта гарантия **не распространяется** на отправку почты, HTTP и другую внешнюю БД.
Порядок после retry не является строгим FIFO. Есть один писатель SQLite;
распределённые workers, сетевые диски, миграции, приоритеты и автоматическая очистка
истории не реализованы. Падение процесса проверено; отключение питания не моделировалось.
Фактические ОС, проверки и публикация — [VALIDATION.md](VALIDATION.md).
