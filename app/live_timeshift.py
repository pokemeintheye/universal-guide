"""Bounded, disk-backed timeshift for the configured Channels DVR TVE streams.

The tuner input always comes from the allowlisted backend stream resolver.
Do not accept client-controlled URLs or paths. This is a live recording
buffer, NOT a permanent DVR recording or a full Channels recordings API.
"""
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.channels_dvr import live_stream
from app.tablo_live import _disk_dvr_playlist_window, _safe_ffmpeg_message

router = APIRouter(prefix="/api/live/timeshift", tags=["live-timeshift"])
STORE = Path(os.getenv("CHANNELS_DVR_CACHE_DIR", "/data/channels-live-cache"))
MINUTES = max(2, min(30, int(os.getenv("CHANNELS_DVR_BUFFER_MINUTES", "20"))))
SEGMENT_SECONDS = 4
SEGMENT_COUNT = (MINUTES * 60) // SEGMENT_SECONDS
MAX_SESSIONS = 2
IDLE_SECONDS = 1800
ACTIVE = {}
LOCK = threading.RLock()
PREPARED = False
SWEEPER_STARTED = False
SESSION_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,80}\Z")
SEGMENT_PATTERN = re.compile(r"seg_[0-9]{8,10}\.ts\Z")


def _prepare():
    global PREPARED, SWEEPER_STARTED
    with LOCK:
        if not PREPARED:
            STORE.mkdir(parents=True, mode=0o700, exist_ok=True)
            for p in STORE.iterdir():
                if p.is_dir() and not p.is_symlink() and SESSION_PATTERN.fullmatch(p.name):
                    shutil.rmtree(p, ignore_errors=True)
            PREPARED = True
        if not SWEEPER_STARTED:
            threading.Thread(target=_sweep, name="channels-timeshift-cleanup",
                             daemon=True).start()
            SWEEPER_STARTED = True


def _stop(sid):
    with LOCK:
        state = ACTIVE.pop(sid, None)
    if state is None:
        return False
    process = state["process"]
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    shutil.rmtree(state["directory"], ignore_errors=True)
    return True


def _cleanup():
    now = time.monotonic()
    with LOCK:
        expired = [sid for sid, state in ACTIVE.items()
                   if now - state["last"] > IDLE_SECONDS]
    for sid in expired:
        _stop(sid)


def _sweep():
    while True:
        time.sleep(60)
        _cleanup()


def _session(sid):
    if not SESSION_PATTERN.fullmatch(sid):
        raise HTTPException(404, "Invalid timeshift session")
    _cleanup()
    with LOCK:
        state = ACTIVE.get(sid)
        if state is None:
            raise HTTPException(404, "Timeshift session has ended")
        state["last"] = time.monotonic()
        return state


def _health(state):
    directory = state["directory"]
    playlist = directory / "index.m3u8"
    returncode = state["process"].poll()
    return {
        "transcoder_running": returncode is None,
        "ffmpeg_exit_code": returncode,
        "manifest_ready": playlist.is_file(),
        "disk_dvr_playlist": _disk_dvr_playlist_window(playlist),
        "dvr_window_target_seconds": MINUTES * 60,
        "mode": "rolling_dvr",
        "ffmpeg_error": _safe_ffmpeg_message(directory) if returncode is not None else "",
    }


@router.post("/start/{playback_id}")
def start(playback_id: str):
    """Start a new rolling buffer for a server-owned, allowlisted stream ID."""
    # This resolution rejects unknown IDs BEFORE starting ffmpeg or using disk.
    source = live_stream(playback_id)
    if shutil.which("ffmpeg") is None:
        raise HTTPException(503, "ffmpeg is not installed in Universal Guide")
    _prepare()
    _cleanup()
    with LOCK:
        if len(ACTIVE) >= MAX_SESSIONS:
            raise HTTPException(409, "Two Channels DVR timeshift sessions are already active")
        sid = secrets.token_urlsafe(24)
        directory = STORE / sid
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        # Copy the already-compatible TVE H264 video without expensive
        # re-encoding; encode audio to AAC for consistent tvOS HLS playback.
        args = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rw_timeout", "15000000", "-i", source["stream_url"],
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "128k",
            "-f", "hls", "-hls_time", str(SEGMENT_SECONDS),
            "-hls_list_size", str(SEGMENT_COUNT),
            "-hls_delete_threshold", "3",
            "-hls_flags", "delete_segments+omit_endlist+program_date_time",
            "-hls_segment_filename", str(directory / "seg_%08d.ts"),
            str(directory / "index.m3u8"),
        ]
        try:
            with (directory / "ffmpeg.log").open("wb") as log:
                process = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=log, close_fds=True)
        except OSError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(502, "Could not start Channels DVR buffer: " +
                                type(exc).__name__) from exc
        now = time.monotonic()
        ACTIVE[sid] = {"process": process, "directory": directory,
                       "created": now, "last": now}
    return {"session_id": sid,
            "playlist_url": "/api/live/timeshift/" + sid + "/index.m3u8",
            "dvr_window_target_seconds": MINUTES * 60}


@router.get("/{sid}/status")
def status(sid: str):
    return _health(_session(sid))


@router.get("/{sid}/{file_name}")
def media(sid: str, file_name: str):
    if file_name != "index.m3u8" and not SEGMENT_PATTERN.fullmatch(file_name):
        raise HTTPException(404, "Invalid timeshift media filename")
    state = _session(sid)
    path = state["directory"] / file_name
    if file_name == "index.m3u8" and not path.is_file():
        # The tvOS client polls status before opening the playlist; allow a
        # little extra time for the first segment if it races playback.
        for _ in range(16):
            if path.is_file() or state["process"].poll() is not None:
                break
            time.sleep(0.5)
    if not path.is_file():
        health = _health(state)
        if not health["transcoder_running"]:
            raise HTTPException(502, "Channels DVR buffer stopped: " +
                                (health["ffmpeg_error"] or "encoder exited"))
        raise HTTPException(503, "Channels DVR buffer is preparing")
    return FileResponse(
        path, media_type=("application/vnd.apple.mpegurl"
                          if file_name == "index.m3u8" else "video/mp2t"),
        headers={"Cache-Control": "no-store"})


@router.delete("/{sid}")
def close(sid: str):
    if not SESSION_PATTERN.fullmatch(sid):
        raise HTTPException(404, "Invalid timeshift session")
    return {"stopped": _stop(sid)}
