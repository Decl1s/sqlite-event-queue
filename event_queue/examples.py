"""Воспроизводимые события и настоящая авария отдельного процесса в демонстрации."""
import json
from pathlib import Path
import subprocess
import sys

from . import store, worker

ROOT = Path(__file__).resolve().parents[1]
EVENTS = [
    {'event_id': 'E0001', 'payload': {'category': 'book', 'amount_cents': 10000}},
    {'event_id': 'E0001', 'payload': {'category': 'book', 'amount_cents': 10000}},
    {'event_id': 'E0002', 'payload': {'category': 'pen', 'amount_cents': 2000}},
    {'event_id': 'E0003', 'payload': {'category': 'book', 'amount_cents': 5000}},
    {'event_id': 'E0004', 'payload': {'category': 'toy', 'amount_cents': 3000}},
]


def sample(output: Path) -> None:
    """Режим x сохраняет пользовательские правки при повторном запуске."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        for event in EVENTS:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')


def crash_child(db: Path, *, now: int = 1000) -> int:
    """Авария дочернего worker после INSERT: os._exit(86) пропускает finally и COMMIT."""
    code = ('from pathlib import Path; import sys; from event_queue.worker import once; '
            'once(Path(sys.argv[1]), now=int(sys.argv[2]), crash_before_commit=True)')
    result = subprocess.run([sys.executable, '-c', code, str(db.resolve()), str(now)],
                            cwd=ROOT, capture_output=True, timeout=15)
    if result.returncode != 86:
        raise RuntimeError('Учебный дочерний worker не завершился ожидаемым кодом 86.')
    return result.returncode


def demo(folder: Path) -> dict:
    """Логическое время 1000..1002 исключает сон; это модельные секунды, не даты Git."""
    folder.mkdir(parents=True, exist_ok=False)
    db = folder / 'queue.sqlite3'
    sample(folder / 'events.jsonl')
    store.initialize(db)
    admitted = store.enqueue(db, store.read_jsonl(folder / 'events.jsonl'), now=1000)
    assert admitted == {'inserted': 4, 'duplicates': 1}
    worker.once(db, now=1000)  # E0001
    retry = worker.once(db, now=1000, fail_once='E0002')
    exit_code = crash_child(db)  # E0003: эффект откатится, выдача сохранится
    rejected = worker.once(db, now=1000)  # E0004: неизвестная бизнес-категория
    before = store.snapshot(db)
    assert before['effect_count'] == 1 and before['total_cents'] == 10000
    assert before['states'] == {'ready': 1, 'leased': 1, 'done': 1, 'dead': 1}
    worker.once(db, now=1001)  # наступила доступность E0002
    worker.once(db, now=1002)  # истёк lease E0003: новая generation
    final = store.snapshot(db)
    assert final['states'] == {'ready': 0, 'leased': 0, 'done': 3, 'dead': 1}
    assert final['effect_count'] == 3 and final['total_cents'] == 17000
    assert final['by_category'] == {'book': 15000, 'pen': 2000}
    repeated = store.enqueue(db, EVENTS, now=1003)
    assert repeated == {'inserted': 0, 'duplicates': 5}
    assert worker.once(db, now=1003) is None
    assert store.snapshot(db) == final
    result = {'admitted': admitted, 'retry': retry, 'crash_exit': exit_code,
              'rejected': rejected, 'after_crash': before, 'repeated': repeated, 'final': final}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
