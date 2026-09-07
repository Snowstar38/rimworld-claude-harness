"""Relay overlay human messages into the durable native-agent event bus."""
import hashlib
import json
import time
from pathlib import Path

import event_bus
import game_session
import overlay_client as ov
import runtime_binding

STATE = Path(__file__).resolve().parent / "state" / "human-events.json"
POLL_SECONDS = 0.5


def event_id(item):
    stable = {k: item.get(k) for k in ("id", "ts", "kind", "text")}
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "human-overlay-" + hashlib.sha256(raw).hexdigest()


def pump_once(get=ov.get, publish=event_bus.publish):
    saved = game_session.read(STATE, {"seen": [], "pending": []})
    binding = runtime_binding.load()
    session = binding.get("session_id")
    generation = game_session.current().get("generation")
    if saved.get("session") != session:
        saved = {"session": session, "seen": [], "pending": []}
    seen = set(saved.get("seen") or [])
    pending = {row["event_id"]: row for row in (saved.get("pending") or [])}
    state, why = get("/state", timeout=1.5)
    for item in (state or {}).get("feed") or []:
        if item.get("kind") != "human":
            continue
        eid = event_id(item)
        if eid in seen or eid in pending:
            continue
        # A newly bound session must not replay the overlay's retained history.
        try:
            bound_at = runtime_binding.PATH.stat().st_mtime
        except OSError:
            bound_at = time.time()
        if float(item.get("ts") or 0) < bound_at:
            seen.add(eid)
            continue
        pending[eid] = {"event_id": eid, "body": item.get("text") or "",
                        "captured_at": float(item.get("ts") or time.time()),
                        "session": session, "generation": generation}
    # Persist discovery before delivery: if the overlay feed rolls over while
    # the bus is unavailable, M's message still exists locally.
    game_session.write(STATE, {"session": session, "seen": list(seen)[-1024:],
                               "pending": list(pending.values())})
    accepted = 0
    for eid, row in list(pending.items()):
        if row.get("session") != session or row.get("generation") != generation:
            pending.pop(eid, None)
            continue
        try:
            result = publish(kind="human", body=row["body"], event_id=eid,
                             captured_at=row["captured_at"], generation=row.get("generation"),
                             source="overlay-human")
        except Exception:
            continue
        # Do not watermark a refusal. The next poll retries it, while event_bus's
        # unique id makes a crash after insertion harmless.
        if result.get("accepted"):
            seen.add(eid)
            pending.pop(eid, None)
            accepted += 1
    game_session.write(STATE, {"session": session, "seen": list(seen)[-1024:],
                               "pending": list(pending.values())})
    return accepted


def run(should_stop, sleep=time.sleep):
    while not should_stop():
        try:
            pump_once()
        except Exception:
            pass
        sleep(POLL_SECONDS)
