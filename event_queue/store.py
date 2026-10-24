"""Состояние очереди, короткие транзакции и защита от старого обработчика."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import time

APP_ID = 1163284785
MAX_ATTEMPTS = 3
MAX_FILE = 1_000_000


class QueueError(ValueError):
    """Понятная ошибка контракта или состояния очереди."""


class LeaseLost(QueueError):
    """Выдача просрочена, заменена новой или уже завершена."""


@dataclass(frozen=True)
class Lease:
    event_id: str
    payload: dict
    attempt: int
    generation: int
    until: int


def timestamp(now: int | None = None) -> int:
    """Реальные секунды по умолчанию; явное время делает учебные тесты детерминированными."""
    value = int(time.time()) if now is None else now
    if type(value) is not int or not 0 <= value <= 2**53:
        raise QueueError('Время должно быть неотрицательным целым числом секунд.')
    return value


def canonical(payload: dict) -> str:
    """Равенство payload определяется содержимым JSON, а не пробелами и порядком ключей."""
    if not isinstance(payload, dict):
        raise QueueError('payload должен быть JSON-объектом.')
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise QueueError('payload не является конечным JSON.') from exc
    if len(text.encode('utf-8')) > 10_000:
        raise QueueError('payload превышает 10 000 байт.')
    return text


def event_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'E[0-9]{4}', value):
        raise QueueError('event_id должен иметь вид E0001.')
    return value


@contextmanager
def connection(db: Path):
    """Не создаём БД неявно и не принимаем чужой файл за нашу очередь."""
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise QueueError('Нужен SQLite >= 3.37 для STRICT-таблиц.')
    con = sqlite3.connect(db.resolve().as_uri() + '?mode=rw', uri=True, timeout=5, autocommit=True)
    con.row_factory = sqlite3.Row
    try:
        if (con.execute('PRAGMA application_id').fetchone()[0] != APP_ID
                or con.execute('PRAGMA user_version').fetchone()[0] != 1):
            raise QueueError('Файл не является очередью версии 1; чужая БД не изменена.')
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        yield con
    finally:
        con.close()


@contextmanager
def transaction(db: Path):
    """Один писатель получает право изменения до SELECT: два клиента не заберут одну строку."""
    with connection(db) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            yield con
            con.execute('COMMIT')
        except BaseException:
            if con.in_transaction:
                con.execute('ROLLBACK')
            raise


def initialize(db: Path) -> None:
    """Новый файл создаём исключительно; повтор init своей БД ничего не очищает."""
    db = Path(db)
    if db.exists():
        with connection(db):
            return
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise QueueError('Нужен SQLite >= 3.37.')
    db.parent.mkdir(parents=True, exist_ok=True)
    with db.open('xb'):
        pass
    con = sqlite3.connect(db, autocommit=True)
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        con.executescript(Path(__file__).with_name('sql').joinpath('schema.sql').read_text(encoding='utf-8'))
    finally:
        con.close()


def enqueue(db: Path, events: list[dict], *, now: int | None = None) -> dict:
    """Вся партия принимается одной транзакцией; конфликт ID откатывает и новые строки."""
    at = timestamp(now)
    prepared = []
    if len(events) > 1000:
        raise QueueError('Допускается до 1000 событий в партии.')
    for event in events:
        if not isinstance(event, dict) or set(event) != {'event_id', 'payload'}:
            raise QueueError('У события нужны ровно event_id и payload.')
        prepared.append((event_key(event['event_id']), canonical(event['payload'])))
    inserted = duplicates = 0
    with transaction(db) as con:
        for key, payload in prepared:
            old = con.execute('SELECT payload FROM messages WHERE event_id=?', (key,)).fetchone()
            if old is not None:
                if old['payload'] != payload:
                    raise QueueError('Конфликт содержимого для ' + key + '; партия отклонена.')
                duplicates += 1
            else:
                con.execute('INSERT INTO messages(event_id,payload,available_at) VALUES(?,?,?)', (key, payload, at))
                inserted += 1
    return {'inserted': inserted, 'duplicates': duplicates}


def claim(db: Path, *, lease_seconds: int = 2, now: int | None = None) -> Lease | None:
    """Вернуть не более одной выдачи. generation растёт при каждой повторной доставке."""
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise QueueError('lease_seconds должен быть от 1 до 3600.')
    with transaction(db) as con:
        at = timestamp(now)  # после ожидания блокировки, а не до него
        con.execute("""UPDATE messages SET state=CASE WHEN attempts>=? THEN 'dead' ELSE 'ready' END,
                       available_at=?, lease_until=NULL, last_error='lease_expired'
                       WHERE state='leased' AND lease_until<=?""", (MAX_ATTEMPTS, at, at))
        row = con.execute("""SELECT * FROM messages WHERE state='ready' AND available_at<=?
                             ORDER BY available_at,event_id LIMIT 1""", (at,)).fetchone()
        if row is None:
            return None
        until = at + lease_seconds
        con.execute("""UPDATE messages SET state='leased', attempts=attempts+1,
                       generation=generation+1, lease_until=? WHERE event_id=?""", (until, row['event_id']))
        return Lease(row['event_id'], json.loads(row['payload']), row['attempts'] + 1,
                     row['generation'] + 1, until)


def require_lease(con: sqlite3.Connection, lease: Lease, now: int) -> sqlite3.Row:
    """Fencing: одним event_id подтвердить работу нельзя, нужна текущая generation и срок."""
    row = con.execute('SELECT * FROM messages WHERE event_id=?', (lease.event_id,)).fetchone()
    if (row is None or row['state'] != 'leased' or row['generation'] != lease.generation
            or row['lease_until'] <= now):
        raise LeaseLost('Выдача больше не действительна: ' + lease.event_id)
    return row


def fail(db: Path, lease: Lease, *, permanent: bool, reason: str, now: int | None = None) -> str:
    """Плохой payload сразу в DLQ; временный отказ — задержка 1,2 секунды, максимум 3 выдачи."""
    if reason not in {'invalid_payload', 'temporary_failure'}:
        raise QueueError('Неизвестный код ошибки.')
    with transaction(db) as con:
        at = timestamp(now)
        row = require_lease(con, lease, at)
        state = 'dead' if permanent or row['attempts'] >= MAX_ATTEMPTS else 'ready'
        delay = min(60, 2 ** (row['attempts'] - 1))
        con.execute('UPDATE messages SET state=?,available_at=?,lease_until=NULL,last_error=? WHERE event_id=?',
                    (state, at + delay, reason, lease.event_id))
        return state


def redrive(db: Path, event_id: str, *, now: int | None = None) -> None:
    """Осознанный возврат из DLQ. Payload и generation не сбрасываем; attempts — новый цикл."""
    event_key(event_id)
    with transaction(db) as con:
        result = con.execute("""UPDATE messages SET state='ready', attempts=0,available_at=?,last_error=NULL
                                WHERE event_id=? AND state='dead'""", (timestamp(now), event_id))
        if result.rowcount != 1:
            raise QueueError('redrive разрешён только для существующего dead-сообщения.')


def snapshot(db: Path) -> dict:
    """Несколько SELECT читают один снимок, даже если параллельно работает другой клиент."""
    with connection(db) as con:
        con.execute('BEGIN')
        try:
            messages = [dict(r) for r in con.execute('SELECT event_id,state,attempts,generation,available_at,lease_until,last_error FROM messages ORDER BY event_id')]
            totals = {r['category']: r['total'] for r in con.execute('SELECT category,sum(amount_cents) AS total FROM effects GROUP BY category ORDER BY category')}
            count = con.execute('SELECT count(*) FROM effects').fetchone()[0]
            con.execute('COMMIT')
        except BaseException:
            con.execute('ROLLBACK')
            raise
    return {'states': {name: sum(r['state'] == name for r in messages) for name in ['ready','leased','done','dead']},
            'effect_count': count, 'total_cents': sum(totals.values()), 'by_category': totals, 'messages': messages}


def read_jsonl(path: Path) -> list[dict]:
    """Проверить JSON целиком до транзакции; не принять повтор ключа JSON за последнее значение."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise QueueError('Повтор ключа JSON запрещён.')
            result[key] = value
        return result
    def invalid_constant(_):
        raise QueueError('NaN/Infinity запрещены.')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise QueueError('JSONL превышает 1 000 000 байт.')
    try:
        rows = [json.loads(line, object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
                for line in data.decode('utf-8-sig').splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise QueueError('Неверный UTF-8 или JSONL.') from exc
    if not 1 <= len(rows) <= 1000:
        raise QueueError('В JSONL должно быть от 1 до 1000 событий.')
    return rows
