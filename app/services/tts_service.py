from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any
import shutil

from app.core.paths import DATA_DIR


class TtsService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._active_file: str | None = None
        self._last_error: str | None = None

    def _mpv_path(self) -> str | None:
        return shutil.which("mpv")

    def _espeak_path(self) -> str | None:
        return shutil.which("espeak")

    def speak(self, text: str = "", file_path: str = "") -> dict[str, Any]:
        text = (text or "").strip()
        file_path = (file_path or "").strip()
        if not file_path and text:
            espeak = self._espeak_path()
            if not espeak:
                return {"ok": False, "message": "espeak ist nicht installiert"}
            out_path = Path(DATA_DIR) / "audio" / "tts_last.wav"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                proc = subprocess.run([espeak, "-w", str(out_path), text], capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired:
                self._last_error = "TTS-Generierung Timeout."
                return {"ok": False, "message": "TTS-Generierung Timeout."}
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "TTS-Generierung fehlgeschlagen").strip()
                self._last_error = err
                return {"ok": False, "message": err}
            file_path = str(out_path)

        if not file_path:
            return {"ok": False, "message": "file_path oder text erforderlich"}

        path = Path(file_path)
        if not path.exists():
            return {"ok": False, "message": f"TTS-Datei nicht gefunden: {file_path}"}

        mpv = self._mpv_path()
        if not mpv:
            return {"ok": False, "message": "mpv ist nicht installiert"}

        with self._lock:
            self.stop()
            try:
                self._process = subprocess.Popen(
                    [mpv, "--no-video", "--really-quiet", "--idle=no", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                self._active_file = str(path)
                self._last_error = None
            except Exception as exc:
                self._process = None
                self._active_file = None
                self._last_error = str(exc)
                return {"ok": False, "message": f"TTS-Playback Start fehlgeschlagen: {exc}"}

            threading.Thread(target=self._watcher, daemon=True).start()
            return {"ok": True, "message": "TTS gestartet", "file_path": self._active_file}

    def _watcher(self) -> None:
        proc = self._process
        if proc is None:
            return
        try:
            proc.wait(timeout=120)
        except Exception:
            return
        with self._lock:
            self._process = None

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None:
                return {"ok": True, "message": "TTS bereits gestoppt"}
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            self._active_file = None
            return {"ok": True, "message": "TTS gestoppt"}

    def status(self) -> dict[str, Any]:
        running = self._process is not None and self._process.poll() is None
        return {
            "running": running,
            "file_path": self._active_file,
            "pid": self._process.pid if running and self._process is not None else None,
            "last_error": self._last_error,
        }


tts_service = TtsService()
