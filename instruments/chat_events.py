"""Relay screened stream chat into the durable native-agent event bus.

Only overlay records carrying the server-owned approval marker are eligible.
Chat remains audience input: it is delivered without wakeups and with explicit
untrusted provenance for the lifecycle hook to render safely.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import event_bus
import game_session
import overlay_client as ov
import runtime_binding

STATE = Path(__file__).resolve().parent / "state" / "chat-events.json"
POLL_SECONDS = 0.5
MAX_NAME = 40
MAX_TEXT = 500

# The names a viewer uses to address the agent directly. A message that names
# the agent is the most valuable thing chat produces and the class that failed
# worst: on 2026-09-07 "downed wolf needs finished off and butchered. @Errata"
# was approved by the screener in 20 ms and then sat 5m49s in the outbox behind
# a backlog of Lookout reviews, and the wolf lived four more turns. A mention is
# therefore published as a waking, priority event; nothing about the "@" itself
# ever filtered anything -- that theory, filed live, is wrong.
#
# `twitch_chat.mentions_agent` keeps a copy of this rule for the overlay side;
# the two trees deploy separately, so the copies must be changed together.
AGENT_NAMES = tuple(n.strip() for n in (os.environ.get("RIMWORLD_AGENT_NAMES")
                                        or "errata,claude_plays_rimworld").split(",")
                    if n.strip())
MENTION = re.compile(r"(?<![0-9A-Za-z_])@?(?:%s)(?![0-9A-Za-z_])"
                     % "|".join(re.escape(n) for n in AGENT_NAMES), re.IGNORECASE)


def mentions_agent(text):
    """Does this viewer message address the agent by name, with or without @?"""
    return bool(AGENT_NAMES) and bool(MENTION.search(str(text or "")))


def event_id(item):
    stable = {k: item.get(k) for k in
              ("id", "ts", "kind", "name", "text", "source", "approved")}
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "approved-chat-" + hashlib.sha256(raw).hexdigest()


def approved_message(item):
    """Return the approved display fields, or None for an ineligible record."""
    if item.get("kind") != "chat" or item.get("approved") is not True:
        return None
    if item.get("source") != "twitch":
        return None
    name, body = item.get("name"), item.get("text")
    if not isinstance(name, str) or not isinstance(body, str):
        return None
    if not name.strip() or not body.strip():
        return None
    if len(name) > MAX_NAME or len(body) > MAX_TEXT:
        return None
    return {"username": name, "text": body}


def pump_once(get=ov.get, publish=event_bus.publish):
    saved = game_session.read(STATE, {"seen": [], "pending": []})
    binding = runtime_binding.load()
    session = binding.get("session_id")
    generation = game_session.current().get("generation")
    if saved.get("session") != session:
        saved = {"session": session, "seen": [], "pending": []}
    seen = set(saved.get("seen") or [])
    pending = {row["event_id"]: row for row in (saved.get("pending") or [])}
    state, _why = get("/state", timeout=1.5)
    for item in (state or {}).get("feed") or []:
        message = approved_message(item)
        if message is None:
            continue
        eid = event_id(item)
        if eid in seen or eid in pending:
            continue
        try:
            bound_at = runtime_binding.PATH.stat().st_mtime
        except OSError:
            bound_at = time.time()
        if float(item.get("ts") or 0) < bound_at:
            seen.add(eid)
            continue
        pending[eid] = {
            "event_id": eid, "body": message,
            "captured_at": float(item.get("ts") or time.time()),
            "session": session, "generation": generation,
        }
    game_session.write(STATE, {"session": session, "seen": list(seen)[-1024:],
                               "pending": list(pending.values())})
    accepted = 0
    for eid, row in list(pending.items()):
        if row.get("session") != session or row.get("generation") != generation:
            pending.pop(eid, None)
            continue
        try:
            mention = mentions_agent((row.get("body") or {}).get("text"))
            result = publish(kind="chat", body=row["body"], event_id=eid,
                             captured_at=row["captured_at"],
                             generation=row.get("generation"), source="chat",
                             meta={"trust": "untrusted", "approved": True,
                                   # Ordinary chat still never wakes a parked
                                   # agent or jumps the queue; being spoken to
                                   # by name is the whole exception.
                                   "wake": mention, "mention": mention,
                                   "priority": 1 if mention else 0})
        except Exception:
            continue
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
