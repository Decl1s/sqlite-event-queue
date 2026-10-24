"""Демонстрация, настоящий CLI, конфликт партии и ручное восстановление DLQ."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from event_queue import examples, store, worker


def cli(*args, expected=0):
    result = subprocess.run([sys.executable,'-m','event_queue',*map(str,args)], cwd=ROOT,
                            capture_output=True, timeout=20)
    if result.returncode != expected:
        raise AssertionError(result.stderr.decode('utf-8',errors='replace'))
    return json.loads(result.stdout.decode('utf-8')) if expected == 0 else None


def main():
    with tempfile.TemporaryDirectory(prefix='event-queue-smoke-') as folder:
        root=Path(folder)
        result=examples.demo(root/'demo')
        db=root/'demo/queue.sqlite3'
        assert cli('--db',db,'status')==result['final']
        assert cli('--db',db,'enqueue','--input',root/'demo/events.jsonl')=={'inserted':0,'duplicates':5}
        bad=root/'conflict.jsonl'
        bad.write_text(json.dumps({'event_id':'E0005','payload':{'category':'bag','amount_cents':100}})+'\n'
                       +json.dumps({'event_id':'E0001','payload':{'category':'book','amount_cents':10001}})+'\n',encoding='utf-8')
        cli('--db',db,'enqueue','--input',bad,expected=1)
        assert cli('--db',db,'status')==result['final']
        cli('demo','--output',root/'demo',expected=1)
        # Модель исправления бизнес-правила: после поддержки toy тот же payload можно redrive.
        worker.CATEGORIES.add('toy')
        try:
            cli('--db',db,'redrive','E0004')
            assert worker.once(db)['state']=='done'
        finally:
            worker.CATEGORIES.remove('toy')
        assert store.snapshot(db)['total_cents']==20000
        assert store.snapshot(db)['states']['dead']==0
        manual=root/'manual.sqlite3'
        cli('--db',manual,'init')
        cli('sample','--output',root/'input/events.jsonl')
        assert cli('--db',manual,'enqueue','--input',root/'input/events.jsonl')['inserted']==4
        cli('--db',manual,'work','--lease-seconds','30')
        assert cli('--db',manual,'status')['total_cents']==17000
        assert len(cli('--db',manual,'dead'))==1
    print(json.dumps({'smoke':'passed','crash_exit':86,'total_cents':17000,
                      'dead':1,'after_redrive_cents':20000,'cli':True}))


if __name__=='__main__':
    main()
