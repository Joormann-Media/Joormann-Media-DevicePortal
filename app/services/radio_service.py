from __future__ import annotations

import signal
import subprocess
import threading
import time
from typing import Any
import shutil


class RadioService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._stream_url: str | None = None
        self._last_error: str | None = None

    def _mpv_path(self) -> str | None:
        return shutil.which("mpv")

    def play(self, stream_url: str) -> dict[str, Any]:
        url = (stream_url or "").strip().replace("&amp;", "&")
        if not url:
            return {"ok": False, "message": "stream_url fehlt"}
        mpv = self._mpv_path()
        if not mpv:
            return {"ok": False, "message": "mpv ist nicht installiert"}

        with self._lock:
            self.stop()
            is_rtsp = url.lower().startswith("rtsp://")
            attempts: list[list[str]] = [
                [
                    mpv,
                    "--no-video",
                    "--no-terminal",
                    "--no-ytdl",
                    "--really-quiet",
                    "--idle=no",
                    "--ao=pulse",
                    url,
                ]
            ]
            if is_rtsp:
                attempts.insert(
                    0,
                    [
                        mpv,
                        "--no-video",
                        "--no-terminal",
                        "--no-ytdl",
                        "--really-quiet",
                        "--idle=no",
                        "--ao=pulse",
                        "--rtsp-transport=tcp",
                        url,
                    ],
                )

            last_error = "mpv hat den Stream nicht akzeptiert."
            for cmd in attempts:
                try:
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    self._stream_url = url
                except Exception as exc:
                    self._process = None
                    self._stream_url = None
                    last_error = str(exc)
                    continue

                time.sleep(0.35)
                if self._process.poll() is None:
                    self._last_error = None
                    return {"ok": True, "message": "Radio gestartet", "stream_url": url}

                stderr = ""
                try:
                    stderr = (self._process.stderr.read() if self._process.stderr else "") or ""
                except Exception:
                    stderr = ""

                last_error = stderr.strip() or last_error
                self._process = None
                self._stream_url = None

            self._last_error = last_error
            return {"ok": False, "message": f"Radio-Start fehlgeschlagen: {last_error}", "stream_url": url}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None:
                return {"ok": True, "message": "Radio bereits gestoppt"}

            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

            self._process = None
            self._stream_url = None
            return {"ok": True, "message": "Radio gestoppt"}

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None:
                return {"ok": False, "message": "Radio laeuft nicht"}
            try:
                self._process.send_signal(signal.SIGSTOP)
                return {"ok": True, "message": "Radio pausiert"}
            except Exception as exc:
                return {"ok": False, "message": f"Pause fehlgeschlagen: {exc}"}

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None:
                if self._stream_url:
                    return self.play(self._stream_url)
                return {"ok": False, "message": "Keine Radio-Session vorhanden"}
            try:
                self._process.send_signal(signal.SIGCONT)
                return {"ok": True, "message": "Radio fortgesetzt"}
            except Exception as exc:
                return {"ok": False, "message": f"Resume fehlgeschlagen: {exc}"}

    def status(self) -> dict[str, Any]:
        running = self._process is not None and self._process.poll() is None
        return {
            "running": running,
            "stream_url": self._stream_url,
            "pid": self._process.pid if running and self._process is not None else None,
            "last_error": self._last_error,
        }


radio_service = RadioService()
