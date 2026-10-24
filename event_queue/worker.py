"""Конечный обработчик: результат и ACK принимаются вместе в той же SQLite."""
import json
import os
from pathlib import Path

from . import store

CATEGORIES = {'book', 'pen', 'bag'}


def validate(payload: dict) -> tuple[str, int]:
    """Контракт обработчика строже транспорта: очередь может хранить проблемное событие."""
    if (not isinstance(payload, dict) or set(payload) != {'category', 'amount_cents'}
            or not isinstance(payload['category'], str) or payload['category'] not in CATEGORIES
            or type(payload['amount_cents']) is not int or not 1 <= payload['amount_cents'] <= 9_999_999):
        raise store.QueueError('Некорректное событие для обработчика.')
    return payload['category'], payload['amount_cents']


def apply(db: Path, lease: store.Lease, *, now: int | None = None,
          crash_before_commit: bool = False) -> None:
    """INSERT эффекта и переход done в одной транзакции; внешние HTTP-вызовы сюда не входят."""
    with store.transaction(db) as con:
        row = store.require_lease(con, lease, store.timestamp(now))
        # Читаем канонический payload из БД: вызывающий не может подменить объект Lease.payload.
        category, amount = validate(json.loads(row['payload']))
        con.execute('INSERT INTO effects(event_id,category,amount_cents) VALUES(?,?,?)',
                    (lease.event_id, category, amount))
        if crash_before_commit:
            # Только явная учебная команда/дочерний процесс теста: без finally и без COMMIT.
            os._exit(86)
        store.require_lease(con, lease, store.timestamp(now))
        con.execute("UPDATE messages SET state='done',lease_until=NULL,last_error=NULL WHERE event_id=?", (lease.event_id,))


def once(db: Path, *, now: int | None = None, lease_seconds: int = 2,
         fail_once: str | None = None, crash_before_commit: bool = False) -> dict | None:
    lease = store.claim(db, now=now, lease_seconds=lease_seconds)
    if lease is None:
        return None
    if fail_once == lease.event_id and lease.attempt == 1:
        state = store.fail(db, lease, permanent=False, reason='temporary_failure', now=now)
        return {'event_id': lease.event_id, 'state': state, 'attempt': lease.attempt}
    try:
        apply(db, lease, now=now, crash_before_commit=crash_before_commit)
    except store.LeaseLost:
        raise
    except store.QueueError:
        state = store.fail(db, lease, permanent=True, reason='invalid_payload', now=now)
    else:
        state = 'done'
    return {'event_id': lease.event_id, 'state': state, 'attempt': lease.attempt}
