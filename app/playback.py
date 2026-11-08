"""Experimental token-isolated Plex HLS player for trusted LAN use only.

The public browser receives opaque session/resource IDs, never Plex's token.
It deliberately proxies only /video/:/transcode/universal/* on the fixed
configured Plex server, and disallows redirects and arbitrary URL fetching.
"""
import re
import secrets
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from typing import Literal

router = APIRouter(prefix="/api/player")
_sessions = {}
_guard = threading.RLock()
SESSION_TTL = 3 * 60 * 60
SESSION_LIMIT = 12
PLAYLIST_LIMIT = 2 * 1024 * 1024


class PlaybackRequest(BaseModel):
    rating_key: int
    mode: Literal["beginning", "join"] = "beginning"
    airing_start: str | None = None
    airing_end: str | None = None


def _config():
    from app.main import DB, PLEX_URL, PLEX_TOKEN
    if not PLEX_URL or not PLEX_TOKEN:
        raise HTTPException(503, "Plex server and token must be configured")
    return DB, PLEX_URL, PLEX_TOKEN


def _clean_sessions():
    now = time.monotonic()
    expired = []
    with _guard:
        for key in list(_sessions):
            if now - _sessions[key]["last"] > SESSION_TTL:
                expired.append(key)
                del _sessions[key]
        if len(_sessions) > SESSION_LIMIT:
            for key, _ in sorted(_sessions.items(), key=lambda x: x[1]["last"])[:len(_sessions) - SESSION_LIMIT]:
                expired.append(key)
                del _sessions[key]
    for sid in expired:
        _stop_plex_transcode(sid)


def _stop_plex_transcode(sid):
    """Terminate only the Plex transcode belonging to our opaque session."""
    _, base, token = _config()
    try:
        # Plex uses the session identifier supplied to the HLS transcode.
        requests.get(
            base + "/video/:/transcode/universal/stop",
            params={"session": sid, "X-Plex-Client-Identifier": "universal-guide-" + sid},
            headers={"X-Plex-Token": token, "X-Plex-Product": "Universal TV Guide",
                     "X-Plex-Client-Identifier": "universal-guide-" + sid},
            timeout=(3, 8), allow_redirects=False,
        )
    except requests.RequestException:
        # Closing our browser session remains possible if Plex is unreachable.
        pass


def _session(sid):
    _clean_sessions()
    with _guard:
        session = _sessions.get(sid)
        if session:
            session["last"] = time.monotonic()
            return session
    raise HTTPException(404, "Playback session expired. Select Play again.")


def _safe_resource(base, location):
    """Resolve playlist references only against the configured Plex URL."""
    candidate = urljoin(base, location.strip())
    expected = urlparse(base)
    parsed = urlparse(candidate)
    if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
        raise HTTPException(502, "Plex playlist references an external host")
    if not parsed.path.startswith("/video/:/transcode/universal/"):
        raise HTTPException(502, "Plex playlist referenced an unexpected resource")
    # Never carry authentication tokens in browser-visible URLs.
    filtered = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                if k.casefold() != "x-plex-token"]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(filtered), ""))


def _map_reference(session, parent, reference):
    real = _safe_resource(parent, reference)
    with _guard:
        for key, value in session["resources"].items():
            if value == real:
                return "/api/player/" + session["id"] + "/" + key
        if len(session["resources"]) >= 12000:
            raise HTTPException(502, "Plex playlist has too many resources")
        key = secrets.token_urlsafe(12)
        session["resources"][key] = real
    return "/api/player/" + session["id"] + "/" + key


