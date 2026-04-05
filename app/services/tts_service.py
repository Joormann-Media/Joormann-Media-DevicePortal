from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any
import shutil
import os

from app.core.netcontrol import audio_outputs_status
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

    def _pico2wave_path(self) -> str | None:
        return shutil.which("pico2wave")

    def _pulse_env(self) -> dict[str, str]:
        env = dict(os.environ)
        uid = os.getuid()
        runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        env["XDG_RUNTIME_DIR"] = runtime_dir
        pulse_socket = f"{runtime_dir}/pulse/native"
        if os.path.exists(pulse_socket):
            env["PULSE_SERVER"] = f"unix:{pulse_socket}"
        return env

    def _resolve_preferred_sink(self) -> str:
        try:
            outputs = audio_outputs_status()
        except Exception:
            return ""
        if not isinstance(outputs, dict):
            return ""
        current = str(outputs.get("current_output") or "").strip()
        available = outputs.get("available_outputs")
        if not isinstance(available, list):
            return ""
        if current:
            for item in available:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != current:
                    continue
                sink_name = str(item.get("sink_name") or "").strip()
                if sink_name:
                    return sink_name
        # fallback: first available local output sink
        for item in available:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("available")):
                continue
            sink_name = str(item.get("sink_name") or "").strip()
            if sink_name:
                return sink_name
        return ""

    def speak(self, text: str = "", file_path: str = "") -> dict[str, Any]:
        text = (text or "").strip()
        file_path = (file_path or "").strip()
        if not file_path and text:
            espeak = self._espeak_path()
            out_path = Path(DATA_DIR) / "audio" / "tts_last.wav"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if espeak:
                try:
                    proc = subprocess.run([espeak, "-w", str(out_path), text], capture_output=True, text=True, timeout=15)
                except subprocess.TimeoutExpired:
                    self._last_error = "TTS-Generierung Timeout."
                    return {"ok": False, "message": "TTS-Generierung Timeout."}
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or "TTS-Generierung fehlgeschlagen").strip()
                    self._last_error = err
                    return {"ok": False, "message": err}
            else:
                pico2wave = self._pico2wave_path()
                if not pico2wave:
                    return {"ok": False, "message": "Kein TTS-Backend installiert (espeak oder pico2wave erforderlich)"}
                try:
                    proc = subprocess.run([pico2wave, "-w", str(out_path), text], capture_output=True, text=True, timeout=20)
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

        ao_mode = str(os.environ.get("DEVICEPORTAL_TTS_AO") or "auto").strip().lower()
        if ao_mode not in ("auto", "pulse", "alsa"):
            ao_mode = "auto"

        with self._lock:
            self.stop()
            try:
                mpv_cmd = [mpv, "--no-video", "--really-quiet", "--idle=no"]
                if ao_mode != "auto":
                    mpv_cmd.append(f"--ao={ao_mode}")
                mpv_cmd.append(str(path))
                env = self._pulse_env()
                sink_name = self._resolve_preferred_sink()
                if sink_name:
                    env["PULSE_SINK"] = sink_name
                self._process = subprocess.Popen(
                    mpv_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
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
        stderr_text = ""
        try:
            _stdout_ignored, stderr_text = proc.communicate(timeout=120)
        except Exception:
            return
        with self._lock:
            if proc.returncode not in (None, 0):
                detail = (stderr_text or "").strip()
                if detail:
                    self._last_error = f"mpv exit {proc.returncode}: {detail[:300]}"
                else:
                    self._last_error = f"mpv exit {proc.returncode}"
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
