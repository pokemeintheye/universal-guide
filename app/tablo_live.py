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
    # Closing the original tuner also closes previews sharing its Tablo session.
    with LOCK:
        children=[key for key,state in ACTIVE.items() if state.get("parent")==sid]
    for child in children:
        _stop(child)
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
        if sum(1 for state in ACTIVE.values() if not state.get("parent") and state["process"].poll() is None)>=MAX_SESSIONS:
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
                     "channel":channel_id,"tune_status":r.status_code,
                     "origin":origin,"source_playlist":playlist}
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


# Diagnostic reads metadata from the Tablo's own HLS playlist, not video segments.
# Keep remote URLs and playback tokens entirely server-side.
def _summarize_source_playlist(body):
    lines=[line.strip() for line in body.splitlines() if line.strip()]
    if not lines or lines[0]!="#EXTM3U":
        raise ValueError("Tablo response was not an HLS playlist")
    def integer_tag(name):
        marker="#"+name+":"
        for line in lines:
            if line.startswith(marker):
                try:return int(line[len(marker):])
                except ValueError:return None
        return None
    def decimal_tag(name):
        marker="#"+name+":"
        for line in lines:
            if line.startswith(marker):
                try:return float(line[len(marker):].split(",",1)[0])
                except ValueError:return None
        return None
    variants=sum(1 for line in lines if line.startswith("#EXT-X-STREAM-INF:"))
    media_segments=sum(1 for line in lines if line.startswith("#EXTINF:"))
    durations=[decimal_tag_value.split(",",1)[0] for line in lines
               if line.startswith("#EXTINF:")
               for decimal_tag_value in [line[len("#EXTINF:"):]]]
    duration=0.0
    for value in durations:
        try:duration+=float(value)
        except ValueError:pass
    sequence=integer_tag("EXT-X-MEDIA-SEQUENCE")
    kind="master" if variants else "media" if media_segments else "unknown"
    return {
        "kind":kind,
        "variant_count":variants,
        "segment_count":media_segments,
        "media_sequence":sequence,
        "last_media_sequence":sequence+media_segments-1
            if sequence is not None and media_segments else None,
        "window_seconds":round(duration,1),
        "target_duration_seconds":integer_tag("EXT-X-TARGETDURATION"),
        "endlist":("#EXT-X-ENDLIST" in lines),
        "playlist_type":next((line.split(":",1)[1] for line in lines
                               if line.startswith("#EXT-X-PLAYLIST-TYPE:")),None),
        "program_date_time_present":any(line.startswith("#EXT-X-PROGRAM-DATE-TIME:")
                                         for line in lines),
        "discontinuity_count":sum(line=="#EXT-X-DISCONTINUITY" for line in lines),
        "encryption_declared":any(line.startswith("#EXT-X-KEY:") for line in lines),
    }


def _read_source_playlist(url):
    # Do not follow redirects. The URL was already restricted to the Tablo host
    # by _allowed_url when the tuner was created.
    try:
        with requests.get(url,headers={"User-Agent":USER_AGENT,
                          "Accept":"application/vnd.apple.mpegurl"},
                          timeout=(3,8),allow_redirects=False,stream=True) as response:
            if response.status_code!=200:
                raise HTTPException(502,"Tablo playlist returned HTTP "+
                                    str(response.status_code))
            raw=bytearray()
            for part in response.iter_content(chunk_size=8192):
                raw.extend(part)
                if len(raw)>262144:
                    raise HTTPException(502,"Tablo playlist exceeded diagnostic size limit")
            return raw.decode("utf-8",errors="replace")
    except requests.RequestException as exc:
        # Never show exception text: it may contain a signed playlist URL.
        raise HTTPException(502,"Tablo playlist read failed: "+
                            type(exc).__name__) from None


def _source_media_playlist(origin,playlist):
    """Resolve one media variant without returning signed URLs to callers."""
    body=_read_source_playlist(playlist)
    summary=_summarize_source_playlist(body)
    if summary["kind"]=="master":
        lines=body.splitlines()
        variant=next((lines[i+1].strip() for i,line in enumerate(lines[:-1])
                      if line.strip().startswith("#EXT-X-STREAM-INF:")
                      and lines[i+1].strip() and not lines[i+1].startswith("#")),None)
        if not variant:
            raise HTTPException(502,"Tablo master playlist contains no media variant")
        playlist=_allowed_url(origin,urljoin(playlist,variant))
        body=_read_source_playlist(playlist)
        summary=_summarize_source_playlist(body)
        summary["master_variant_selected"]=True
    if summary["kind"]!="media":
        raise HTTPException(502,"Tablo did not expose an inspectable media playlist")
    return playlist,body,summary


