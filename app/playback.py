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
    mode: Literal["beginning", "join", "resume"] = "beginning"
    offset_seconds: float | None = None
    audio_stream_id: int | None = None
    subtitle_stream_id: int | None = None
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


# Keep the extension on HLS child resources. Safari's web playback can
# infer content types from HTTP headers; native AVPlayer often relies on
# playlist/segment URI suffixes when following an HLS master.
_HLS_EXTENSIONS = frozenset((".m3u8", ".ts", ".m4s", ".mp4", ".aac",
                             ".mp3", ".vtt", ".webvtt", ".key", ".m4a"))

def _map_reference(session, parent, reference):
    real = _safe_resource(parent, reference)
    with _guard:
        for key, value in session["resources"].items():
            if value == real:
                return "/api/player/" + session["id"] + "/" + key
        if len(session["resources"]) >= 12000:
            raise HTTPException(502, "Plex playlist has too many resources")
        path = urlparse(real).path
        extension = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if extension not in _HLS_EXTENSIONS:
            # MPEG-TS media segments are the only extension-less references
            # in the Plex HLS media playlists observed so far.
            extension = ".ts"
        key = secrets.token_urlsafe(12) + extension
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


def _trim_join_playlist(session, payload):
    """Drop Plex's empty pre-seek HLS slots for join-in-progress sessions.

    Plex may expose one segment slot for every second of the entire media,
    even though a nonzero offset starts the transcoder much later. Requests
    for pre-offset slots return 188-byte empty TS packets, which Safari
    traverses without ever attaining HAVE_METADATA. Only process simple,
    finite MPEG-TS media playlists, never master or encrypted playlists.
    """
    offset=session.get("requested_offset_seconds",0)
    if session.get("mode") not in ("join","resume") or not offset:
        return payload,None
    text=payload.decode("utf-8")
    lines=text.splitlines()
    if (not lines or lines[0].strip()!="#EXTM3U"
        or not any(line.startswith("#EXTINF:") for line in lines)
        or not any(line.strip()=="#EXT-X-ENDLIST" for line in lines)
        or any(line.startswith(("#EXT-X-KEY:","#EXT-X-MAP:",
                                 "#EXT-X-BYTERANGE:","#EXT-X-DISCONTINUITY"))
               for line in lines)):
        return payload,None
    duration=session.get("item_duration_seconds") or 0
    if not duration:
        return payload,None
    # Keep the first segment at/just after the requested media position.
    # Do not modify short/range-limited Plex manifests that already start
    # near the seek point. Count only complete EXTINF+URI pairs.
    segments=[]
    current=None
    for line in lines:
        if line.startswith("#EXTINF:"):
            try:current=float(line[8:].split(",",1)[0])
            except ValueError:return payload,None
            if current<=0:return payload,None
        elif current is not None and line.strip() and not line.startswith("#"):
            segments.append((current,line))
            current=None
    if current is not None or len(segments)<2:
        return payload,None
    total=sum(d for d,_ in segments)
    if abs(total-duration)>max(30,duration*.03):
        # Full media timeline must match duration before assuming the
        # offset corresponds to the earlier segment indices.
        return payload,None
    index=0
    elapsed=0.0
    # Skip the empty slots, then retain a few seconds of seek tolerance.
    threshold=max(0.0,float(offset)-2.0)
    while index<len(segments)-1 and elapsed+segments[index][0]<=threshold:
        elapsed+=segments[index][0]
        index+=1
    if index<2 or len(segments)-index<2:
        return payload,None
    header=[]
    sequence=0
    for line in lines:
        if line.startswith("#EXTINF:"):
            break
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:sequence=int(line.split(":",1)[1])
            except ValueError:return payload,None
            continue
        if line=="#EXT-X-ENDLIST":continue
        header.append(line)
    # HLS media-sequence must reflect the first visible media segment.
    header.append("#EXT-X-MEDIA-SEQUENCE:"+str(sequence+index))
    out=header[:]
    for seg_duration,uri in segments[index:]:
        out.extend(("#EXTINF:"+format(seg_duration,".3f")+",",uri))
    out.append("#EXT-X-ENDLIST")
    return ("\n".join(out)+"\n").encode("utf-8"),{
        "skipped_segments":index,
        "start_at_seconds":round(elapsed,2),
        "remaining_segments":len(segments)-index
    }


def _record(session, **values):
    with _guard:
        for key,value in values.items():
            session[key] = value


