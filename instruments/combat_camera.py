"""A quiet, combat-scoped camera director.

The director only lives while ``combat.advance`` is blocked inside the game's
event waiter.  Every interval it frames the participating drafted pawns and
nearby hostiles.  Any camera movement it did not make itself permanently
relinquishes control for that pulse, so a human pan or zoom always wins.
"""
import contextlib
import math
import threading
import time

import camlock
import rim
import status as colony_status

INTERVAL_SECONDS = 8.0
HOSTILE_RADIUS = 20.0
MARGIN_CELLS = 5
CAMERA_EPSILON = 0.15
# User-tunable overview floor. RimWorld rootSize is roughly half the viewport's
# height in cells; larger values are farther out. This deliberately starts wide
# and can be replaced with M's measured preferred value.
WIDE_ROOT_SIZE = 25.5292435
COMPACT_ROOT_SIZE = 13.3315439
COMPACT_MAX_SPAN = 15.0


def participant_ids(ledger):
    """Session pawns intentionally involved in combat, without broad roster scans."""
    ids = set((ledger.get("orders") or {}).keys())
    ids.update((ledger.get("draftObligations") or {}).keys())
    ids.update(pid for pid, row in (ledger.get("pawns") or {}).items()
               if row.get("originalDrafted"))
    return sorted(ids)


def _position(row):
    p = row.get("position") or {}
    try:
        return float(p["x"]), float(p["z"])
    except (KeyError, TypeError, ValueError):
        return None


def _pawn_id(row):
    value = row.get("pawnId") or row.get("thingId")
    return str(value) if value is not None else None


def combat_bounds(snapshot, participant_ids, hostile_radius=HOSTILE_RADIUS):
    """Return bounds for live drafted participants and hostiles near any one."""
    ids = {str(x) for x in participant_ids}
    friendlies = []
    for row in snapshot.get("colonists") or []:
        if _pawn_id(row) in ids and row.get("drafted") and _position(row):
            friendlies.append(_position(row))
    if not friendlies:
        return None
    points = list(friendlies)
    radius2 = float(hostile_radius) ** 2
    for row in (snapshot.get("threats") or {}).get("hostiles") or []:
        pos = _position(row)
        if pos and any((pos[0] - x) ** 2 + (pos[1] - z) ** 2 <= radius2
                       for x, z in friendlies):
            points.append(pos)
    return {"minX": int(math.floor(min(p[0] for p in points))),
            "maxX": int(math.ceil(max(p[0] for p in points))),
            "minZ": int(math.floor(min(p[1] for p in points))),
            "maxZ": int(math.ceil(max(p[1] for p in points)))}


def _camera_signature(state):
    """Stable geometry only; tolerate harmless extra bridge metadata."""
    state = state or {}
    camera = state.get("camera") if isinstance(state.get("camera"), dict) else state
    rect = camera.get("viewRect") or camera.get("visibleRect") or {}
    center = camera.get("position") or camera.get("center") or {}
    def number(*values):
        for value in values:
            if isinstance(value, (int, float)):
                return float(value)
        return None
    rect_x = (number(rect.get("minX")) + number(rect.get("maxX"))) / 2.0 \
        if number(rect.get("minX")) is not None and number(rect.get("maxX")) is not None else None
    rect_z = (number(rect.get("minZ")) + number(rect.get("maxZ"))) / 2.0 \
        if number(rect.get("minZ")) is not None and number(rect.get("maxZ")) is not None else None
    return (number(center.get("x"), camera.get("x"), rect.get("centerX"), rect_x),
            number(center.get("z"), camera.get("z"), rect.get("centerZ"), rect_z),
            number(camera.get("rootSize"), camera.get("zoom"),
                   (camera.get("config") or {}).get("rootSize")))


def _changed(actual, expected, epsilon=CAMERA_EPSILON):
    if expected is None or actual is None:
        return False
    for have, want in zip(actual, expected):
        if have is not None and want is not None and abs(have - want) > epsilon:
            return True
    return False


class CombatCameraDirector:
    def __init__(self, participant_ids, interval=INTERVAL_SECONDS,
                 wide_root_size=WIDE_ROOT_SIZE,
                 compact_root_size=COMPACT_ROOT_SIZE,
                 compact_max_span=COMPACT_MAX_SPAN, snapshot_reader=None,
                 camera_reader=None, framer=None):
        self.participant_ids = tuple(str(x) for x in participant_ids)
        self.interval = float(interval)
        self.wide_root_size = float(wide_root_size)
        self.compact_root_size = float(compact_root_size)
        self.compact_max_span = float(compact_max_span)
        self.snapshot_reader = snapshot_reader or (lambda: colony_status.read(detail=True))
        self.camera_reader = camera_reader or (lambda: rim.game(
            "rimworld/get_camera_state", {}, strict=False))
        self.framer = framer or (lambda bounds: rim.game(
            "rimworld/frame_cell_rect",
            dict(bounds, marginCells=MARGIN_CELLS,
                 rootSize=self._root_size(bounds)), strict=False))
        self._stop = threading.Event()
        self._thread = None
        self._expected = None
        self._started_at = None
        self.relinquished = False
        self.manual_override = False

    def _root_size(self, bounds):
        # Fit the vertical span and conservatively account for a widescreen
        # viewport. Never choose a closer view than the configured overview.
        raw_height = bounds["maxZ"] - bounds["minZ"]
        raw_width = bounds["maxX"] - bounds["minX"]
        floor = (self.compact_root_size
                 if max(raw_height, raw_width) < self.compact_max_span
                 else self.wide_root_size)
        height = raw_height + 2 * MARGIN_CELLS
        width = raw_width + 2 * MARGIN_CELLS
        return max(floor, height / 2.0, width / 3.0)

    def start(self):
        if not self.participant_ids:
            return self
        try:
            self._expected = _camera_signature(self.camera_reader())
        except Exception:
            # Camera presentation must never prevent guarded combat time.
            self.relinquished = True
            return self
        # Do not claim the camera or mark it continuously moving: either would
        # block ordinary Hands commands. Use claims only as an interrupt signal.
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run,
                                        name="combat-camera", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                interrupted, _ = camlock.interrupted(self._started_at)
                actual = _camera_signature(self.camera_reader())
                if interrupted or _changed(actual, self._expected):
                    self.relinquished = True
                    self.manual_override = True
                    return
                bounds = combat_bounds(self.snapshot_reader(), self.participant_ids)
                if not bounds:
                    continue
                self.framer(bounds)
                self._expected = _camera_signature(self.camera_reader())
            except Exception:
                # Fail silent and yield; this is a view aid, never a safety gate.
                self.relinquished = True
                return

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(1.0, self.interval + 0.1))


@contextlib.contextmanager
def directing(participant_ids, **kwargs):
    director = CombatCameraDirector(participant_ids, **kwargs).start()
    try:
        yield director
    finally:
        director.stop()