def _media_segment_urls(origin,playlist,body):
    """Only resolve segment references that immediately follow EXTINF."""
    urls=[]
    expect_uri=False
    base=urlparse(origin)
    allowed_ports={base.port or 80,80,8885,8888}
    for line in body.splitlines():
        line=line.strip()
        if line.startswith("#EXTINF:"):
            expect_uri=True
        elif expect_uri and line and not line.startswith("#"):
            candidate=urljoin(playlist,line)
            target=urlparse(candidate)
            # Preserve the same strict local origin restriction used for /watch.
            # Segment paths are not playlist paths and must be .ts media.
            if (target.scheme!="http" or target.hostname!=base.hostname
                    or target.port not in allowed_ports
                    or target.username or target.password or target.fragment
                    or not target.path.startswith("/stream/")
                    or not target.path.lower().endswith(".ts")):
                raise HTTPException(502,"Tablo playlist contains an unexpected segment destination")
            urls.append(candidate)
            expect_uri=False
    return urls


def _probe_source_segment(url):
    """Read <=64 KiB of one segment for existence and MPEG-TS header checks."""
    try:
        with requests.get(url,headers={"User-Agent":USER_AGENT,
                          "Accept":"video/mp2t","Range":"bytes=0-65535"},
                          timeout=(3,7),allow_redirects=False,stream=True) as response:
            status=response.status_code
            if status not in (200,206):
                return {"http_status":status,"readable":False,"sample_bytes":0}
            sample=bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                sample.extend(chunk[:max(0,65536-len(sample))])
                if len(sample)>=65536:
                    break
            # MPEG-TS uses 188-byte packets with 0x47 sync bytes.
            sync= len(sample)>=377 and sample[0]==0x47 and sample[188]==0x47
            return {"http_status":status,"readable":bool(sample),
                    "sample_bytes":len(sample),"mpeg_ts_sync":sync}
    except requests.RequestException as exc:
        # Exception strings can contain upstream token-bearing URLs.
        return {"readable":False,"error":type(exc).__name__}


def _historic_clip_manifest(origin,media_url,body,seconds_behind_live=120,clip_seconds=75):
    """Make a bounded, finite HLS timeline for a seek playback proof of concept.

    The original Tablo still holds the buffer; only a short clip is transcoded
    to the existing temporary playback directory, not the entire history.
    """
    if not isinstance(seconds_behind_live,int) or not 30<=seconds_behind_live<=1800:
        raise HTTPException(400,"Preview offset must be 30 to 1800 seconds")
    urls=_media_segment_urls(origin,media_url,body)
    durations=[]
    for line in body.splitlines():
        if line.startswith("#EXTINF:"):
            try:durations.append(float(line[8:].split(",",1)[0]))
            except ValueError:raise HTTPException(502,"Invalid Tablo segment duration")
    if not urls or len(durations)!=len(urls):
        raise HTTPException(502,"Tablo playlist segment metadata was incomplete")
    total=sum(durations)
    if total<seconds_behind_live+15:
        raise HTTPException(409,"Not enough Tablo buffer has accumulated yet")
    # Find the media segment containing the requested historical position.
    start_time=max(0,total-seconds_behind_live)
    accumulated=0.0
    start=0
    for i,d in enumerate(durations):
        if accumulated+d>start_time:
            start=i
            break
        accumulated+=d
    chosen=[]
    elapsed=0.0
    for index in range(start,len(urls)):
        chosen.append((urls[index],durations[index]))
        elapsed+=durations[index]
        if elapsed>=clip_seconds:break
    if elapsed<15:
        raise HTTPException(409,"Not enough buffered video for a test clip")
    output=["#EXTM3U","#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:"+str(max(1,int(__import__("math").ceil(max(d for _,d in chosen))))),
            "#EXT-X-MEDIA-SEQUENCE:0"]
    for url,duration in chosen:
        output.append("#EXTINF:"+format(duration,".3f")+",")
        output.append(url)
    output.append("#EXT-X-ENDLIST")
    return "\n".join(output)+"\n",{"start_seconds_behind_live":round(total-accumulated,1),
                                  "clip_duration_seconds":round(elapsed,1),
                                  "segment_count":len(chosen)}


