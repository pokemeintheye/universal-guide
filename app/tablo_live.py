"""Experimental local Tablo live tuner, HLS transcoding, and diagnostics.

Tune requests are limited to channel IDs already indexed from the Tablo API.
No arbitrary URLs, shell evaluation, or media directories are exposed.
Requires ffmpeg in the container and a working Tablo /watch endpoint.
"""
import os, re, shutil, secrets, subprocess, threading, time
from pathlib import Path
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone
import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router=APIRouter(prefix="/api/tablo/live")
USER_AGENT="Tablo-FAST/1.7.0 (Mobile; iPhone; iOS 18.4)"
STORE=Path("/tmp/universal-guide-live")
MAX_SESSIONS=2
IDLE_SECONDS=120
FAILED_SESSION_SECONDS=240
ACTIVE={}
LOCK=threading.RLock()

def _settings():
    from app.main import TABLO_URL, DB
    if not TABLO_URL:
        raise HTTPException(503,"TABLO_URL is not configured")
    p=urlparse(TABLO_URL)
    if p.scheme!="http" or not p.hostname or p.username or p.password:
        raise HTTPException(503,"Tablo live playback currently requires a local HTTP device URL")
    return TABLO_URL,DB

def _stop(sid):
    with LOCK:
        session=ACTIVE.pop(sid,None)
    if not session:return False
    process=session["process"]
    try:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass
    shutil.rmtree(session["directory"],ignore_errors=True)
    return True

def _cleanup():
    now=time.monotonic()
    with LOCK:
        expired=[sid for sid,state in ACTIVE.items()
                 if now-state["last"] > (IDLE_SECONDS if state["process"].poll() is None
                                        else FAILED_SESSION_SECONDS)]
    for sid in expired:_stop(sid)

def _allowed_url(origin,url):
    """Only stream from the same device host on a local HTTP port."""
    candidate=urljoin(origin+"/",url)
    base=urlparse(origin)
    target=urlparse(candidate)
    # Legacy Tablo uses :8885 for the JSON API but often returns its HLS
    # playlist on port 80 (e.g. /stream/pl.m3u8). Some firmware uses :8888.
    # Do not expand this into an unrestricted host/port proxy.
    allowed_ports={base.port or 80,80,8885,8888}
    stream_path=target.path.startswith("/stream/") and target.path.endswith(".m3u8")
    if (target.scheme!="http" or target.hostname!=base.hostname
            or target.username or target.password or target.fragment
            or target.port not in allowed_ports or not stream_path):
        # Only scheme/host/port/path (no credentials, query or stream tokens).
        raise HTTPException(502,"Tablo supplied an unexpected playlist destination: "+
            str(target.scheme)+"://"+str(target.hostname)+":"+
            str(target.port or 80)+str(target.path[:90]))
    return candidate

@router.post("/start")
def tune(channel_id:str):
    origin,db_path=_settings()
    if not re.fullmatch(r"/guide/channels/[A-Za-z0-9_-]{1,80}",channel_id):
        raise HTTPException(400,"Invalid Tablo channel identifier")
    import sqlite3
    with sqlite3.connect(db_path) as c:
        found=c.execute("SELECT 1 FROM tablo_channels WHERE channel_id=?",(channel_id,)).fetchone()
    if not found:raise HTTPException(404,"Channel isn't in the indexed Tablo lineup")
    _cleanup()
    with LOCK:
        if sum(1 for state in ACTIVE.values() if state["process"].poll() is None)>=MAX_SESSIONS:
            raise HTTPException(409,"Two Tablo streams are already in use in Universal Guide")
    try:
        r=requests.post(origin+channel_id+"/watch",headers={
            "User-Agent":USER_AGENT,"Accept":"application/json"
        },timeout=(4,25),allow_redirects=False)
    except requests.RequestException as exc:
        raise HTTPException(502,"Tablo tune request failed: "+type(exc).__name__)
    if r.status_code!=200:
        detail=""
        try:
            d=r.json()
            detail=str(d.get("code") or d.get("message") or d.get("details") or "")[:120]
        except (ValueError,AttributeError):
            pass
        if r.status_code in (401,403):
            raise HTTPException(502,"Tablo declined the tune (HTTP "+str(r.status_code)+
                "). This firmware may require signed app authorization. "+detail)
        raise HTTPException(502,"Tablo tune returned HTTP "+str(r.status_code)+
            (": "+detail if detail else ""))
    try:
        data=r.json()
    except ValueError:
        raise HTTPException(502,"Tablo did not return JSON from the watch endpoint")
    playlist=data.get("playlist_url") if isinstance(data,dict) else None
    if not isinstance(playlist,str) or not playlist:
        raise HTTPException(502,"Tablo watch response did not contain playlist_url")
    playlist=_allowed_url(origin,playlist)
    if shutil.which("ffmpeg") is None:
        raise HTTPException(503,"ffmpeg is not installed in the Universal Guide container")
    sid=secrets.token_urlsafe(20)
    directory=STORE/sid
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    args=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
          "-user_agent",USER_AGENT,"-i",playlist,
          "-map","0:v:0","-map","0:a:0?","-vf","yadif",
          "-c:v","libx264","-preset","veryfast","-tune","zerolatency",
          "-pix_fmt","yuv420p","-b:v","3000k","-maxrate","3500k","-bufsize","6000k",
          "-c:a","aac","-ac","2","-b:a","128k",
          "-f","hls","-hls_time","4","-hls_list_size","7",
          "-hls_flags","delete_segments+omit_endlist",
          "-hls_segment_filename",str(directory/"seg_%05d.ts"),
          str(directory/"index.m3u8")]
    log_path=directory/"ffmpeg.log"
    try:
        with log_path.open("wb") as log:
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,stderr=log,
                                     close_fds=True)
    except OSError as exc:
        shutil.rmtree(directory,ignore_errors=True)
        raise HTTPException(502,"Could not launch ffmpeg: "+type(exc).__name__)
    with LOCK:
        ACTIVE[sid]={"id":sid,"directory":directory,"process":process,
                     "last":time.monotonic(),"created":time.monotonic(),
                     "channel":channel_id,"tune_status":r.status_code}
    return {"session_id":sid,"playlist_url":"/api/tablo/live/"+sid+"/index.m3u8",
            "note":"Live tuner starting. HLS may need several seconds to buffer."}

