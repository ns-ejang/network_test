#!/usr/bin/env python3
"""Supervisor that keeps the web UI alive while app.py can be started/stopped."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

DIR = Path(__file__).resolve().parent
SUPERVISOR_PORT = int(os.environ.get("PORT", "8080"))
WORKER_PORT = int(os.environ.get("WORKER_PORT", str(SUPERVISOR_PORT + 1)))

worker: subprocess.Popen | None = None
worker_lock = threading.Lock()

app = Flask(__name__, template_folder=str(DIR / "templates"))


def worker_base() -> str:
    return f"http://127.0.0.1:{WORKER_PORT}"


def worker_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{worker_base()}/api/health", timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def worker_managed() -> bool:
    return worker is not None and worker.poll() is None


def worker_active() -> bool:
    return worker_healthy()


def pid_on_port(port: int) -> int | None:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return int(out.splitlines()[0])
    except (subprocess.CalledProcessError, ValueError, IndexError, FileNotFoundError):
        pass
    return None


def kill_port_process(port: int) -> bool:
    pid = pid_on_port(port)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGINT)
        for _ in range(20):
            if pid_on_port(port) is None:
                return True
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
        return pid_on_port(port) is None
    except ProcessLookupError:
        return True


def wait_for_worker(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if worker_healthy():
            return True
        if worker is not None and worker.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def start_worker(force: bool = False) -> tuple[bool, str]:
    global worker

    if worker_healthy() and not force:
        return True, "already running"

    with worker_lock:
        if worker_managed():
            return True, "already running"

        if pid_on_port(WORKER_PORT) is not None:
            if not kill_port_process(WORKER_PORT):
                return False, f"port {WORKER_PORT} is already in use"

        worker = subprocess.Popen(
            [
                sys.executable,
                str(DIR / "app.py"),
                "--port",
                str(WORKER_PORT),
                "--host",
                "127.0.0.1",
            ],
            cwd=str(DIR),
        )

    if wait_for_worker():
        return True, "started"

    with worker_lock:
        if worker and worker.poll() is None:
            worker.kill()
        worker = None

    if pid_on_port(WORKER_PORT):
        return False, f"port {WORKER_PORT} is already in use"
    return False, "failed to start app.py"


def stop_worker() -> tuple[bool, str]:
    global worker

    with worker_lock:
        managed = worker_managed()
        proc = worker if managed else None

    if managed and proc is not None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        with worker_lock:
            worker = None
        return True, "stopped"

    if worker_healthy() or pid_on_port(WORKER_PORT) is not None:
        kill_port_process(WORKER_PORT)
        with worker_lock:
            worker = None
        return True, "stopped"

    with worker_lock:
        worker = None
    return True, "already stopped"


def proxy_to_worker(path: str) -> Response:
    if not worker_active():
        return jsonify(
            {
                "error": "Controller(app.py)가 중지되어 있습니다. 상단에서 시작 버튼을 눌러주세요.",
            }
        ), 503

    url = f"{worker_base()}{path}"
    if request.query_string:
        url = f"{url}?{request.query_string.decode()}"

    if path.endswith("/stream"):
        def generate():
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    while True:
                        chunk = resp.read(1024)
                        if not chunk:
                            break
                        yield chunk
            except Exception:
                return

        return Response(generate(), content_type="text/event-stream")

    data = request.get_data() if request.method in {"POST", "PUT", "PATCH"} else None
    headers = {}
    if data and request.content_type:
        headers["Content-Type"] = request.content_type

    req = urllib.request.Request(url, data=data, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "application/json")
            return Response(body, status=resp.status, content_type=content_type)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        content_type = exc.headers.get("Content-Type", "application/json")
        return Response(body, status=exc.code, content_type=content_type)


@app.route("/")
def index() -> str:
    return render_template("index.html", scripts={})


@app.route("/api/server/status")
def server_status():
    running = worker_active()
    return jsonify(
        {
            "running": running,
            "mode": "supervisor",
            "supervisor_port": SUPERVISOR_PORT,
            "worker_port": WORKER_PORT if running else None,
        }
    )


@app.route("/api/server/start", methods=["POST"])
def server_start():
    ok, message = start_worker()
    return jsonify({"ok": ok, "message": message, "running": worker_active()})


@app.route("/api/server/stop", methods=["POST"])
def server_stop():
    ok, message = stop_worker()
    return jsonify({"ok": ok, "message": message, "running": worker_active()})


@app.route("/api/server/restart", methods=["POST"])
def server_restart():
    stop_worker()
    ok, message = start_worker(force=True)
    return jsonify({"ok": ok, "message": message, "running": worker_active()})


@app.route("/api/<path:subpath>", methods=["GET", "POST"])
def proxy_api(subpath: str):
    if subpath.startswith("server/"):
        return jsonify({"error": "Not found"}), 404
    return proxy_to_worker(f"/api/{subpath}")


if __name__ == "__main__":
    if not worker_healthy():
        start_worker()
    app.run(host="0.0.0.0", port=SUPERVISOR_PORT, debug=False, threaded=True)
