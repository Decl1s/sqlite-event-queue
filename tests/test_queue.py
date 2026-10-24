"""Настоящая SQLite и отдельные соединения; время моделируется без sleep."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from event_queue import store, worker, examples


def event(key='E0001', amount=10000, category='book'):
    return {'event_id': key, 'payload': {'category': category, 'amount_cents': amount}}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'queue.sqlite3'
        store.initialize(self.db)

    def put(self):
        store.enqueue(self.db, [event()], now=1000)

    def test_repeat_init_preserves_events(self):
        self.put();store.initialize(self.db)
        self.assertEqual(store.snapshot(self.db)['states']['ready'], 1)

    def test_foreign_database_unchanged(self):
        other = self.db.with_name('other.sqlite3')
        with sqlite3.connect(other, autocommit=True) as con:
            con.execute('CREATE TABLE private(value TEXT)')
        con.close()
        before = hashlib.sha256(other.read_bytes()).digest()
        with self.assertRaises(store.QueueError):
            store.initialize(other)
        self.assertEqual(hashlib.sha256(other.read_bytes()).digest(), before)

    def test_missing_database_not_created_by_status(self):
        missing = self.db.with_name('missing.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):
            store.snapshot(missing)
        self.assertFalse(missing.exists())

    def test_duplicate_and_key_order_are_idempotent(self):
        self.put()
        duplicate = {'event_id': 'E0001', 'payload': {'amount_cents': 10000, 'category': 'book'}}
        self.assertEqual(store.enqueue(self.db, [duplicate], now=1000), {'inserted': 0, 'duplicates': 1})

    def test_conflict_rolls_back_entire_batch(self):
        self.put()
        with self.assertRaises(store.QueueError):
            store.enqueue(self.db, [event('E0002'), event(amount=99)], now=1000)
        self.assertEqual(len(store.snapshot(self.db)['messages']), 1)

    def test_invalid_envelope_rejected_before_write(self):
        for malformed in [event('wrong'), {'event_id': 'E0001', 'payload': []},
                          {'event_id': 'E0001', 'payload': {}, 'extra': True}]:
            with self.subTest(malformed=malformed), self.assertRaises(store.QueueError):
                store.enqueue(self.db, [event('E0002'), malformed])
        self.assertEqual(store.snapshot(self.db)['messages'], [])

    def test_payload_size_and_nonfinite_rejected(self):
        for payload in [{'text': 'x'*10001}, {'x': float('nan')}]:
            with self.assertRaises(store.QueueError):
                store.canonical(payload)

    def test_success_and_repeated_admission_do_not_double_effect(self):
        self.put();worker.once(self.db, now=1000)
        store.enqueue(self.db, [event()], now=1001)
        self.assertIsNone(worker.once(self.db, now=1001))
        self.assertEqual(store.snapshot(self.db)['total_cents'], 10000)

    def test_apply_reads_payload_from_database(self):
        self.put();lease = store.claim(self.db, now=1000)
        worker.apply(self.db, replace(lease, payload={'category': 'pen','amount_cents': 1}), now=1000)
        self.assertEqual(store.snapshot(self.db)['by_category'], {'book': 10000})

    def test_expiry_boundary_rejects_ack_without_reclaim(self):
        self.put();lease = store.claim(self.db, now=1000)
        with self.assertRaises(store.LeaseLost):
            worker.apply(self.db, lease, now=1002)
        self.assertEqual(store.snapshot(self.db)['effect_count'], 0)

    def test_old_generation_cannot_complete_new_lease(self):
        self.put();old = store.claim(self.db, now=1000)
        current = store.claim(self.db, now=1002)
        self.assertEqual(current.generation, old.generation+1)
        with self.assertRaises(store.LeaseLost):
            worker.apply(self.db, old, now=1002)
        with self.assertRaises(store.LeaseLost):
            store.fail(self.db, old, permanent=False, reason='temporary_failure', now=1002)
        worker.apply(self.db, current, now=1002)
        self.assertEqual(store.snapshot(self.db)['effect_count'], 1)

    def test_expiry_during_apply_rolls_back_insert(self):
        self.put();lease = store.claim(self.db, now=1000)
        with patch.object(store, 'timestamp', side_effect=[1000,1002]):
            with self.assertRaises(store.LeaseLost):
                worker.apply(self.db, lease)
        self.assertEqual(store.snapshot(self.db)['effect_count'], 0)

    def test_retry_delay_is_respected(self):
        self.put();worker.once(self.db, now=1000, fail_once='E0001')
        self.assertIsNone(store.claim(self.db, now=1000))
        self.assertEqual(worker.once(self.db, now=1001)['attempt'], 2)

    def test_transient_failures_exhaust_budget(self):
        self.put()
        for at in [1000,1001,1003]:
            lease = store.claim(self.db, now=at)
            state = store.fail(self.db, lease, permanent=False, reason='temporary_failure', now=at)
        self.assertEqual(state, 'dead')
        self.assertIsNone(store.claim(self.db, now=2000))
        self.assertEqual(store.snapshot(self.db)['messages'][0]['attempts'], 3)

    def test_expired_deliveries_exhaust_budget(self):
        self.put()
        for at in [1000,1002,1004]:
            self.assertIsNotNone(store.claim(self.db, now=at))
        self.assertIsNone(store.claim(self.db, now=1006))
        self.assertEqual(store.snapshot(self.db)['states']['dead'], 1)

    def test_permanent_failures_go_to_dlq(self):
        payloads = [event(category='toy')['payload'], {'category': [], 'amount_cents': 1},
                    {'category':'book','amount_cents':True}, {'category':'book','amount_cents':1.5},
                    {'category':'book','amount_cents':0}, {}]
        for i,payload in enumerate(payloads):
            key=f'E{i:04d}';store.enqueue(self.db,[{'event_id':key,'payload':payload}],now=1000)
        for _ in payloads:
            self.assertEqual(worker.once(self.db, now=1000)['state'], 'dead')
        self.assertEqual(store.snapshot(self.db)['states']['dead'], len(payloads))
        self.assertEqual(store.snapshot(self.db)['effect_count'], 0)

    def test_redrive_preserves_generation_and_payload(self):
        self.put();old=store.claim(self.db,now=1000)
        store.fail(self.db,old,permanent=True,reason='invalid_payload',now=1000)
        store.redrive(self.db,'E0001',now=1001)
        new=store.claim(self.db,now=1001)
        self.assertEqual((new.attempt,new.generation),(1,2))
        with self.assertRaises(store.LeaseLost):worker.apply(self.db,old,now=1001)
        worker.apply(self.db,new,now=1001)
        self.assertEqual(store.snapshot(self.db)['total_cents'],10000)

    def test_redrive_ready_or_done_rejected(self):
        self.put()
        with self.assertRaises(store.QueueError):store.redrive(self.db,'E0001')
        worker.once(self.db,now=1000)
        with self.assertRaises(store.QueueError):store.redrive(self.db,'E0001')

    def test_two_clients_claim_single_message_once(self):
        self.put();barrier=Barrier(2)
        def take():
            barrier.wait(timeout=5)
            return store.claim(self.db,now=1000)
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(lambda _:take(),range(2)))
        self.assertEqual(sum(v is not None for v in values),1)

    def test_concurrent_consumers_have_no_duplicate_effects(self):
        store.enqueue(self.db,[event(f'E{i:04d}',amount=100) for i in range(20)],now=1000)
        def consume():
            while worker.once(self.db,now=1000) is not None:pass
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:consume(),range(2)))
        result=store.snapshot(self.db)
        self.assertEqual((result['effect_count'],result['total_cents'],result['states']['done']),(20,2000,20))

    def test_actual_child_death_rolls_back_effect(self):
        self.put();self.assertEqual(examples.crash_child(self.db),86)
        before=store.snapshot(self.db)
        self.assertEqual((before['effect_count'],before['states']['leased']),(0,1))
        worker.once(self.db,now=1002)
        self.assertEqual(store.snapshot(self.db)['total_cents'],10000)

    def test_sql_constraints_are_real(self):
        self.put()
        with store.connection(self.db) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE messages SET state='leased' WHERE event_id='E0001'")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO effects VALUES('E9999','book',100)")

    def test_invalid_clock_or_lease_rejected(self):
        for value in [True,-1,1.5]:
            with self.assertRaises(store.QueueError):store.timestamp(value)
        for value in [0,True,3601]:
            with self.assertRaises(store.QueueError):store.claim(self.db,lease_seconds=value)

    def test_jsonl_bom_crlf_and_duplicate_keys(self):
        path=self.db.with_suffix('.jsonl')
        path.write_bytes(b'\xef\xbb\xbf'+json.dumps(event()).encode()+b'\r\n')
        self.assertEqual(store.read_jsonl(path),[event()])
        path.write_text('{"event_id":"E0001","event_id":"E0002","payload":{}}')
        with self.assertRaises(store.QueueError):store.read_jsonl(path)

    def test_jsonl_empty_invalid_and_oversize_rejected(self):
        path=self.db.with_suffix('.jsonl')
        for data in [b'',b'{',b'\xff',b'{"x":NaN}',b'x'*(store.MAX_FILE+1)]:
            path.write_bytes(data)
            with self.assertRaises(store.QueueError):store.read_jsonl(path)

    def test_sample_refuses_overwrite(self):
        path=self.db.with_suffix('.jsonl');examples.sample(path);before=path.read_bytes()
        with self.assertRaises(FileExistsError):examples.sample(path)
        self.assertEqual(path.read_bytes(),before)