@router.post("/{sid}/preview")
def historical_preview(sid:str,seconds_behind_live:int=120):
    """Experimental 75s clip from existing Tablo history; original live tuner untouched."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    _cleanup()
    with LOCK:
        parent=ACTIVE.get(sid)
        if not parent or parent.get("parent"):
            raise HTTPException(404,"Original Tablo stream has ended")
        if parent["process"].poll() is not None:
            raise HTTPException(409,"Original Tablo stream is no longer running")
        if any(x.get("parent")==sid for x in ACTIVE.values()):
            raise HTTPException(409,"A historical preview is already running")
        parent["last"]=time.monotonic()
        origin,source=parent["origin"],parent["source_playlist"]
    media_url,body,_=_source_media_playlist(origin,source)
    manifest,info=_historic_clip_manifest(origin,media_url,body,seconds_behind_live)
    child_id=secrets.token_urlsafe(20)
    directory=STORE/child_id
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    input_path=directory/"historical-input.m3u8"
    input_path.write_text(manifest)
    input_path.chmod(0o600)
    args=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
          "-protocol_whitelist","file,http,tcp,crypto",
          "-user_agent",USER_AGENT,"-i",str(input_path),
          "-map","0:v:0","-map","0:a:0?","-vf","yadif",
          "-c:v","libx264","-preset","veryfast","-tune","zerolatency",
          "-pix_fmt","yuv420p","-b:v","3000k","-maxrate","3500k","-bufsize","6000k",
          "-c:a","aac","-ac","2","-b:a","128k",
          "-f","hls","-hls_time","4","-hls_list_size","0",
          "-hls_segment_filename",str(directory/"seg_%05d.ts"),
          str(directory/"index.m3u8")]
    try:
        with (directory/"ffmpeg.log").open("wb") as log:
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL,stderr=log,close_fds=True)
    except OSError as exc:
        shutil.rmtree(directory,ignore_errors=True)
        raise HTTPException(502,"Could not launch preview transcoder: "+type(exc).__name__)
    with LOCK:
        if ACTIVE.get(sid) is not parent:
            process.terminate()
            shutil.rmtree(directory,ignore_errors=True)
            raise HTTPException(409,"Original Tablo stream closed while starting preview")
        ACTIVE[child_id]={"id":child_id,"directory":directory,"process":process,
                          "last":time.monotonic(),"created":time.monotonic(),
                          "channel":parent["channel"],"parent":sid}
    return {"session_id":child_id,
            "playlist_url":"/api/tablo/live/"+child_id+"/index.m3u8",
            "experiment":"finite_historical_preview",**info,
            "note":"Preview ends after the buffered test clip. Original live stream remains active."}


@router.get("/{sid}/segment-diagnostics")
def segment_diagnostics(sid:str):
    """Read small samples of old/middle/recent native Tablo HLS segments."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    _cleanup()
    with LOCK:
        item=ACTIVE.get(sid)
        if not item:
            raise HTTPException(404,"Tablo stream expired")
        item["last"]=time.monotonic()
        origin,playlist=item["origin"],item["source_playlist"]
    try:
        media_url,body,summary=_source_media_playlist(origin,playlist)
        urls=_media_segment_urls(origin,media_url,body)
    except ValueError:
        raise HTTPException(502,"Tablo did not return a valid playlist") from None
    if not urls:
        raise HTTPException(502,"No Tablo segments were listed in the live playlist")
    # Oldest, midpoint, newest: max three requests, up to 64 KiB each.
    selected=(("oldest",0),("middle",len(urls)//2),("newest",len(urls)-1))
    samples={}
    for label,index in selected:
        if index in [entry["segment_index"] for entry in samples.values()]:
            continue
        result=_probe_source_segment(urls[index])
        samples[label]={"segment_index":index,**result}
    return {"session_id":sid,"source":"tablo_origin",
            "advertised_segments":len(urls),
            "advertised_window_seconds":summary["window_seconds"],
            "samples":samples,
            "limitations":"A readable sample does not yet prove browser seeking and transcoding will work."}


@router.get("/{sid}/source-diagnostics")
def source_diagnostics(sid:str):
    """Read-only inspection of the Tablo-origin playlist, with no media download."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    _cleanup()
    with LOCK:
        item=ACTIVE.get(sid)
        if not item:
            raise HTTPException(404,"Tablo stream expired")
        item["last"]=time.monotonic()
        origin=item["origin"]
        playlist=item["source_playlist"]
        previous=item.get("source_snapshot")
    try:
        body=_read_source_playlist(playlist)
        summary=_summarize_source_playlist(body)
        if summary["kind"]=="master":
            # A master playlist has no timeshift window. Inspect one variant
            # only when its destination is still on the authorized Tablo host.
            lines=body.splitlines()
            variant=next((lines[i+1].strip() for i,line in enumerate(lines[:-1])
                          if line.strip().startswith("#EXT-X-STREAM-INF:")
                          and lines[i+1].strip() and not lines[i+1].startswith("#")),None)
            if variant:
                variant_url=_allowed_url(origin,urljoin(playlist,variant))
                summary=_summarize_source_playlist(_read_source_playlist(variant_url))
                summary["master_variant_selected"]=True
        if summary["kind"]!="media":
            raise HTTPException(502,"Tablo did not expose an inspectable media playlist")
    except ValueError as exc:
        raise HTTPException(502,str(exc)) from None
    current={"observed_at":datetime.now(timezone.utc).isoformat(),
             "media_sequence":summary["media_sequence"],
             "segment_count":summary["segment_count"],
             "window_seconds":summary["window_seconds"]}
    with LOCK:
        if ACTIVE.get(sid) is item:
            item["source_snapshot"]=current
    comparison=None
    if previous:
        a,b=previous.get("media_sequence"),current["media_sequence"]
        comparison={
            "previous_observed_at":previous["observed_at"],
            "media_sequence_advanced":b-a if a is not None and b is not None else None,
            "previous_window_seconds":previous["window_seconds"]
        }
    return {"session_id":sid,"source":"tablo_origin","playlist":summary,
            "observation":current,"previous_observation":comparison,
            "limitations":"Playlist metadata only. This does not establish whether historical segments can be requested or decoded."}


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
        if item.get("parent") in ACTIVE:
            ACTIVE[item["parent"]]["last"]=time.monotonic()
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
