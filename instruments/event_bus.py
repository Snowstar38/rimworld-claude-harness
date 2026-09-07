"""Durable event delivery to Hands, falling back to Core only at handoff.

The SQLite outbox is independent of game polling and model turns. Receipts are
acknowledged explicitly after reading; crashed delivery is retried, never lost.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import game_session
import runtime_binding

DB = Path(__file__).resolve().parent / 'state' / 'events.sqlite3'
RECEIPT_SECONDS = 120


class ClosingConnection(sqlite3.Connection):
    """sqlite3's context manager commits but does not close the handle."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=5, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    con.executescript('''
    CREATE TABLE IF NOT EXISTS routes (session TEXT PRIMARY KEY, hands TEXT, turn INTEGER);
    CREATE TABLE IF NOT EXISTS events (
      id TEXT PRIMARY KEY, session TEXT, kind TEXT, body TEXT, generation TEXT,
      captured REAL, source TEXT, meta TEXT, created REAL,
      status TEXT DEFAULT 'pending', recipient TEXT, receipt TEXT, offered REAL,
      priority INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS event_status ON events(session,status,created);
    ''')
    # `receive` hands over eight events at a time, oldest first. A backlog of
    # Lookout reviews therefore buried a viewer's direct question behind four
    # tool boundaries. Priority is the queue-jump lane, and only the publisher
    # sets it (`meta['priority']`).
    if 'priority' not in {r[1] for r in con.execute('PRAGMA table_info(events)')}:
        con.execute('ALTER TABLE events ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
    return con


def route(con, session):
    r = con.execute('SELECT hands FROM routes WHERE session=?', (session,)).fetchone()
    return r['hands'] if r and r['hands'] else 'core'


def set_hands(session, agent_id, turn):
    if not agent_id or agent_id == 'core':
        raise ValueError('Hands requires a native subagent id')
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT hands,turn FROM routes WHERE session=?',
                          (session,)).fetchone()
        active = row['hands'] if row and row['hands'] else 'core'
        if active not in ('core', agent_id):
            held = row['turn'] if row else None
            # A route stamped with an EARLIER turn cannot still be live:
            # `stream.py hands-start` refuses to open turn N+1 while turn N is
            # open, so by the time turn N+1 claims, turn N's fork is gone and
            # only its uncleared route remains -- exactly what happened on
            # 2026-09-07, where the refusal here escaped the register hook and
            # left turn 20 with no recorded owner at all.
            if held is None or turn is None or int(held) >= int(turn):
                raise RuntimeError('Another Hands agent already owns this turn')
        con.execute('INSERT OR REPLACE INTO routes VALUES(?,?,?)', (session, agent_id, turn))
        # A Core receipt issued just before Hands acquired the turn must not
        # strand the event with an inactive recipient for two minutes.
        con.execute("UPDATE events SET status='pending',receipt=NULL,offered=NULL "
                    "WHERE session=? AND status='offered' AND recipient!=?",
                    (session, agent_id))


def handback(session, agent_id=None):
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        active = route(con, session)
        if agent_id and active not in ('core', agent_id):
            raise RuntimeError('Cannot hand back another agent\'s turn')
        con.execute('INSERT OR REPLACE INTO routes VALUES(?,NULL,NULL)', (session,))
        # Offered-but-unacknowledged messages must survive a completed fork.
        con.execute("UPDATE events SET status='pending',receipt=NULL,offered=NULL WHERE session=? AND status='offered'", (session,))


def current_recipient(session=None):
    session = session or runtime_binding.load().get('session_id')
    with connect() as con:
        return route(con, session)


def publish(kind, body, event_id, captured_at=None, generation=None, source=None, meta=None):
    binding = runtime_binding.load()
    session = binding.get('session_id')
    if not session:
        return dict(accepted=False, event_id=event_id, reason='no controlling session')
    current_generation = game_session.current().get('generation')
    generation = generation or current_generation
    if not generation:
        return dict(accepted=False, event_id=event_id, reason='no game generation')
    if generation != current_generation:
        return dict(accepted=False, event_id=event_id, reason='stale game generation')
    now = time.time()
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        recipient = route(con, session)
        encoded = body if isinstance(body, str) else json.dumps(body, sort_keys=True)
        try:
            priority = int((meta or {}).get('priority') or 0)
        except (TypeError, ValueError):
            priority = 0
        inserted = con.execute('''INSERT OR IGNORE INTO events
            (id,session,kind,body,generation,captured,source,meta,created,recipient,priority)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (event_id, session, kind, encoded, generation,
             captured_at if captured_at is not None else now,
             source, json.dumps(meta or {}, sort_keys=True), now, recipient,
             priority)).rowcount
        stored = con.execute('SELECT recipient,session,generation FROM events WHERE id=?',
                             (event_id,)).fetchone()
        if inserted == 0 and (not stored or stored['session'] != session
                              or stored['generation'] != generation):
            return dict(accepted=False, event_id=event_id,
                        reason='event id belongs to another session/generation')
    return dict(accepted=True, event_id=event_id,
                recipient=stored['recipient'] if stored else recipient,
                duplicate=inserted == 0)


