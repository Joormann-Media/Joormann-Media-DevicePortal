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
from app.core.jsonio import read_json, write_json
from app.core.paths import DATA_DIR
from app.core.timeutil import utc_now


logger = logging.getLogger(__name__)


class RadioService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._stream_url: str | None = None
        self._playback_url: str | None = None
        self._started_at: str | None = None
        self._last_error: str | None = None
        self._rtsp_adapter_process: subprocess.Popen[str] | None = None
        self._rtsp_adapter_source_url: str | None = None
        self._rtsp_adapter_target_url: str | None = None
        self._rtsp_adapter_started_at: str | None = None
        self._rtsp_adapter_last_error: str | None = None
        self._rtsp_adapter_last_stderr: str | None = None
        self._rtsp_adapter_last_exit_code: int | None = None
        self._mpv_last_stderr: str | None = None
        self._state_path = os.path.join(DATA_DIR, "radio-state.json")

    def _is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _is_mpv_process(self, pid: int) -> bool:
        if pid <= 0:
            return False
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            raw = open(cmdline_path, "rb").read()
            text = raw.decode("utf-8", errors="ignore").replace("\x00", " ").lower()
        except Exception:
            return True
        return "mpv" in text

    def _read_state_locked(self) -> dict[str, Any]:
        state = read_json(self._state_path, {})
        if not isinstance(state, dict):
            return {}
        return state

    def _write_state_locked(self, running: bool) -> None:
        pid = self._process.pid if self._process is not None and self._process.poll() is None else None
        payload = {
            "running": bool(running),
            "pid": int(pid) if isinstance(pid, int) else None,
            "stream_url": self._stream_url,
            "playback_url": self._playback_url,
            "started_at": self._started_at,
            "updated_at": utc_now(),
            "last_error": self._last_error,
        }
        write_json(self._state_path, payload, mode=0o600)

    def _restore_running_from_state_locked(self) -> tuple[bool, int | None]:
        state = self._read_state_locked()
        pid_raw = state.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, (int, float, str)) and str(pid_raw).strip().isdigit() else None
        running = bool(state.get("running")) and isinstance(pid, int) and self._is_pid_alive(pid) and self._is_mpv_process(pid)
        if running:
            if not self._stream_url:
                self._stream_url = str(state.get("stream_url") or "").strip() or None
            if not self._playback_url:
                self._playback_url = str(state.get("playback_url") or "").strip() or None
            if not self._started_at:
                self._started_at = str(state.get("started_at") or "").strip() or None
            return True, pid

        if state:
            state["running"] = False
            state["pid"] = None
            state["updated_at"] = utc_now()
            write_json(self._state_path, state, mode=0o600)
        return False, None

    def _detect_stream_kind(self, url: str) -> str:
        value = str(url or "").strip().lower()
        if value.startswith("rtsp://"):
            return "rtsp"
        if value.startswith("http://") or value.startswith("https://"):
            if ".m3u8" in value or ".m3u" in value:
                return "m3u"
            if ".pls" in value:
                return "pls"
            return "http"
        if value.startswith("file://"):
            return "file"
        return "unknown"

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
        # Profile 1: copy container streams with low-latency flags.
        # Profile 2: audio transcode fallback when source codecs/container are not accepted by the local player path.
        cmd_profiles = [
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                str(cfg.get("loglevel") or "warning"),
                "-rtsp_transport",
                str(cfg.get("transport") or "tcp"),
                "-fflags",
                "+genpts+nobuffer",
                "-flags",
                "low_delay",
                "-i",
                url,
                "-map",
                "0",
                "-c",
                "copy",
                "-f",
                str(cfg.get("output_format") or "mpegts"),
                target_url,
            ],
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                str(cfg.get("loglevel") or "warning"),
                "-rtsp_transport",
                str(cfg.get("transport") or "tcp"),
                "-i",
                url,
                "-vn",
                "-map",
                "a:0?",
                "-c:a",
                "mp2",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-f",
                str(cfg.get("output_format") or "mpegts"),
                target_url,
            ],
        ]

        proc: subprocess.Popen[str] | None = None
        last_boot_error = ""
        has_retried_without_rw_timeout = False
        for cmd in cmd_profiles:
            current_cmd = list(cmd)
            logger.info("Starting RTSP adapter: %s", " ".join(shlex.quote(x) for x in cmd))
            try:
                proc = subprocess.Popen(
                    current_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=self._pulse_env(),
                )
            except Exception as exc:
                last_boot_error = str(exc)
                logger.error("RTSP adapter start failed: %s", exc)
                continue

            self._rtsp_adapter_process = proc
            self._rtsp_adapter_source_url = url
            self._rtsp_adapter_target_url = target_url
            self._rtsp_adapter_started_at = utc_now()
            self._rtsp_adapter_last_error = None
            self._rtsp_adapter_last_stderr = None
            self._rtsp_adapter_last_exit_code = None
            self._spawn_stderr_reader(proc, "adapter")
            self._spawn_adapter_watcher(proc)

            time.sleep(0.65)
            if proc.poll() is None:
                logger.info("RTSP adapter ready: source=%s target=%s", url, target_url)
                return True, target_url, ""

            self._rtsp_adapter_last_exit_code = proc.returncode
            self._rtsp_adapter_process = None
            err = self._rtsp_adapter_last_stderr or f"ffmpeg exit {proc.returncode}"
            self._rtsp_adapter_last_error = err
            last_boot_error = err

            # Older ffmpeg builds do not support -rw_timeout. Retry once without it.
            if (not has_retried_without_rw_timeout) and "rw_timeout" in err.lower() and "option" in err.lower():
                retry_cmd = self._strip_ffmpeg_option_with_value(current_cmd, "-rw_timeout")
                if retry_cmd != current_cmd:
                    has_retried_without_rw_timeout = True
                    logger.warning("RTSP adapter retrying without -rw_timeout after ffmpeg option error.")
                    try:
                        proc = subprocess.Popen(
                            retry_cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                            text=True,
                            env=self._pulse_env(),
                        )
                    except Exception as exc:
                        last_boot_error = str(exc)
                        logger.error("RTSP adapter retry without -rw_timeout failed: %s", exc)
                        continue

                    self._rtsp_adapter_process = proc
                    self._rtsp_adapter_source_url = url
                    self._rtsp_adapter_target_url = target_url
                    self._rtsp_adapter_started_at = utc_now()
                    self._rtsp_adapter_last_error = None
                    self._rtsp_adapter_last_stderr = None
                    self._rtsp_adapter_last_exit_code = None
                    self._spawn_stderr_reader(proc, "adapter")
                    self._spawn_adapter_watcher(proc)

                    time.sleep(0.65)
                    if proc.poll() is None:
                        logger.info("RTSP adapter ready after retry without -rw_timeout: source=%s target=%s", url, target_url)
                        return True, target_url, ""

                    self._rtsp_adapter_last_exit_code = proc.returncode
                    self._rtsp_adapter_process = None
                    err = self._rtsp_adapter_last_stderr or f"ffmpeg exit {proc.returncode}"
                    self._rtsp_adapter_last_error = err
                    last_boot_error = err

            logger.warning("RTSP adapter profile failed, trying fallback profile: %s", err)

        final_error = last_boot_error or "unbekannter ffmpeg Fehler"
        return False, "", f"RTSP-Adapter hat sofort beendet: {final_error}"

    def _strip_ffmpeg_option_with_value(self, cmd: list[str], option: str) -> list[str]:
        cleaned: list[str] = []
        skip_next = False
        for idx, part in enumerate(cmd):
            if skip_next:
                skip_next = False
                continue
            if part == option:
                if idx + 1 < len(cmd):
                    skip_next = True
                continue
            cleaned.append(part)
        return cleaned

    def play(self, stream_url: str) -> dict[str, Any]:
        url = (stream_url or "").strip().replace("&amp;", "&")
        if not url:
            return {"ok": False, "message": "stream_url fehlt"}
        mpv = self._mpv_path()
        if not mpv:
            return {"ok": False, "message": "mpv ist nicht installiert"}

        with self._lock:
            self.stop()
            stream_kind = self._detect_stream_kind(url)
            is_rtsp = stream_kind == "rtsp"
            playback_url = url
            adapter_active = False
            adapter_error = ""
            if is_rtsp:
                ok_adapter, adapted_url, adapter_err = self._start_rtsp_adapter_locked(url)
                if not ok_adapter:
                    # Keep RTSP playback possible even when adapter startup fails:
                    # fallback to direct mpv input instead of hard failing.
                    adapter_error = adapter_err
                    logger.warning("RTSP adapter unavailable, fallback to direct RTSP playback: %s", adapter_err)
                else:
                    playback_url = adapted_url
                    adapter_active = True

            attempt_urls: list[str] = [playback_url]
            if is_rtsp and adapter_active and playback_url != url:
                # If adapted stream cannot be consumed by mpv, try direct RTSP as safety net.
                attempt_urls.append(url)

            attempts: list[list[str]] = []
            for attempt_url in attempt_urls:
                attempts.append([
                    mpv,
                    "--no-video",
                    "--no-terminal",
                    "--no-ytdl",
                    "--really-quiet",
                    "--idle=no",
                    "--ao=pulse",
                    attempt_url,
                ])

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
                    self._started_at = utc_now()
                    self._write_state_locked(True)
                    return {
                        "ok": True,
                        "message": "Radio gestartet",
                        "stream_url": url,
                        "playback_url": playback_url,
                        "stream_kind": stream_kind,
                        "rtsp_adapter_active": adapter_active,
                        "rtsp_adapter_error": adapter_error or None,
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
            self._started_at = None
            self._last_error = last_error
            self._write_state_locked(False)
            return {"ok": False, "message": f"Radio-Start fehlgeschlagen: {last_error}", "stream_url": url}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            state_running, state_pid = self._restore_running_from_state_locked()
            if self._process is None and self._rtsp_adapter_process is None and not state_running:
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
            if self._process is None and state_running and isinstance(state_pid, int):
                try:
                    os.kill(state_pid, signal.SIGTERM)
                    for _ in range(12):
                        if not self._is_pid_alive(state_pid):
                            break
                        time.sleep(0.2)
                    if self._is_pid_alive(state_pid):
                        os.kill(state_pid, signal.SIGKILL)
                except Exception:
                    pass

            self._process = None
            self._stream_url = None
            self._playback_url = None
            self._started_at = None
            self._stop_rtsp_adapter_locked()
            self._write_state_locked(False)
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
        state_pid: int | None = None
        if not running:
            running, state_pid = self._restore_running_from_state_locked()
        if running and self._process is not None and self._process.poll() is None:
            self._write_state_locked(True)

        adapter_running = self._rtsp_adapter_process is not None and self._rtsp_adapter_process.poll() is None
        stream_kind = self._detect_stream_kind(self._stream_url or "")
        return {
            "running": running,
            "stream_url": self._stream_url,
            "playback_url": self._playback_url,
            "stream_kind": stream_kind,
            "pid": self._process.pid if running and self._process is not None else state_pid,
            "started_at": self._started_at,
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
