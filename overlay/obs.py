#!/usr/bin/env python3
"""Talk to a running OBS through obs-websocket (v5 protocol).

Exists because OBS only reads its config files at launch and only writes them
at exit -- while it runs, this socket is the sole way to see or change its
actual state. M enabled the server 2026-08-31.

    python obs.py status                     what OBS is doing right now
    python obs.py screenshot [out.png]       the composed program view, as a file
    python obs.py items                      scene items of the current scene
    python obs.py raw <RequestType> ['{json}']   any obs-websocket request

Credentials are read live from OBS's own config
(%APPDATA%/obs-studio/plugin_config/obs-websocket/config.json), so there is
nothing to keep in sync. If OBS is closed, this prints one line and exits 1 --
fall back to editing the files on disk, which is what OBS reads at launch.

Needs the `websocket-client` package (installed 2026-08-31:
`python -m pip install websocket-client`).
"""
import base64
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

try:
    import websocket
except ImportError:
    sys.exit("websocket-client is not installed: python -m pip install websocket-client")

CONFIG = Path(os.environ["APPDATA"]) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"


def connect():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except OSError:
        sys.exit(f"no obs-websocket config at {CONFIG}")
    if not cfg.get("server_enabled"):
        sys.exit("obs-websocket server is disabled (Tools > WebSocket Server Settings)")
    port = cfg.get("server_port", 4455)
    try:
        ws = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=5)
    except (ConnectionRefusedError, OSError):
        sys.exit("OBS is not running (or the websocket server isn't listening) -- edit the files on disk instead")

    hello = json.loads(ws.recv())
    ident = {"rpcVersion": 1}
    auth = hello.get("d", {}).get("authentication")
    if auth:
        pw = cfg.get("server_password", "")
        secret = base64.b64encode(hashlib.sha256((pw + auth["salt"]).encode()).digest()).decode()
        ident["authentication"] = base64.b64encode(
            hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
    ws.send(json.dumps({"op": 1, "d": ident}))
    reply = json.loads(ws.recv())
    if reply.get("op") != 2:
        sys.exit(f"auth failed: {reply}")
    return ws


def request(ws, req_type, data=None):
    rid = str(uuid.uuid4())
    ws.send(json.dumps({"op": 6, "d": {"requestType": req_type, "requestId": rid,
                                       "requestData": data or {}}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
            d = msg["d"]
            if not d["requestStatus"]["result"]:
                sys.exit(f"{req_type} failed: {d['requestStatus']}")
            return d.get("responseData", {})


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    ws = connect()
    try:
        if cmd == "status":
            v = request(ws, "GetVersion")
            scene = request(ws, "GetCurrentProgramScene")
            stream = request(ws, "GetStreamStatus")
            rec = request(ws, "GetRecordStatus")
            print(f"OBS {v['obsVersion']} (ws {v['obsWebSocketVersion']})")
            print(f"scene    : {scene['currentProgramSceneName']}")
            print(f"streaming: {stream['outputActive']}"
                  + (f" ({stream['outputTimecode']})" if stream["outputActive"] else ""))
            print(f"recording: {rec['outputActive']}")
        elif cmd == "screenshot":
            out = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).parent / "obs-preview.png")
            scene = request(ws, "GetCurrentProgramScene")["currentProgramSceneName"]
            shot = request(ws, "GetSourceScreenshot",
                           {"sourceName": scene, "imageFormat": "png",
                            "imageWidth": 1920, "imageHeight": 1080})
            b64 = shot["imageData"].split(",", 1)[1]
            Path(out).write_bytes(base64.b64decode(b64))
            print(out)
        elif cmd == "items":
            scene = request(ws, "GetCurrentProgramScene")["currentProgramSceneName"]
            for it in request(ws, "GetSceneItemList", {"sceneName": scene})["sceneItems"]:
                print(f"{it['sceneItemId']}: {it['sourceName']}"
                      f" {'shown' if it['sceneItemEnabled'] else 'HIDDEN'}")
        elif cmd == "raw":
            data = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            print(json.dumps(request(ws, sys.argv[2], data), indent=2))
        else:
            sys.exit(__doc__)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