def _rewrite_playlist(session, parent, raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(502, "Plex returned an unreadable playlist")
    if not text.lstrip().startswith("#EXTM3U"):
        raise HTTPException(502, "Plex did not return an HLS playlist")
    lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            # HLS master tags, subtitles and encryption keys can contain URI=.
            line = re.sub(r'URI="([^"]+)"',
                          lambda m: 'URI="' + _map_reference(session, parent, m.group(1)) + '"', line)
            lines.append(line)
        elif line.strip():
            lines.append(_map_reference(session, parent, line))
        else:
            lines.append(line)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _upstream(session, url, *, stream=False):
    _, base, token = _config()
    # Callers can only use registered resources previously validated by _safe_resource.
    expected = urlparse(base)
    target = urlparse(url)
    if target.scheme != expected.scheme or target.netloc != expected.netloc:
        raise HTTPException(502, "Invalid Plex target")
    if not target.path.startswith("/video/:/transcode/universal/"):
        raise HTTPException(502, "Invalid Plex resource")
    headers = {
        "X-Plex-Token": token,
        "X-Plex-Client-Identifier": "universal-guide-" + session["id"],
        "X-Plex-Product": "Universal TV Guide",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Profile-Name": "Chrome",
    }
    try:
        response = requests.get(url, headers=headers, timeout=(5, 90),
                                stream=stream, allow_redirects=False)
        if response.status_code != 200:
            response.close()
            raise HTTPException(502, "Plex stream returned HTTP " + str(response.status_code))
        return response
    except requests.RequestException:
        raise HTTPException(502, "Plex stream is unavailable or timed out")


def _plex_duration(key):
    _, base, token = _config()
    try:
        r = requests.get(base + "/library/metadata/" + str(key),
                         headers={"X-Plex-Token": token, "Accept": "application/xml"},
                         timeout=(5, 20), allow_redirects=False)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        video = root.find("Video")
        if video is None:
            raise ValueError("No playable media metadata")
        duration = int(video.get("duration", "0")) // 1000
        return duration if duration > 0 else 0
    except (requests.RequestException, ValueError, ET.ParseError):
        raise HTTPException(502, "Unable to read this item's runtime from Plex")


@router.post("/start")
def start_playback(body: PlaybackRequest):
    db_path, base, token = _config()
    if body.rating_key <= 0:
        raise HTTPException(400, "Invalid rating key")
    with sqlite3.connect(db_path, timeout=15) as c:
        item = c.execute("SELECT id,title,kind FROM media WHERE id IN (?,?)",
                         ("movie:" + str(body.rating_key),
                          "episode:" + str(body.rating_key))).fetchone()
    if not item:
        raise HTTPException(404, "Movie or episode not found in indexed Plex library")
    offset = 0
    duration = _plex_duration(body.rating_key)
    if body.mode == "join":
        if not body.airing_start or not body.airing_end:
            raise HTTPException(400, "Join in progress requires airing start and end times")
        try:
            start = datetime.fromisoformat(body.airing_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(body.airing_end.replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError("Timezone needed")
            span = (end - start).total_seconds()
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        except ValueError:
            raise HTTPException(400, "Invalid broadcast time")
        if span <= 0 or span > 24 * 3600 or elapsed < 0:
            raise HTTPException(400, "The program is not currently airing")
        if duration <= 0:
            raise HTTPException(400, "Plex did not provide a media runtime")
        # Scale out commercial breaks when broadcast runtime exceeds media runtime.
        offset = min(max(0, elapsed / span * duration), max(0, duration - 10))
    _clean_sessions()
    sid = secrets.token_urlsafe(20)
    endpoint = base + "/video/:/transcode/universal/start.m3u8"
    params = {
        "path": "/library/metadata/" + str(body.rating_key),
        "protocol": "hls",
        "directPlay": "0",
        "directStream": "0",
        "session": sid,
        "mediaIndex": "0",
        "partIndex": "0",
        "maxVideoBitrate": "12000",
        "videoResolution": "1920x1080",
        "videoQuality": "100",
        "fastSeek": "1",
        "offset": str(round(offset, 3)),
        "X-Plex-Product": "Universal TV Guide",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Identifier": "universal-guide-" + sid,
        "X-Plex-Client-Profile-Name": "Chrome",
    }
    master = endpoint + "?" + urlencode(params)
    session = {"id": sid, "resources": {"master.m3u8": master},
               "last": time.monotonic()}
    with _guard:
        _sessions[sid] = session
    return {"title": item[1], "kind": item[2],
            "playlist_url": "/api/player/" + sid + "/master.m3u8",
            "offset_seconds": round(offset, 1), "duration_seconds": duration,
            "mode": body.mode}


@router.get("/{session_id}/{resource_id}")
def serve_resource(session_id: str, resource_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,80}", session_id):
        raise HTTPException(404, "Invalid session")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{5,80}", resource_id):
        raise HTTPException(404, "Invalid resource")
    session = _session(session_id)
    with _guard:
        url = session["resources"].get(resource_id)
    if not url:
        raise HTTPException(404, "Resource not in this playback session")
    is_playlist = urlparse(url).path.endswith(".m3u8")
    response = _upstream(session, url, stream=not is_playlist)
    if is_playlist:
        try:
            body = response.content
            if len(body) > PLAYLIST_LIMIT:
                raise HTTPException(502, "Plex HLS playlist exceeded maximum size")
            rewritten = _rewrite_playlist(session, url, body)
            return Response(content=rewritten, media_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-store"})
        finally:
            response.close()
    content_type = response.headers.get("Content-Type", "application/octet-stream")
    if not (content_type.startswith(("video/", "audio/", "application/octet-stream",
                                    "application/mp2t", "application/vnd.apple.mpegurl"))):
        content_type = "application/octet-stream"

    def data():
        try:
            yield from response.iter_content(chunk_size=128 * 1024)
        finally:
            response.close()
    return StreamingResponse(data(), media_type=content_type,
                             headers={"Cache-Control": "no-store"})


@router.delete("/{session_id}")
def stop_playback(session_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,80}", session_id):
        raise HTTPException(404, "Invalid session")
    with _guard:
        existed = _sessions.pop(session_id, None)
    # Don't allow arbitrary requests to terminate other clients' sessions:
    # only session IDs minted and still held by this application can be stopped.
    if existed is not None:
        _stop_plex_transcode(session_id)
    return {"stopped": existed is not None}
