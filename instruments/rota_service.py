"""Detached singleton scheduler for the blind external screenshot reviewer.

The first review starts with the service, then slots occur on an exact 180-second
wall cadence. Review completion never moves the next deadline.

  python rota_service.py start
  python rota_service.py status
  python rota_service.py serve       # internal detached entry point
"""
import json
import os
import subprocess
import sys
import time
import uuid
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
PIDFILE = STATE / "rota-service.json"
STOPFILE = STATE / "rota-service.stop"
LOG = STATE / "rota-service.log"
PENDING = STATE / "review-publish-pending.json"
INTERVAL = 180.0
REVIEW_BUDGET = 150.0
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200
RUN_ID = uuid.uuid4().hex


def _pid_alive(pid, process_started=None):
    import runtime_binding
    identity = runtime_binding.process_identity(pid)
    return bool(identity and (process_started is None or str(identity) == str(process_started)))


def state():
    try:
        return json.loads(PIDFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def running():
    info = state()
    return bool(info.get("pid") and info.get("processStarted")
                and _pid_alive(info["pid"], info["processStarted"]))


def ensure_started(popen=subprocess.Popen):
    """Start one hidden service if needed. Returns ``(running, message)``."""
    STATE.mkdir(parents=True, exist_ok=True)
    import runtime_binding
    binding = runtime_binding.load()
    if not runtime_binding.alive(binding):
        return False, "no live controlling session binding"
    if running():
        owner = state().get("sessionId")
        if owner != binding.get("session_id"):
            return False, "a live rota service belongs to another session (%s)" % owner
        # Same-session restart cancels a stop request that the live service has
        # not consumed yet. Never cancel another session's lifecycle flag.
        try:
            STOPFILE.unlink()
        except OSError:
            pass
        return True, "already running"
    try:
        STOPFILE.unlink()
    except OSError:
        pass
    creationflags = (DETACHED | NO_WINDOW) if os.name == "nt" else 0
    try:
        child = popen([sys.executable, str(Path(__file__).resolve()), "serve"],
                      cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, creationflags=creationflags,
                      close_fds=True)
        # Test doubles may intentionally have no PID. Real startup waits for the
        # locked child to publish its identity/session ready record.
        if not getattr(child, "pid", None):
            return True, "started"
        until = time.monotonic() + 3.0
        while time.monotonic() < until:
            info = state()
            if (info.get("pid") == child.pid and info.get("sessionId") == binding.get("session_id")
                    and running()):
                return True, "started"
            if child.poll() is not None:
                return False, "rota service exited before becoming ready"
            time.sleep(0.05)
        _terminate_owned(child)
        return False, "rota service did not become ready within 3 seconds"
    except Exception as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def _write_state(**extra):
    STATE.mkdir(parents=True, exist_ok=True)
    import runtime_binding
    payload = {"pid": os.getpid(), "started": time.time(),
               "processStarted": runtime_binding.process_identity(os.getpid()), **extra}
    tmp = PIDFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(PIDFILE))


def _log(text):
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))


def _queue_publish(event_id, body, captured_at=None, generation=None):
    import game_session
    import runtime_binding
    rows = game_session.read(PENDING, [])
    rows.append({"event_id": event_id, "body": body,
                 "session": runtime_binding.load().get("session_id"),
                 "captured_at": captured_at, "generation": generation})
    game_session.write(PENDING, rows)


def _publish_skip(event_id, body):
    try:
        import event_bus
        result = event_bus.publish(kind="review", body=body, event_id=event_id,
                                   source="rota-reviewer", captured_at=time.time())
        if not result.get("accepted"):
            _queue_publish(event_id, body)
    except Exception as ex:
        _log("could not publish %s: %s: %s" % (event_id, type(ex).__name__, ex))
        _queue_publish(event_id, body)


def _retry_pending():
    import event_bus
    import game_session
    import runtime_binding
    rows = game_session.read(PENDING, [])
    session = runtime_binding.load().get("session_id")
    keep = []
    for row in rows:
        if row.get("session") != session:
            continue
        try:
            result = event_bus.publish(kind="review", body=row["body"],
                                       event_id=row["event_id"], source="rota-reviewer",
                                       captured_at=row.get("captured_at"),
                                       generation=row.get("generation"))
            if not result.get("accepted") and "stale" not in result.get("reason", "").lower():
                keep.append(row)
        except Exception:
            keep.append(row)
    game_session.write(PENDING, keep)


def _terminate_owned(child):
    """Terminate only the Popen-owned reviewer and its descendants."""
    if child is None or child.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=NO_WINDOW)
    else:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()