def _safe_ffmpeg_message(directory):
    """Expose a short diagnostic without leaking upstream stream tokens."""
    try:
        raw=(directory/"ffmpeg.log").read_bytes()[-8000:]
        message=raw.decode("utf-8",errors="replace")
        message=re.sub(r"https?://[^\\s\\\]\[<>\"']+","[Tablo stream URL]",message)
        message=re.sub(r"(?i)(?:token|key|secret|session)[=:]\\s*[^\\s&]+",
                       "[redacted]",message)
        meaningful=[line.strip() for line in message.splitlines() if line.strip()]
        return " | ".join(meaningful[-3:])[-480:]
    except OSError:
        return ""


def _probe_segment(directory):
    """Lightweight ffprobe inspection; no media filenames or external URLs returned."""
    segments=sorted(directory.glob("seg_*.ts"))
    if not segments:return {"status":"no_segments"}
    segment=segments[-1]
    if not segment.is_file() or segment.stat().st_size<188:
        return {"status":"empty_segment"}
    try:
        import json
        cmd=["ffprobe","-v","error","-show_entries",
             "stream=codec_type,codec_name,width,height,pix_fmt",
             "-of","json",str(segment)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=5,check=False)
        if p.returncode:
            return {"status":"probe_failed","exit_code":p.returncode,
                    "message":p.stderr[-180:].strip()}
        streams=json.loads(p.stdout).get("streams",[])
        return {"status":"ok" if streams else "no_decodable_streams",
                "segment_bytes":segment.stat().st_size,
                "streams":[{k:v for k,v in stream.items()
                           if k in ("codec_type","codec_name","width","height","pix_fmt")}
                          for stream in streams[:3]]}
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        return {"status":"probe_error","error":type(exc).__name__}


def _health(item,probe=False):
    directory=item["directory"]
    playlist=directory/"index.m3u8"
    returncode=item["process"].poll()
    try:
        segments=len(list(directory.glob("seg_*.ts")))
    except OSError:
        segments=0
    result={"transcoder_running":returncode is None,
            "ffmpeg_exit_code":returncode,
            "manifest_ready":playlist.is_file(),
            "segment_count":segments,
            "seconds_since_start":round(time.monotonic()-item["created"],1),
            "ffmpeg_error":_safe_ffmpeg_message(directory) if returncode is not None else ""}
    if probe:
        result["segment_probe"]=_probe_segment(directory)
        if playlist.is_file():
            try:
                lines=playlist.read_text(errors="replace").splitlines()
                result["playlist_valid"]=bool(lines and lines[0].strip()=="#EXTM3U")
                result["playlist_segment_refs"]=sum(1 for line in lines
                                  if line.strip().endswith(".ts") and not line.startswith("#"))
            except OSError:
                result["playlist_valid"]=False
    return result


@router.get("/{sid}/status")
def live_status(sid:str,probe:bool=False):
    _cleanup()
    with LOCK:
        item=ACTIVE.get(sid)
        if not item:raise HTTPException(404,"Tablo stream ended or was unavailable")
        item["last"]=time.monotonic()
        return _health(item,probe=probe)


@router.get("/{sid}/{file_name}")
def live_segment(sid:str,file_name:str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    if file_name!="index.m3u8" and not re.fullmatch(r"seg_\d{5,10}\.ts",file_name):
        raise HTTPException(404,"Invalid media filename")
    _cleanup()
    with LOCK:
        item=ACTIVE.get(sid)
        if not item:raise HTTPException(404,"Tablo stream expired")
        item["last"]=time.monotonic()
        directory=item["directory"]
    path=directory/file_name
    if file_name=="index.m3u8" and not path.exists():
        for _ in range(32):
            if path.exists():break
            with LOCK:
                process=item["process"]
                if process.poll() is not None:break
            time.sleep(.5)
    if not path.is_file():
        info=_health(item)
        if info["ffmpeg_exit_code"] is not None:
            raise HTTPException(502,"Tablo FFmpeg stopped (exit "+
                str(info["ffmpeg_exit_code"])+"): "+(info["ffmpeg_error"] or
                "no error text produced"))
        raise HTTPException(503,"Tablo transcoder is still preparing the HLS stream")
    return FileResponse(path,media_type=("application/vnd.apple.mpegurl" if
       file_name.endswith(".m3u8") else "video/mp2t"),
       headers={"Cache-Control":"no-store"})

@router.delete("/{sid}")
def close_live(sid:str):
    return {"stopped":_stop(sid)}