def _decision(session, url):
    """Register a fresh Plex playback session once, before the HLS master."""
    if session["decision_attempted"]:
        return
    _record(session, decision_attempted=True)
    from urllib.parse import urlsplit, urlunsplit
    u=urlsplit(url)
    if not u.path.endswith("/start.m3u8"):
        return
    decision=urlunsplit((u.scheme,u.netloc,u.path.rsplit("/",1)[0]+"/decision",u.query,""))
    _,base,token=_config()
    headers={"X-Plex-Token":token,"X-Plex-Client-Identifier":"universal-guide-"+session["id"],
             "X-Plex-Session-Identifier":session["id"],"X-Plex-Product":"Universal TV Guide",
             "X-Plex-Platform":"Chrome","X-Plex-Client-Profile-Name":"Chrome"}
    try:
        response=requests.get(decision,headers=headers,timeout=(5,15),allow_redirects=False)
        _record(session, decision_http=response.status_code)
        response.close()
    except requests.RequestException as exc:
        _record(session, decision_http="network-error", last_error=type(exc).__name__)
    # An unsupported decision route should not break an otherwise working stream.


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
        "X-Plex-Session-Identifier": session["id"],
        "X-Plex-Product": "Universal TV Guide",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Profile-Name": "Chrome",
    }
    try:
        response = requests.get(url, headers=headers, timeout=(5, 90),
                                stream=stream, allow_redirects=False)
        _record(session, last_http=response.status_code,
                last_resource=("playlist" if urlparse(url).path.endswith(".m3u8") else "segment"),
                requests=session["requests"]+1)
        if response.status_code != 200:
            _record(session, last_error="Plex HTTP "+str(response.status_code))
            response.close()
            raise HTTPException(502, "Plex stream returned HTTP " + str(response.status_code))
        return response
    except requests.RequestException as exc:
        _record(session, last_error=type(exc).__name__)
        raise HTTPException(502, "Plex stream is unavailable or timed out")


def _plex_item_metadata(key):
    """Return only UI-safe metadata from the indexed Plex movie/episode."""
    _, base, token = _config()
    try:
        response=requests.get(
            base+"/library/metadata/"+str(key),
            headers={"X-Plex-Token":token,"Accept":"application/xml"},
            params={"includeChapters":1,"includeStreams":1},
            timeout=(5,20),allow_redirects=False)
        response.raise_for_status()
        root=ET.fromstring(response.content)
    except (requests.RequestException,ET.ParseError) as exc:
        raise HTTPException(502,"Plex metadata unavailable: "+type(exc).__name__)
    video=root.find("Video")
    if video is None:raise HTTPException(502,"Plex returned no video metadata")
    media=video.find("Media")
    part=media.find("Part") if media is not None else None
    def number(value,default=0):
        try:return int(value)
        except (ValueError,TypeError):return default
    chapters=[]
    for node in video.findall(".//Chapter"):
        starts=number(node.get("startTimeOffset") or node.get("startTime"),-1)
        if starts>=0:
            chapters.append({"title":node.get("tag") or node.get("title") or "Chapter "+str(len(chapters)+1),
                             "start_seconds":starts/1000})
    streams={"audio":[],"subtitle":[]}
    if part is not None:
        for node in part.findall("Stream"):
            kind={"2":"audio","3":"subtitle"}.get(node.get("streamType"))
            if kind is None:continue
            stream_id=number(node.get("id"))
            if stream_id<=0:continue
            entry={"id":stream_id,
                   "language":node.get("language") or node.get("languageCode") or "Unknown",
                   "codec":node.get("codec") or "",
                   "forced":node.get("forced")=="1",
                   "title":node.get("title") or node.get("displayTitle") or "",
                   "selected":node.get("selected")=="1"}
            streams[kind].append(entry)
    return {"part_id":number(part.get("id")) if part is not None else 0,
            "kind":video.get("type") or "",
            "title":video.get("title") or "",
            "show_title":video.get("grandparentTitle") or "",
            "year":number(video.get("year")),
            "release_date":video.get("originallyAvailableAt") or None,
            "season":number(video.get("parentIndex")),
            "episode":number(video.get("index")),
            "duration_seconds":number(video.get("duration"))/1000,
            "chapters":chapters,
            "audio":streams["audio"],"subtitles":streams["subtitle"],
            "preview_available":bool(part is not None and
                                     "sd" in (part.get("indexes") or "").split(","))
            }


@router.get("/{session_id}/metadata")
def playback_metadata(session_id:str):
    session=_session(session_id)
    return _plex_item_metadata(session["rating_key"])


class PlexTrackSelection(BaseModel):
    audio_stream_id: int | None = None
    subtitle_stream_id: int | None = None


