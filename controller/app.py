#!/usr/bin/env python3
"""Web controller for network test shell scripts."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent.parent

SCRIPTS: dict[str, dict[str, str]] = {
    "extract_urllist_fromCCI": {
        "label": "GenAI URL Test (CCI)",
        "folder": "extract_urllist_fromCCI",
        "script": "genai_test.sh",
    },
    "extract_urllist_fromCCI_jp": {
        "label": "GenAI URL Test (CCI JP)",
        "folder": "extract_urllist_fromCCI_jp",
        "script": "genai_test.sh",
    },
    "malicious_sites": {
        "label": "Malicious Sites Test",
        "folder": "malicious_sites",
        "script": "web_test.sh",
    },
    "npa": {
        "label": "NPA Network Test",
        "folder": "npa",
        "script": "npa_test.sh",
    },
}

MAX_LOG_LINES = 2000
TRAFFIC_WINDOW_SEC = 60


@dataclass
class Job:
    script_id: str
    process: subprocess.Popen[str] | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    status: str = "idle"
    started_at: float | None = None
    total_bytes: int = 0
    traffic_samples: deque[tuple[float, int]] = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader_thread: threading.Thread | None = None

    def append_log(self, line: str) -> None:
        with self.lock:
            self.logs.append(line)

    def record_traffic(self, byte_count: int) -> None:
        now = time.time()
        with self.lock:
            self.total_bytes += byte_count
            self.traffic_samples.append((now, byte_count))
            cutoff = now - TRAFFIC_WINDOW_SEC
            while self.traffic_samples and self.traffic_samples[0][0] < cutoff:
                self.traffic_samples.popleft()

    def reset_traffic(self) -> None:
        with self.lock:
            self.total_bytes = 0
            self.traffic_samples.clear()

    def get_traffic(self) -> dict[str, float | int]:
        now = time.time()
        cutoff = now - TRAFFIC_WINDOW_SEC
        with self.lock:
            minute_bytes = sum(b for ts, b in self.traffic_samples if ts >= cutoff)
            total_bytes = self.total_bytes
        return {
            "total_bytes": total_bytes,
            "minute_bytes": minute_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 3),
            "minute_mb": round(minute_bytes / (1024 * 1024), 3),
        }

    def get_logs(self, offset: int = 0) -> tuple[list[str], int]:
        with self.lock:
            lines = list(self.logs)
        if offset < 0:
            offset = 0
        if offset >= len(lines):
            return [], len(lines)
        return lines[offset:], len(lines)


jobs: dict[str, Job] = {script_id: Job(script_id=script_id) for script_id in SCRIPTS}
jobs_lock = threading.Lock()

app = Flask(__name__)


def _script_path(script_id: str) -> Path:
    meta = SCRIPTS[script_id]
    return BASE_DIR / meta["folder"] / meta["script"]


def _process_line(job: Job, line: str) -> None:
    if line.startswith("[TRAFFIC] "):
        try:
            job.record_traffic(int(line.split()[1]))
        except (ValueError, IndexError):
            pass
        return
    job.append_log(line)


def _read_output(job: Job) -> None:
    process = job.process
    if process is None or process.stdout is None:
        return

    try:
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            _process_line(job, line.rstrip("\n"))
    finally:
        exit_code = process.wait()
        job.append_log(f"--- Process exited with code {exit_code} ---")
        with jobs_lock:
            job.status = "idle"
            job.process = None
            job.started_at = None


@app.route("/")
def index() -> str:
    return render_template("index.html", scripts=SCRIPTS)


@app.route("/api/scripts")
def list_scripts():
    payload = []
    for script_id, meta in SCRIPTS.items():
        job = jobs[script_id]
        traffic = job.get_traffic()
        with job.lock:
            payload.append(
                {
                    "id": script_id,
                    "label": meta["label"],
                    "folder": meta["folder"],
                    "script": meta["script"],
                    "status": job.status,
                    "started_at": job.started_at,
                    "log_count": len(job.logs),
                    **traffic,
                }
            )
    return jsonify(payload)


@app.route("/api/traffic")
def get_traffic():
    payload = []
    total_all = 0
    minute_all = 0
    for script_id, meta in SCRIPTS.items():
        job = jobs[script_id]
        traffic = job.get_traffic()
        total_all += traffic["total_bytes"]
        minute_all += traffic["minute_bytes"]
        payload.append(
            {
                "id": script_id,
                "label": meta["label"],
                "status": job.status,
                **traffic,
            }
        )
    return jsonify(
        {
            "scripts": payload,
            "total_mb": round(total_all / (1024 * 1024), 3),
            "minute_mb": round(minute_all / (1024 * 1024), 3),
        }
    )


@app.route("/api/scripts/<script_id>/start", methods=["POST"])
def start_script(script_id: str):
    if script_id not in SCRIPTS:
        return jsonify({"error": "Unknown script"}), 404

    script = _script_path(script_id)
    if not script.exists():
        return jsonify({"error": f"Script not found: {script}"}), 404

    with jobs_lock:
        job = jobs[script_id]
        if job.process is not None and job.process.poll() is None:
            return jsonify({"error": "Already running"}), 409

        job.logs.clear()
        job.reset_traffic()
        job.status = "running"
        job.started_at = time.time()
        job.append_log(f"--- Starting {script.name} in {script.parent.name} ---")

        try:
            job.process = subprocess.Popen(
                ["bash", script.name],
                cwd=str(script.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )
        except OSError as exc:
            job.status = "idle"
            job.started_at = None
            job.process = None
            job.append_log(f"--- Failed to start: {exc} ---")
            return jsonify({"error": str(exc)}), 500

        job.reader_thread = threading.Thread(
            target=_read_output, args=(job,), daemon=True
        )
        job.reader_thread.start()

    return jsonify({"ok": True, "status": "running"})


@app.route("/api/scripts/<script_id>/stop", methods=["POST"])
def stop_script(script_id: str):
    if script_id not in SCRIPTS:
        return jsonify({"error": "Unknown script"}), 404

    with jobs_lock:
        job = jobs[script_id]
        process = job.process
        if process is None or process.poll() is not None:
            job.status = "idle"
            job.process = None
            job.started_at = None
            return jsonify({"ok": True, "status": "idle"})

        job.status = "stopping"
        job.append_log("--- Sending stop signal (SIGINT) ---")

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGINT)
        except ProcessLookupError:
            job.status = "idle"
            job.process = None
            job.started_at = None
            return jsonify({"ok": True, "status": "idle"})

    return jsonify({"ok": True, "status": "stopping"})


@app.route("/api/scripts/<script_id>/logs")
def get_logs(script_id: str):
    if script_id not in SCRIPTS:
        return jsonify({"error": "Unknown script"}), 404

    offset = request.args.get("offset", default=0, type=int)
    job = jobs[script_id]
    lines, total = job.get_logs(offset)
    return jsonify({"lines": lines, "total": total, "status": job.status})


@app.route("/api/scripts/<script_id>/stream")
def stream_logs(script_id: str):
    if script_id not in SCRIPTS:
        return jsonify({"error": "Unknown script"}), 404

    def event_stream() -> Iterator[str]:
        job = jobs[script_id]
        offset = 0
        idle_ticks = 0

        while True:
            lines, total = job.get_logs(offset)
            if lines:
                idle_ticks = 0
                for line in lines:
                    yield f"data: {line}\n\n"
                offset = total
            else:
                idle_ticks += 1

            yield f"event: status\ndata: {job.status}\n\n"
            traffic = job.get_traffic()
            yield f"event: traffic\ndata: {traffic['total_mb']},{traffic['minute_mb']}\n\n"

            if job.status == "idle" and offset >= total:
                if idle_ticks >= 3:
                    break
            idle_ticks = min(idle_ticks, 10)
            time.sleep(0.5)

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/server/status")
def server_status():
    return jsonify(
        {
            "running": True,
            "mode": "direct",
            "supervisor_port": None,
            "worker_port": int(os.environ.get("PORT", "8080")),
        }
    )


@app.route("/api/server/stop", methods=["POST"])
def server_stop():
    def _shutdown() -> None:
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"ok": True, "message": "stopping", "running": False})


@app.route("/api/server/restart", methods=["POST"])
def server_restart():
    def _restart() -> None:
        time.sleep(0.3)
        os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"ok": True, "message": "restarting", "running": True})


def main() -> None:
    parser = argparse.ArgumentParser(description="Network test web controller")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    args = parser.parse_args()
    os.environ["PORT"] = str(args.port)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
