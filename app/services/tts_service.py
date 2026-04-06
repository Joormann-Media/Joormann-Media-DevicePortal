from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any
import shutil
import os

from app.core.config import ensure_config
from app.core.netcontrol import audio_outputs_status, audio_volume_set
from app.core.paths import DATA_DIR


class TtsService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: list[subprocess.Popen[str]] = []
        self._active_file: str | None = None
        self._last_error: str | None = None
        self._duck_restore_map: dict[str, int] = {}
        self._duck_release_ms: int = 0

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

    def _resolve_tts_target_sinks(self, mixer: dict[str, Any]) -> list[str]:
        mode = str(mixer.get("tts_target_mode") or "current").strip().lower()
        target_output_id = str(mixer.get("tts_target_output_id") or "").strip()
        try:
            outputs = audio_outputs_status()
        except Exception:
            preferred = self._resolve_preferred_sink()
            return [preferred] if preferred else []
        if not isinstance(outputs, dict):
            preferred = self._resolve_preferred_sink()
            return [preferred] if preferred else []
        available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
        current_output = str(outputs.get("current_output") or "").strip()

        def _sink_for_output_id(output_id: str) -> str:
            for item in available:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() != output_id:
                    continue
                if not bool(item.get("available")):
                    return ""
                sink_name = str(item.get("sink_name") or "").strip()
                if sink_name:
                    return sink_name
            return ""

        if mode == "all":
            sinks: list[str] = []
            seen: set[str] = set()
            for item in available:
                if not isinstance(item, dict):
                    continue
                if not bool(item.get("available")):
                    continue
                sink_name = str(item.get("sink_name") or "").strip()
                if not sink_name or sink_name in seen:
                    continue
                seen.add(sink_name)
                sinks.append(sink_name)
            return sinks

        if mode == "specific":
            sink = _sink_for_output_id(target_output_id)
            if sink:
                return [sink]
            preferred = self._resolve_preferred_sink()
            return [preferred] if preferred else []

        sink = _sink_for_output_id(current_output) if current_output else ""
        if sink:
            return [sink]
        preferred = self._resolve_preferred_sink()
        return [preferred] if preferred else []

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
                cfg = ensure_config()
                mixer = cfg.get("audio_mixer") if isinstance(cfg.get("audio_mixer"), dict) else {}
                tts_volume = int(mixer.get("tts_volume_percent") or 90)
                tts_volume = max(0, min(150, tts_volume))
                mpv_cmd.append(f"--volume={tts_volume}")
                if ao_mode != "auto":
                    mpv_cmd.append(f"--ao={ao_mode}")
                mpv_cmd.append(str(path))
                env = self._pulse_env()
                target_sinks = self._resolve_tts_target_sinks(mixer)
                self._apply_ducking(target_sinks=target_sinks, mixer=mixer)
                processes: list[subprocess.Popen[str]] = []
                if not target_sinks:
                    target_sinks = [""]
                for sink_name in target_sinks:
                    target_env = dict(env)
                    if sink_name:
                        target_env["PULSE_SINK"] = sink_name
                    proc = subprocess.Popen(
                        mpv_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=target_env,
                    )
                    processes.append(proc)
                self._processes = processes
                self._active_file = str(path)
                self._last_error = None
            except Exception as exc:
                self._processes = []
                self._active_file = None
                self._last_error = str(exc)
                self._restore_ducking()
                return {"ok": False, "message": f"TTS-Playback Start fehlgeschlagen: {exc}"}

            threading.Thread(target=self._watcher, args=(list(self._processes),), daemon=True).start()
            return {"ok": True, "message": "TTS gestartet", "file_path": self._active_file}

    def _watcher(self, procs: list[subprocess.Popen[str]]) -> None:
        if not procs:
            return
        failure_detail = ""
        for proc in procs:
            stderr_text = ""
            try:
                _stdout_ignored, stderr_text = proc.communicate(timeout=120)
            except Exception:
                continue
            if proc.returncode not in (None, 0):
                detail = (stderr_text or "").strip()
                if detail:
                    failure_detail = f"mpv exit {proc.returncode}: {detail[:300]}"
                else:
                    failure_detail = f"mpv exit {proc.returncode}"
        with self._lock:
            self._processes = []
            if failure_detail:
                self._last_error = failure_detail
            self._restore_ducking()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._processes:
                return {"ok": True, "message": "TTS bereits gestoppt"}
            for proc in list(self._processes):
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._processes = []
            self._active_file = None
            self._restore_ducking()
            return {"ok": True, "message": "TTS gestoppt"}

    def status(self) -> dict[str, Any]:
        running = any(proc.poll() is None for proc in self._processes)
        pid = None
        for proc in self._processes:
            if proc.poll() is None:
                pid = proc.pid
                break
        return {
            "running": running,
            "file_path": self._active_file,
            "pid": pid,
            "last_error": self._last_error,
        }

    def _apply_ducking(self, target_sinks: list[str], mixer: dict[str, Any]) -> None:
        self._duck_restore_map = {}
        self._duck_release_ms = 0
        if not bool(mixer.get("ducking_enabled", True)):
            return
        duck_level = int(mixer.get("ducking_level_percent") or 30)
        duck_level = max(0, min(100, duck_level))
        self._duck_release_ms = max(0, min(30000, int(mixer.get("ducking_release_ms") or 450)))
        try:
            outputs = audio_outputs_status()
        except Exception:
            return
        available = outputs.get("available_outputs") if isinstance(outputs.get("available_outputs"), list) else []
        target_set = {str(s or "").strip() for s in target_sinks if str(s or "").strip()}
        for item in available:
            if not isinstance(item, dict):
                continue
            target_sink = str(item.get("sink_name") or "").strip()
            if not target_sink:
                continue
            if target_sink in target_set:
                continue
            current_vol = item.get("volume_percent")
            try:
                current = int(current_vol)
            except Exception:
                current = None
            if current is None:
                continue
            self._duck_restore_map[target_sink] = current
            if current > duck_level:
                try:
                    audio_volume_set(target_sink, duck_level)
                except Exception:
                    pass

    def _restore_ducking(self) -> None:
        if not self._duck_restore_map:
            return
        if self._duck_release_ms > 0:
            time.sleep(self._duck_release_ms / 1000.0)
        for sink_name, volume in list(self._duck_restore_map.items()):
            try:
                audio_volume_set(sink_name, int(volume))
            except Exception:
                pass
        self._duck_restore_map = {}
        self._duck_release_ms = 0


tts_service = TtsService()