@router.put("/{session_id}/tracks")
def select_playback_tracks(session_id:str,body:PlexTrackSelection):
    """Validate and select media-part streams via Plex's documented PUT API.

    Unlike a transcode URL parameter, this changes stream selection for the
    Plex account associated with the configured token, including other apps.
    A failed selection is reported before interrupting the current player.
    """
    session=_session(session_id)
    if body.audio_stream_id is None and body.subtitle_stream_id is None:
        raise HTTPException(400,"No stream selection supplied")
    metadata=_plex_item_metadata(session["rating_key"])
    part_id=metadata["part_id"]
    if part_id<=0:raise HTTPException(404,"Plex media part unavailable")
    audio=body.audio_stream_id
    subtitle=body.subtitle_stream_id
    if audio is not None and audio not in [x["id"] for x in metadata["audio"]]:
        raise HTTPException(400,"Audio track does not belong to this part")
    if subtitle is not None and subtitle!=0 and subtitle not in [x["id"] for x in metadata["subtitles"]]:
        raise HTTPException(400,"Subtitle track does not belong to this part")
    _,base,token=_config()
    params={"allParts":0}
    if audio is not None:params["audioStreamID"]=audio
    if subtitle is not None:params["subtitleStreamID"]=subtitle
    try:
        response=requests.put(base+"/library/parts/"+str(part_id),
            headers={"X-Plex-Token":token,"Accept":"application/xml"},
            params=params,timeout=(5,15),allow_redirects=False)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(502,"Plex could not select the requested track: "+type(exc).__name__)
    finally:
        if "response" in locals():response.close()
    return {"ok":True,"audio_stream_id":audio,"subtitle_stream_id":subtitle}


@router.get("/{session_id}/preview")
def playback_preview(session_id:str,at:float=0):
    """Proxy only Plex-generated BIF preview JPEGs for the active media item."""
    session=_session(session_id)
    details=_plex_item_metadata(session["rating_key"])
    if not details["preview_available"]:
        raise HTTPException(404,"No Plex preview thumbnails for this item")
    if not (0<=at<=max(0,details["duration_seconds"])):
        raise HTTPException(400,"Invalid preview time")
    _,base,token=_config()
    # Part IDs are determined independently by the server's indexed item.
    response=requests.get(base+"/library/metadata/"+str(session["rating_key"]),
        headers={"X-Plex-Token":token,"Accept":"application/xml"},
        timeout=(5,15),allow_redirects=False)
    try:
        response.raise_for_status()
        part=ET.fromstring(response.content).find(".//Media/Part")
        if part is None or not re.fullmatch(r"\d+",part.get("id") or ""):
            raise HTTPException(404,"Plex thumbnail index unavailable")
        part_id=part.get("id")
    except (requests.RequestException,ET.ParseError):
        raise HTTPException(502,"Plex thumbnail index unavailable")
    finally:
        response.close()
    try:
        frame=requests.get(base+"/library/parts/"+part_id+"/indexes/sd/"+
            str(int(at*1000)),headers={"X-Plex-Token":token,
            "Accept":"image/jpeg"},timeout=(4,12),allow_redirects=False)
        if frame.status_code==404:raise HTTPException(404,"Preview not indexed")
        frame.raise_for_status()
        if len(frame.content)>512*1024 or not frame.content.startswith(b"\xff\xd8"):
            raise HTTPException(502,"Invalid Plex thumbnail")
        return Response(content=frame.content,media_type="image/jpeg",
                        headers={"Cache-Control":"private, max-age=30"})
    except requests.RequestException:
        raise HTTPException(502,"Plex thumbnail retrieval failed")
    finally:
        if "frame" in locals():frame.close()


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


