# Где что находится

Открой каталог проекта в VS Code и создай .venv по README. В Run Task доступны
demo, status demo, sample, unittest, smoke и упаковка. Повтор demo использует новую
папку; существующую командой не стираем. F5 подготовлен для status demo после её запуска.

| Файл / функция | Роль и важные аргументы |
| --- | --- |
| event_queue/__main__.py | Вход CLI. --db до команды выбирает файл очереди |
| examples.py: EVENTS/sample | Синтетический JSONL, режим x сохраняет правки |
| store.py: initialize/connection | Своя схема, версия и явное закрытие соединения |
| sql/schema.sql | STRICT, состояния, CHECK, FK и уникальный эффект |
| store.py: enqueue | events принимаются целиком; now моделирует секунды в тестах |
| store.py: claim | lease_seconds задаёт срок; attempts и generation растут |
| store.py: require_lease | Сверяет state, generation и срок; до и после INSERT |
| worker.py: validate/apply | Бизнес-контракт, результат и ACK в одной транзакции |
| worker.py: once | Одна выдача; fail_once — управляемая временная ошибка |
| store.py: fail/redrive | Backoff/DLQ и осознанный новый цикл попыток |
| store.py: snapshot | Несколько SELECT читают один снимок |
| examples.py: crash_child/demo | Настоящая смерть дочернего процесса и восстановление |
| tests/test_queue.py | Настоящая SQLite, независимые клиенты, точная граница lease |
| scripts/smoke.py | CLI и полный путь, включая исправление бизнес-политики |

Проследи E0003: claim сохраняет leased, attempts=1, generation=1, lease_until=1002.
Дочерний процесс вставляет эффект и завершается до COMMIT. SQLite откатывает INSERT;
выдача остаётся в БД. При now=1002 claim выдаёт событие с generation=2. Новый worker
записывает 5000 и done вместе. Старый generation=1 уже не подходит.

Сначала поставь F9 перед INSERT в worker.apply и наблюдай параметры lease, затем
изучи SQL и тест old_generation. Python-отладчик нельзя использовать как способ
обхода срока: при долгой паузе lease может истечь. Для обычного CLI увеличь
--lease-seconds, а в unit-тесте применяй явное модельное now.
Фактические нажатия GUI не заявляются — проверенные команды и ОС указаны в VALIDATION.
