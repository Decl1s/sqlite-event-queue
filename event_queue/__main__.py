"""Локальная очередь: загрузка, конечный worker, состояние и явный возврат из DLQ."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from . import store, worker, examples


def bounded(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError('Ожидается число от 1 до 1000.')
    return number


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path('workspace/queue.sqlite3'), help='Собственный файл очереди (для demo не используется)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Создать очередь; повтор не очищает сообщения')
    prep = commands.add_parser('sample', help='Создать синтетический JSONL без перезаписи')
    prep.add_argument('--output', type=Path, default=Path('workspace/input/events.jsonl'))
    load = commands.add_parser('enqueue', help='Атомарно принять партию JSONL')
    load.add_argument('--input', type=Path, required=True)
    work = commands.add_parser('work', help='Обработать доступные события и завершиться')
    work.add_argument('--limit', type=bounded, default=100, help='Максимальное число выдач за запуск')
    work.add_argument('--lease-seconds', type=bounded, default=2, help='Срок одной выдачи; CLI допускает до 1000 секунд')
    work.add_argument('--fail-once', type=store.event_key, help='Учебный временный отказ на первой выдаче этого ID')
    work.add_argument('--crash-before-commit', action='store_true', help='Учебно завершить worker после INSERT до COMMIT с кодом 86')
    commands.add_parser('status', help='Показать согласованный снимок очереди и сумм')
    commands.add_parser('dead', help='Показать payload и код ошибки DLQ')
    redrive = commands.add_parser('redrive', help='Явно вернуть dead-сообщение без изменения payload')
    redrive.add_argument('event_id', type=store.event_key)
    demo = commands.add_parser('demo', help='Полный сценарий с настоящим аварийным дочерним процессом')
    demo.add_argument('--output', type=Path, default=Path('workspace/demo'))
    args = parser.parse_args()
    try:
        result = None
        if args.command == 'init':
            store.initialize(args.db)
            result = {'initialized': True}
        elif args.command == 'sample':
            examples.sample(args.output)
            result = {'sample_created': True}
        elif args.command == 'enqueue':
            result = store.enqueue(args.db, store.read_jsonl(args.input))
        elif args.command == 'work':
            outcomes = []
            for _ in range(args.limit):
                item = worker.once(args.db, lease_seconds=args.lease_seconds,
                                   fail_once=args.fail_once, crash_before_commit=args.crash_before_commit)
                if item is None:
                    break
                outcomes.append(item)
            result = {'processed': outcomes, 'note': 'Будущие retry/lease не ожидаются этим конечным запуском.'}
        elif args.command == 'status':
            result = store.snapshot(args.db)
        elif args.command == 'dead':
            with store.connection(args.db) as con:
                result = [{'event_id': r['event_id'], 'payload': json.loads(r['payload']),
                           'attempts': r['attempts'], 'last_error': r['last_error']}
                          for r in con.execute("SELECT * FROM messages WHERE state='dead' ORDER BY event_id")]
        elif args.command == 'redrive':
            store.redrive(args.db, args.event_id)
            result = {'redriven': args.event_id}
        else:
            result = examples.demo(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError, RuntimeError) as exc:
        print('Ошибка: ' + str(exc), file=sys.stderr)
        return 1
    return 0



if __name__ == '__main__':
    raise SystemExit(main())
