"""Evidence-bearing timing and anomaly reviews, separate from pay decisions."""
import json
from .storage import now, encode
from .domain import new_id
from .performance_rules import assess_timeliness


class PerformanceReview:
    def __init__(self, store):
        self.db = store
        self.db.connection.executescript('''
        CREATE TABLE IF NOT EXISTS performance_anomalies(
          id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES performance_units(id),
          kind TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('SUSPECTED','CONFIRMED','WITHDRAWN','NOT_APPLICABLE')),
          evidence TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS performance_sla_reviews(
          id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES performance_units(id),
          inputs_json TEXT NOT NULL, result_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
        );
        ''')

    def _audit(self, uid, event, actor, reason, evidence, before, after):
        self.db.execute('''INSERT INTO performance_events(unit_id,event,actor,reason,evidence,before_json,after_json,created_at)
                           VALUES(?,?,?,?,?,?,?,?)''',
                        (uid, event, actor, reason, encode(evidence), encode(before), encode(after), now()))

    @staticmethod
    def _required(actor, reason, evidence):
        if not actor or not reason or not evidence:
            raise ValueError("Reviewer, reason and evidence are required")

    def flag(self, unit_id, kind, *, actor, reason, evidence):
        self._required(actor, reason, evidence)
        if not kind:
            raise ValueError("Anomaly type required")
        with self.db.transaction():
            aid = new_id()
            self.db.execute('INSERT INTO performance_anomalies VALUES(?,?,?,?,?,?,?,?,?)',
                            (aid, unit_id, kind, 'SUSPECTED', encode(evidence), actor, reason, now(), now()))
            self._audit(unit_id, 'ANOMALY_FLAGGED', actor, reason, evidence, None, {'id': aid, 'state': 'SUSPECTED'})
            return aid

    def resolve(self, anomaly_id, state, *, reviewer, reason, evidence):
        self._required(reviewer, reason, evidence)
        if state not in ('CONFIRMED', 'WITHDRAWN', 'NOT_APPLICABLE'):
            raise ValueError("Explicit human review state required")
        with self.db.transaction():
            old = self.db.one('SELECT * FROM performance_anomalies WHERE id=?', (anomaly_id,))
            if not old:
                raise ValueError("Unknown anomaly")
            self.db.execute('UPDATE performance_anomalies SET state=?,actor=?,reason=?,evidence=?,updated_at=? WHERE id=?',
                            (state, reviewer, reason, encode(evidence), now(), anomaly_id))
            self._audit(old['unit_id'], 'ANOMALY_REVIEWED', reviewer, reason, evidence, dict(old), {'id': anomaly_id, 'state': state})

    def timing(self, unit_id, *, actor, policy=None, exceptions=(), as_of=None):
        if not actor:
            raise ValueError("Actor required")
        unit = self.db.one('SELECT * FROM performance_units WHERE id=?', (unit_id,))
        if not unit:
            raise ValueError("Unknown unit")
        inputs = {'asked_at': unit['question_time'], 'replied_at': unit['first_response_at'],
                  'completed_at': unit['completed_at'], 'time_evidence': unit['question_time_source'],
                  'policy': policy, 'exceptions': exceptions, 'as_of': as_of}
        result = assess_timeliness(**inputs)
        with self.db.transaction():
            self.db.execute('INSERT INTO performance_sla_reviews VALUES(?,?,?,?,?,?)',
                            (new_id(), unit_id, encode(inputs), encode(result), actor, now()))
            self.db.execute('UPDATE performance_units SET timeliness_status=? WHERE id=?', (result['status'], unit_id))
            self._audit(unit_id, 'SLA_EVALUATED', actor, result['reason'], inputs, None, result)
        return result

    def anomaly_summary(self, *, start=None, end=None, rate_policy=None):
        clauses, params = [], []
        if start:
            clauses.append('created_at>=?'); params.append(start)
        if end:
            clauses.append('created_at<?'); params.append(end)
        rows = [dict(r) for r in self.db.all('SELECT * FROM performance_anomalies' + (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params)]
        states = {state: {'events': sum(r['state'] == state for r in rows),
                          'units': len({r['unit_id'] for r in rows if r['state'] == state})}
                  for state in ('SUSPECTED', 'CONFIRMED', 'WITHDRAWN', 'NOT_APPLICABLE')}
        # The denominator and counting convention must be externally confirmed.
        rate = None
        if rate_policy and rate_policy.get('confirmed') is True and rate_policy.get('evidence'):
            denominator = rate_policy.get('denominator')
            kind = rate_policy.get('numerator_kind')
            if isinstance(denominator, int) and not isinstance(denominator, bool) and denominator > 0 and kind in ('events', 'units'):
                rate = states['CONFIRMED'][kind] / denominator
        return {'counts': states, 'rate': rate, 'rate_status': '待确认口径' if rate is None else '按人工确认口径计算',
                'monthly_pay_coefficient': None, 'automatic_penalty': False}