def receive(session, recipient, limit=8):
    now = time.time()
    limit = max(1, min(128, int(limit)))
    generation = game_session.current().get('generation')
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        active = route(con, session)
        if active != recipient:
            return None
        if generation:
            con.execute("UPDATE events SET status='stale' WHERE session=? AND status IN ('pending','offered') AND generation IS NOT NULL AND generation!=?", (session, generation))
        con.execute("UPDATE events SET status='pending',receipt=NULL,offered=NULL WHERE session=? AND status='offered' AND offered<?", (session, now-RECEIPT_SECONDS))
        rows = con.execute("SELECT * FROM events WHERE session=? AND status='pending'"
                           " ORDER BY priority DESC, created LIMIT ?",
                           (session, limit)).fetchall()
        if not rows:
            return None
        receipt = uuid.uuid4().hex
        for row in rows:
            con.execute("UPDATE events SET status='offered',recipient=?,receipt=?,offered=? WHERE id=?", (recipient, receipt, now, row['id']))
        events = []
        for row in rows:
            item = dict(row)
            try:
                item['body'] = json.loads(item['body'])
            except (TypeError, ValueError):
                pass
            try:
                item['meta'] = json.loads(item['meta'])
            except (TypeError, ValueError):
                pass
            events.append(item)
        return dict(receipt=receipt, recipient=recipient, events=events)


def acknowledge(receipt, session=None, recipient=None):
    with connect() as con:
        query = "UPDATE events SET status='delivered' WHERE receipt=? AND status='offered'"
        args = [receipt]
        if session:
            query += ' AND session=?'; args.append(session)
        if recipient:
            query += ' AND recipient=?'; args.append(recipient)
        return con.execute(query, args).rowcount


def receipt_fate(receipt, session=None):
    """Why a receipt acknowledged nothing. Returns a short human line or None.

    `ack` printed a bare `acknowledged 0 event(s)` for the first two Lookout
    reports of the 2026-09-07 session and 1 or 2 afterwards, which reads as
    lost events. Nothing was lost. A receipt stops being the current offer in
    three ordinary ways, all of them at the START of a session where the gap
    between a packet being offered and the model getting round to acking it is
    longest:

      * the route changed -- `set_hands` and `handback` push every offered
        event back to `pending`, so a Core receipt dies the moment a Hands
        turn opens;
      * RECEIPT_SECONDS (120 s) elapsed and the next `receive` re-offered the
        events under a fresh receipt;
      * the ack simply ran twice.

    In all three the events are still in the outbox and are re-delivered.
    """
    with connect() as con:
        rows = con.execute('SELECT status, COUNT(*) AS n FROM events'
                           ' WHERE receipt=? GROUP BY status', (receipt,)).fetchall()
        pending = con.execute("SELECT COUNT(*) AS n FROM events WHERE session=?"
                              " AND status='pending'",
                              (session,)).fetchone()['n'] if session else None
    seen = {r['status']: r['n'] for r in rows}
    if seen.get('delivered'):
        return ('that receipt was already acknowledged (%d event(s)) -- nothing '
                'is lost and nothing is owed' % seen['delivered'])
    if not rows:
        return ('no event carries that receipt any more. It was superseded, '
                'which is normal: the route changed at a turn boundary, or the '
                '%d s receipt lapsed and the events were re-offered under a new '
                'one.%s Nothing is lost -- the next tool boundary delivers them '
                'again with a receipt to ack.'
                % (RECEIPT_SECONDS,
                   '' if not pending else ' %d event(s) are pending now.' % pending))
    return ('that receipt now covers %s -- it is no longer the open offer, and '
            'the events are re-delivered rather than lost'
            % ', '.join('%d %s' % (n, k) for k, n in sorted(seen.items())))