def _launch_review(slot, pass_index, popen=subprocess.Popen):
    event_id = "review-%s-%d" % (RUN_ID, int(slot * 1000))
    args = [sys.executable, str(HERE / "look.py"), "--event-id", event_id,
            "--no-verify", "--who", "sol" if pass_index % 4 in (1, 2) else "luna"]
    if pass_index % 2:
        args.append("--no-wide")
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("ab") as log:
        return popen(args, cwd=str(HERE), stdin=subprocess.DEVNULL,
                     stdout=log, stderr=log, close_fds=True,
                     creationflags=(DETACHED | NO_WINDOW) if os.name == "nt" else 0)


def schedule(clock, sleep, launch, publish, should_stop, terminate=_terminate_owned):
    """Fixed-slot loop. Dependencies are injected for deterministic tests."""
    deadline = clock()
    child = None
    child_started = None
    child_event = None
    pass_index = 0
    try:
        while not should_stop():
            now = clock()
            if (child is not None and child.poll() is None and child_started is not None
                    and now - child_started >= REVIEW_BUDGET):
                terminate(child)
                publish(child_event, "REVIEW TIMED OUT after the fixed 150-second budget; "
                        "its owned process tree was terminated before the next slot.")
                child = None
                child_started = None
                child_event = None
            if now < deadline:
                sleep(min(1.0, deadline - now))
                continue
            while deadline <= now:
                event_id = "review-%s-%d" % (RUN_ID, int(deadline * 1000))
                if child is not None and child.poll() is None:
                    terminate(child)
                    publish(child_event, "REVIEW TIMED OUT at the next fixed slot; its owned "
                            "process tree was terminated.")
                try:
                    child = launch(deadline, pass_index)
                    child_started = now
                    child_event = event_id
                except Exception as ex:
                    child = None
                    child_started = None
                    child_event = None
                    publish(event_id, "REVIEW FAILED TO START at this fixed slot: %s: %s"
                            % (type(ex).__name__, ex))
                pass_index += 1
                deadline += INTERVAL
    finally:
        if child is not None and child.poll() is None:
            terminate(child)


def serve(run=subprocess.run):
    # This repository's shared lock is the singleton authority. It is held by
    # the process for its whole lifetime, so stale PID files cannot create two
    # schedulers after a crash/restart race.
    import game_session
    with game_session.locked("rota-service"):
        import runtime_binding
        binding = runtime_binding.load()
        if not runtime_binding.alive(binding):
            return 1
        bound_session = binding.get("session_id")
        _write_state(lastPulse=None, sessionId=bound_session,
                     binding=dict(binding))
        _log("service started pid=%d" % os.getpid())
        try:
            import human_events
            def session_dead():
                current = runtime_binding.load()
                return (STOPFILE.exists() or not runtime_binding.alive(current)
                        or current.get("session_id") != bound_session)
            human_thread = threading.Thread(
                target=human_events.run, args=(session_dead,),
                name="rimworld-human-events", daemon=True)
            human_thread.start()
            import chat_events
            chat_thread = threading.Thread(
                target=chat_events.run, args=(session_dead,),
                name="rimworld-chat-events", daemon=True)
            chat_thread.start()
            def stopping():
                _retry_pending()
                return session_dead()
            schedule(time.monotonic, time.sleep, _launch_review, _publish_skip, stopping)
        finally:
            info = state()
            if info.get("pid") == os.getpid():
                try:
                    PIDFILE.unlink()
                except OSError:
                    pass
            _log("service stopped pid=%d" % os.getpid())
    return 0


def stop():
    """Request a clean stop. Never signals or forcibly terminates a process."""
    if not running():
        return True, "already stopped"
    STATE.mkdir(parents=True, exist_ok=True)
    STOPFILE.write_text(str(time.time()), encoding="ascii")
    return True, "stop requested"


def main(argv=None):
    cmd = (argv or sys.argv[1:] or ["status"])[0]
    if cmd == "serve":
        return serve()
    if cmd == "start":
        ok, message = ensure_started()
        print("rota service %s" % message)
        return 0 if ok else 1
    if cmd == "stop":
        ok, message = stop()
        print("rota service %s" % message)
        return 0 if ok else 1
    if cmd == "status":
        info = state()
        print("rota service %s%s" %
              ("running" if running() else "stopped",
               " pid=%s" % info.get("pid") if info.get("pid") else ""))
        return 0
    print("usage: rota_service.py start|stop|status")
    return 1


if __name__ == "__main__":
    sys.exit(main())
