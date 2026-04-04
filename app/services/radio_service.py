from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from typing import Any
import shutil

from app.core.config import ensure_config
from app.core.timeutil import utc_now


logger = logging.getLogger(__name__)


class RadioService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._stream_url: str | None = None
        self._playback_url: str | None = None
        self._last_error: str | None = None
        self._rtsp_adapter_process: subprocess.Popen[str] | None = None
        self._rtsp_adapter_source_url: str | None = None
        self._rtsp_adapter_target_url: str | None = None
        self._rtsp_adapter_started_at: str | None = None
        self._rtsp_adapter_last_error: str | None = None
        self._rtsp_adapter_last_stderr: str | None = None
        self._rtsp_adapter_last_exit_code: int | None = None
        self._mpv_last_stderr: str | None = None

    def _mpv_path(self) -> str | None:
        return shutil.which("mpv")

    def _ffmpeg_path(self, configured: str) -> str | None:
        candidate = str(configured or "").strip()
        if candidate:
            if os.path.isabs(candidate):
                return candidate if os.path.exists(candidate) else None
            found = shutil.which(candidate)
            if found:
                return found
        return shutil.which("ffmpeg")

    def _adapter_config(self) -> dict[str, Any]:
        cfg = ensure_config()
        section = cfg.get("radio_rtsp_adapter") if isinstance(cfg.get("radio_rtsp_adapter"), dict) else {}
        ffmpeg_bin = str(section.get("ffmpeg_bin") or os.getenv("RADIO_RTSP_ADAPTER_FFMPEG_BIN") or "ffmpeg").strip() or "ffmpeg"
        transport = str(section.get("rtsp_transport") or os.getenv("RADIO_RTSP_ADAPTER_TRANSPORT") or "tcp").strip().lower() or "tcp"
        host = str(section.get("output_host") or os.getenv("RADIO_RTSP_ADAPTER_OUTPUT_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        format_name = str(section.get("output_format") or os.getenv("RADIO_RTSP_ADAPTER_OUTPUT_FORMAT") or "mpegts").strip().lower() or "mpegts"
        loglevel = str(section.get("loglevel") or os.getenv("RADIO_RTSP_ADAPTER_LOGLEVEL") or "warning").strip().lower() or "warning"
        enabled = bool(section.get("enabled", True))
        try:
            port = int(section.get("output_port") or os.getenv("RADIO_RTSP_ADAPTER_OUTPUT_PORT") or 12340)
        except Exception:
            port = 12340
        if port < 1024 or port > 65535:
            port = 12340
        ffmpeg_path = self._ffmpeg_path(ffmpeg_bin)
        return {
            "enabled": enabled,
            "ffmpeg_bin": ffmpeg_bin,
            "ffmpeg_path": ffmpeg_path,
            "transport": transport if transport in {"tcp", "udp"} else "tcp",
            "output_host": host,
            "output_port": port,
            "output_format": format_name,
            "loglevel": loglevel,
        }

    def _pulse_env(self) -> dict[str, str]:
        env = dict(os.environ)
        uid = os.getuid()
        runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        env["XDG_RUNTIME_DIR"] = runtime_dir
        pulse_socket = f"{runtime_dir}/pulse/native"
        if os.path.exists(pulse_socket):
            env["PULSE_SERVER"] = f"unix:{pulse_socket}"
        return env

    def _spawn_stderr_reader(self, proc: subprocess.Popen[str], target: str) -> None:
        def _reader() -> None:
            if proc.stderr is None:
                return
            lines: list[str] = []
            try:
                for raw in proc.stderr:
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    lines.append(line)
                    if len(lines) > 40:
                        lines.pop(0)
            except Exception:
                pass
            merged = "\n".join(lines).strip() if lines else None
            with self._lock:
                if target == "adapter":
                    self._rtsp_adapter_last_stderr = merged
                else:
                    self._mpv_last_stderr = merged

        threading.Thread(target=_reader, daemon=True).start()

    def _spawn_adapter_watcher(self, proc: subprocess.Popen[str]) -> None:
        def _watch() -> None:
            try:
                code = proc.wait()
            except Exception:
                return
            with self._lock:
                if self._rtsp_adapter_process is proc:
                    self._rtsp_adapter_last_exit_code = int(code)
                    if code != 0 and not self._rtsp_adapter_last_error:
                        self._rtsp_adapter_last_error = self._rtsp_adapter_last_stderr or f"ffmpeg exit {code}"
                    self._rtsp_adapter_process = None
                    logger.warning("RTSP adapter exited: code=%s source=%s", code, self._rtsp_adapter_source_url)

        threading.Thread(target=_watch, daemon=True).start()

    def _stop_rtsp_adapter_locked(self) -> None:
        proc = self._rtsp_adapter_process
        if proc is None:
            self._rtsp_adapter_source_url = None
            self._rtsp_adapter_target_url = None
            self._rtsp_adapter_started_at = None
            return

        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        self._rtsp_adapter_last_exit_code = proc.poll()
        self._rtsp_adapter_process = None
        self._rtsp_adapter_source_url = None
        self._rtsp_adapter_target_url = None
        self._rtsp_adapter_started_at = None

    def _build_rtsp_adapter_target(self, cfg: dict[str, Any]) -> str:
        host = str(cfg.get("output_host") or "127.0.0.1")
        port = int(cfg.get("output_port") or 12340)
        format_name = str(cfg.get("output_format") or "mpegts")
        if format_name == "mpegts":
            return f"udp://{host}:{port}?pkt_size=1316"
        return f"udp://{host}:{port}"

    def _start_rtsp_adapter_locked(self, url: str) -> tuple[bool, str, str]:
        cfg = self._adapter_config()
        if not bool(cfg.get("enabled", True)):
            return False, "", "RTSP-Adapter ist in der Konfiguration deaktiviert."
        ffmpeg = str(cfg.get("ffmpeg_path") or "").strip()
        if not ffmpeg:
            return False, "", "ffmpeg ist nicht installiert."

        self._stop_rtsp_adapter_locked()
        target_url = self._build_rtsp_adapter_target(cfg)
        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            str(cfg.get("loglevel") or "warning"),
            "-rtsp_transport",
            str(cfg.get("transport") or "tcp"),
            "-i",
            url,
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            str(cfg.get("output_format") or "mpegts"),
            target_url,
        ]

        logger.info("Starting RTSP adapter: %s", " ".join(shlex.quote(x) for x in cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                env=self._pulse_env(),
            )
        except Exception as exc:
            self._rtsp_adapter_last_error = str(exc)
            logger.error("RTSP adapter start failed: %s", exc)
            return False, "", f"RTSP-Adapter Start fehlgeschlagen: {exc}"

        self._rtsp_adapter_process = proc
        self._rtsp_adapter_source_url = url
        self._rtsp_adapter_target_url = target_url
        self._rtsp_adapter_started_at = utc_now()
        self._rtsp_adapter_last_error = None
        self._rtsp_adapter_last_stderr = None
        self._rtsp_adapter_last_exit_code = None
        self._spawn_stderr_reader(proc, "adapter")
        self._spawn_adapter_watcher(proc)

        time.sleep(0.45)
        if proc.poll() is not None:
            self._rtsp_adapter_last_exit_code = proc.returncode
            self._rtsp_adapter_process = None
            err = self._rtsp_adapter_last_stderr or f"ffmpeg exit {proc.returncode}"
            self._rtsp_adapter_last_error = err
            logger.error("RTSP adapter exited early: %s", err)
            return False, "", f"RTSP-Adapter hat sofort beendet: {err}"

        logger.info("RTSP adapter ready: source=%s target=%s", url, target_url)
        return True, target_url, ""

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
            playback_url = url
            adapter_active = False
            if is_rtsp:
                ok_adapter, adapted_url, adapter_err = self._start_rtsp_adapter_locked(url)
                if not ok_adapter:
                    self._last_error = adapter_err
                    self._stream_url = url
                    self._playback_url = None
                    return {"ok": False, "message": f"Radio-Start fehlgeschlagen: {adapter_err}", "stream_url": url}
                playback_url = adapted_url
                adapter_active = True

            attempts: list[list[str]] = [[
                mpv,
                "--no-video",
                "--no-terminal",
                "--no-ytdl",
                "--really-quiet",
                "--idle=no",
                "--ao=pulse",
                playback_url,
            ]]

            last_error = "mpv hat den Stream nicht akzeptiert."
            for cmd in attempts:
                try:
                    logger.info("Starting radio playback: source=%s playback=%s", url, playback_url)
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=self._pulse_env(),
                    )
                    self._spawn_stderr_reader(self._process, "mpv")
                    self._stream_url = url
                    self._playback_url = playback_url
                except Exception as exc:
                    self._process = None
                    self._stream_url = None
                    self._playback_url = None
                    last_error = str(exc)
                    continue

                time.sleep(0.35)
                if self._process.poll() is None:
                    self._last_error = None
                    return {
                        "ok": True,
                        "message": "Radio gestartet",
                        "stream_url": url,
                        "playback_url": playback_url,
                        "rtsp_adapter_active": adapter_active,
                    }

                stderr = ""
                try:
                    stderr = (self._process.stderr.read() if self._process.stderr else "") or ""
                except Exception:
                    stderr = ""

                last_error = stderr.strip() or last_error
                self._process = None
                self._stream_url = None
                self._playback_url = None

            if adapter_active:
                self._stop_rtsp_adapter_locked()
            self._last_error = last_error
            return {"ok": False, "message": f"Radio-Start fehlgeschlagen: {last_error}", "stream_url": url}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._process is None and self._rtsp_adapter_process is None:
                return {"ok": True, "message": "Radio bereits gestoppt"}

            try:
                if self._process is not None:
                    self._process.terminate()
                    self._process.wait(timeout=3)
            except Exception:
                try:
                    if self._process is not None:
                        self._process.kill()
                except Exception:
                    pass

            self._process = None
            self._stream_url = None
            self._playback_url = None
            self._stop_rtsp_adapter_locked()
            logger.info("Radio playback stopped")
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
        adapter_running = self._rtsp_adapter_process is not None and self._rtsp_adapter_process.poll() is None
        return {
            "running": running,
            "stream_url": self._stream_url,
            "playback_url": self._playback_url,
            "pid": self._process.pid if running and self._process is not None else None,
            "last_error": self._last_error,
            "rtsp_adapter": {
                "active": adapter_running,
                "pid": self._rtsp_adapter_process.pid if adapter_running and self._rtsp_adapter_process is not None else None,
                "source_url": self._rtsp_adapter_source_url,
                "target_url": self._rtsp_adapter_target_url,
                "started_at": self._rtsp_adapter_started_at,
                "last_error": self._rtsp_adapter_last_error,
                "last_stderr": self._rtsp_adapter_last_stderr,
                "last_exit_code": self._rtsp_adapter_last_exit_code,
            },
        }


radio_service = RadioService()
