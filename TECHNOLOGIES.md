# Технологии и зависимости

Версия проекта 0.1.0, завершение 24.10.2026. Новая тема — надёжная очередь событий.

| Технология | Версия / контракт | Назначение и официальный источник |
| --- | --- | --- |
| Python | 3.12.x, фактическая версия в VALIDATION | CLI и стандартная библиотека: https://docs.python.org/3.12/ |
| venv | Python 3.12 standard library | Локальное окружение: https://docs.python.org/3.12/library/venv.html |
| SQLite | минимум 3.37.0, фактическая версия в VALIDATION | Локальный файл, STRICT и транзакции: https://www.sqlite.org/stricttables.html |
| sqlite3 | модуль Python 3.12 | autocommit=True и явные BEGIN/COMMIT/ROLLBACK: https://docs.python.org/3.12/library/sqlite3.html |
| SQL | диалект SQLite | PK/FK/CHECK, INDEX и GROUP BY: https://www.sqlite.org/lang.html |
| Rollback journal / synchronous FULL | штатный режим SQLite | Атомарность локальных изменений: https://www.sqlite.org/atomiccommit.html |
| JSONL / JSON | UTF-8, BOM/CRLF допустимы на входе | События и снимок: https://docs.python.org/3.12/library/json.html |
| dataclasses / contextlib | Python 3.12 standard library | Неизменная выдача и закрытие транзакций: https://docs.python.org/3.12/library/contextlib.html |
| argparse / pathlib / re | Python 3.12 standard library | Команды, пути и ID: https://docs.python.org/3.12/library/argparse.html |
| time | Python 3.12 standard library | Системные секунды; модельное now для тестов: https://docs.python.org/3.12/library/time.html |
| subprocess / os._exit | Python 3.12 standard library | Настоящая авария дочернего процесса: https://docs.python.org/3.12/library/os.html#os._exit |
| unittest / mock / tempfile | Python 3.12 standard library | Проверки на отдельных файлах и модельных часах: https://docs.python.org/3.12/library/unittest.html |
| ThreadPoolExecutor / Barrier | Python 3.12 standard library | Независимые конкурентные клиенты: https://docs.python.org/3.12/library/concurrent.futures.html |
| hashlib / zipfile / tomllib | Python 3.12 standard library | Безопасный allowlist, ZIP, SHA-256 и версия: https://docs.python.org/3.12/library/zipfile.html |
| Git | фактическая версия в VALIDATION | Собственный корень, ветка codex/: https://git-scm.com/docs |
| GitHub Actions | Ubuntu/Windows, реальный статус в VALIDATION | Удалённая проверка: https://docs.github.com/ru/actions |
| actions/checkout | v4, точный SHA в ci.yml | Исходники без сохранения credentials: https://github.com/actions/checkout |
| actions/setup-python | v5, точный SHA в ci.yml | Python 3.12 на runner: https://github.com/actions/setup-python |
| actions/upload-artifact | v4, точный SHA в ci.yml | Исходники и checksum: https://github.com/actions/upload-artifact |
| VS Code / debugpy | необязательные конфигурации | Задачи и отладка, GUI не проверен: https://code.visualstudio.com/docs/python/debugging |

## Полный перечень зависимостей

**Сторонних прямых и транзитивных Python-зависимостей: 0.** В pyproject.toml закреплено
`requires-python = ">=3.12,<3.13"` и `dependencies = []`. Pip/uv и файл resolver lock
для запуска не нужны. SQL-движок входит в сборку Python/ОС, его версия проверяется
при подключении. Нельзя объявлять эту системную библиотеку закреплённым pip-пакетом;
фактические версии Linux и CI указаны в VALIDATION и журналах Actions.

В собственном коде также используются sys, встроенные аннотации типов и json;
упаковочный сканер использует subprocess. Установка Python и необязательного IDE
не входит в приложение. Никакие серверы, фоновые службы, облачные SDK и npm-пакеты
не используются. Бинарные библиотеки Python/SQLite в исходный ZIP не включены.