def wake_worthy(session):
    """A pending event worth waking a parked agent for. No flag means wake."""
    with connect() as con:
        rows = con.execute("SELECT meta FROM events WHERE session=? AND status='pending'",
                           (session,)).fetchall()
    for row in rows:
        try:
            meta = json.loads(row['meta'] or '{}')
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict) or meta.get('wake', True):
            return True
    return False


def _line(event):
    if event['kind'] == 'alert_new':
        body = event['body'] if isinstance(event['body'], dict) else {}
        alert = body.get('event') or {}
        return '[ALERT_NEW] %s (%s) — game still running' % (
            alert.get('label') or body.get('detail') or '?', alert.get('priority') or '?')
    return '[%s] %s' % (event['kind'].upper(), event['body'])


# A message from a person is a task inside the turn, not a replacement for it.
HUMAN_TURN_NOTE = ('(This is work inside the current turn, not the turn itself. '
                   'Do it, then continue the brief until the turn budget '
                   'reminder arrives.)')


def format_packet(packet):
    lines = ['RIMWORLD EVENT — for %s only' % packet['recipient']]
    for e in packet['events']:
        lines.append(_line(e))
    if any(e['kind'] in ('notification_batch', 'letter', 'message') for e in packet['events']):
        lines.append('React briefly on the stream with say.py, inspect any required choice, then explicitly resume supervised play when appropriate.')
    if any(e['kind'] == 'alert_new' for e in packet['events']):
        lines.append('An alert never pauses the game: nothing here needs a resume.')
    if any(e['kind'] == 'human' for e in packet['events']):
        lines.append(HUMAN_TURN_NOTE)
    lines.append('After reading, acknowledge once: python event_bus.py ack %s' % packet['receipt'])
    return '\n'.join(lines)


def wait(session, recipient, timeout=3500):
    """Quiet local waiting; no bridge calls, no model polls, no periodic output."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        binding = runtime_binding.load()
        if binding.get('session_id') != session or not runtime_binding.alive(binding):
            return None
        with connect() as con:
            active = route(con, session)
        if recipient != 'core' and active != recipient:
            return None
        # A non-waking event (a High alert) waits for something else to wake
        # us; `receive` then hands over everything pending, including it.
        if wake_worthy(session):
            packet = receive(session, recipient)
            if packet:
                return packet
        time.sleep(0.3)
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='op', required=True)
    a = sub.add_parser('ack'); a.add_argument('receipt')
    a = sub.add_parser('wait'); a.add_argument('--recipient', default='core'); a.add_argument('--timeout', type=float, default=3500)
    args = p.parse_args(argv)
    if args.op == 'ack':
        session = runtime_binding.load().get('session_id')
        count = acknowledge(args.receipt, session)
        print('acknowledged %d event(s)' % count)
        if not count:
            fate = receipt_fate(args.receipt, session)
            if fate:
                print('   %s' % fate)
        return 0
    packet = wait(runtime_binding.load().get('session_id'), args.recipient, args.timeout)
    if packet:
        print(format_packet(packet), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