@router.get("/capabilities")
def playback_capabilities():
    # Native apps use this to detect stale Unraid Docker images rather than
    # trying to play old, extensionless HLS resources with AVPlayer.
    return {"native_hls_resources": 2}


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
    if body.mode == "resume":
        if body.offset_seconds is None or not (0<=body.offset_seconds<max(duration,1)):
            raise HTTPException(400,"Invalid resume time")
        offset=float(body.offset_seconds)
    if body.audio_stream_id is not None or body.subtitle_stream_id is not None:
        meta=_plex_item_metadata(body.rating_key)
        if (body.audio_stream_id is not None and
            body.audio_stream_id not in [entry["id"] for entry in meta["audio"]]):
            raise HTTPException(400,"Audio track is not available for this item")
        if (body.subtitle_stream_id is not None and body.subtitle_stream_id!=0 and
            body.subtitle_stream_id not in [entry["id"] for entry in meta["subtitles"]]):
            raise HTTPException(400,"Subtitle track is not available for this item")
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
        "videoCodec": "h264",
        "audioCodec": "aac",
        "maxVideoBitrate": "12000",
        "videoResolution": "1920x1080",
        "videoQuality": "100",
        "fastSeek": "1",
        "offset": str(round(offset, 3)),
        "X-Plex-Product": "Universal TV Guide",
        "X-Plex-Platform": "Chrome",
        "X-Plex-Client-Identifier": "universal-guide-" + sid,
        "X-Plex-Session-Identifier": sid,
        "X-Plex-Client-Profile-Name": "Chrome",
    }
    # The actual tracks are selected through PUT /library/parts/{id},
    # not by injecting stream IDs into the transcoder's URL.
    # Always burn the user's selected subtitles for browser compatibility.
    if body.subtitle_stream_id==0:
        params["subtitles"]="none"
    else:
        selected_subtitle=body.subtitle_stream_id
        if selected_subtitle is None:
            selected_meta=_plex_item_metadata(body.rating_key)
            selected_subtitle=next((x["id"] for x in selected_meta["subtitles"]
                                    if x["selected"]),None)
        params["subtitles"]="burn" if selected_subtitle else "none"
        if selected_subtitle:params["subtitleSize"]="100"
    master = endpoint + "?" + urlencode(params)
    session = {"id": sid, "resources": {"master.m3u8": master},
               "last": time.monotonic(), "rating_key": body.rating_key,
               "mode":body.mode,"requested_offset_seconds":round(offset,1),
               "item_duration_seconds":duration,
               "audio_stream_id":body.audio_stream_id,
               "subtitle_stream_id":body.subtitle_stream_id,
               "requests": 0, "playlists": 0, "segments": 0,
               "last_resource": None, "last_http": None, "last_error": None,
               "decision_http": None, "decision_attempted": False}
    with _guard:
        _sessions[sid] = session
    return {"title": item[1], "kind": item[2],
            "playlist_url": "/api/player/" + sid + "/master.m3u8",
            "offset_seconds": round(offset, 1), "duration_seconds": duration,
            "mode": body.mode}


@router.get("/{session_id}/diagnostics")
def stream_diagnostics(session_id: str):
    """Safe counters only: no Plex URLs, API tokens or media paths."""
    session=_session(session_id)
    with _guard:
        return {key:session.get(key) for key in
                ("requests","playlists","segments","last_resource","last_http",
                 "last_error","decision_http","decision_attempted",
                 "mode","requested_offset_seconds","item_duration_seconds",
                 "playlist_segment_references","playlist_endlist",
                 "first_segment_probe","last_segment_probe",
                 "completed_segments","segment_bytes_total",
                 "join_playlist_trim")}


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
    if resource_id=="master.m3u8":
        _decision(session,url)
    response = _upstream(session, url, stream=not is_playlist)
    with _guard:
        counter="playlists" if is_playlist else "segments"
        session[counter]+=1
    if is_playlist:
        try:
            body = response.content
            if len(body) > PLAYLIST_LIMIT:
                raise HTTPException(502, "Plex HLS playlist exceeded maximum size")
            rewritten = _rewrite_playlist(session, url, body)
            rewritten,trim_info=_trim_join_playlist(session,rewritten)
            if trim_info:
                _record(session,join_playlist_trim=trim_info)
            text=rewritten.decode("utf-8",errors="replace")
            _record(session,
                    playlist_segment_references=sum(1 for line in text.splitlines()
                      if line.strip() and not line.startswith("#")),
                    playlist_endlist="#EXT-X-ENDLIST" in text)
            return Response(content=rewritten, media_type="application/vnd.apple.mpegurl",
                            headers={"Cache-Control": "no-store"})
        finally:
            response.close()
    content_type = response.headers.get("Content-Type", "application/octet-stream")
    if not (content_type.startswith(("video/", "audio/", "application/octet-stream",
                                    "application/mp2t", "application/vnd.apple.mpegurl"))):
        content_type = "application/octet-stream"

    def data():
        total=0
        signature=None
        try:
            for chunk in response.iter_content(chunk_size=128 * 1024):
                if not chunk:continue
                total+=len(chunk)
                if signature is None:
                    if chunk[0]==0x47:
                        signature="mpeg_ts_sync"
                    elif len(chunk)>=8 and chunk[4:8] in (b"ftyp",b"moof",b"styp"):
                        signature="fragmented_mp4"
                    elif chunk.startswith(b"#EXTM3U"):
                        signature="unexpected_hls_playlist"
                    elif chunk.lstrip().startswith((b"<",b"{",b"[")):
                        signature="unexpected_text_or_error"
                    else:
                        signature="unknown_binary"
                yield chunk
        finally:
            with _guard:
                probe={"bytes":total,"type":signature or "empty",
                       "content_type":content_type.split(";",1)[0],
                       "content_length":response.headers.get("Content-Length")}
                if session.get("first_segment_probe") is None:
                    session["first_segment_probe"]=probe
                session["last_segment_probe"]=probe
                session["completed_segments"]=session.get("completed_segments",0)+1
                session["segment_bytes_total"]=session.get("segment_bytes_total",0)+total
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
