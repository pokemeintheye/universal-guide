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
# Keep the rolling DVR on the writable appdata volume, not Compose's 16 MiB /tmp.
STORE=Path(os.getenv("TABLO_CACHE_DIR","/data/tablo-live-cache"))
DVR_MINUTES=max(2,min(30,int(os.getenv("TABLO_DVR_MINUTES","20"))))
DVR_SEGMENT_SECONDS=4
DVR_SEGMENT_COUNT=(DVR_MINUTES*60)//DVR_SEGMENT_SECONDS
CACHE_PREPARED=False
MAX_SESSIONS=2
IDLE_SECONDS=120
FAILED_SESSION_SECONDS=240
ACTIVE={}
LOCK=threading.RLock()

def _prepare_cache():
    """Remove abandoned per-session files from an earlier container process."""
    global CACHE_PREPARED
    with LOCK:
        if CACHE_PREPARED:return
        STORE.mkdir(mode=0o700,parents=True,exist_ok=True)
        for path in STORE.iterdir():
            if (path.is_dir() and not path.is_symlink()
                    and re.fullmatch(r"[A-Za-z0-9_-]{20,80}",path.name)):
                shutil.rmtree(path,ignore_errors=True)
        CACHE_PREPARED=True


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
    if session.get("follower_stop"):
        session["follower_stop"].set()
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
            or (target.port or 80) not in allowed_ports or not stream_path):
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
    _prepare_cache()
    sid=secrets.token_urlsafe(20)
    directory=STORE/sid
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    args=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
          "-user_agent",USER_AGENT,"-i",playlist,
          "-map","0:v:0","-map","0:a:0?","-vf","yadif",
          "-c:v","libx264","-preset","veryfast","-tune","zerolatency",
          "-force_key_frames","expr:gte(t,n_forced*4)",
          "-pix_fmt","yuv420p","-b:v","3000k","-maxrate","3500k","-bufsize","6000k",
          "-c:a","aac","-ac","2","-b:a","128k",
          "-f","hls","-hls_time",str(DVR_SEGMENT_SECONDS),
          "-hls_list_size",str(DVR_SEGMENT_COUNT),
          "-hls_delete_threshold","3",
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
            "dvr_window_target_seconds":DVR_MINUTES*60,
            "note":"One continuously recording DVR HLS stream; seekable window grows over time."}

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
             "stream=codec_type,codec_name,width,height,pix_fmt:format=start_time,duration",
             "-of","json",str(segment)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=5,check=False)
        if p.returncode:
            return {"status":"probe_failed","exit_code":p.returncode,
                    "message":p.stderr[-180:].strip()}
        payload=json.loads(p.stdout)
        streams=payload.get("streams",[])
        fmt=payload.get("format",{})
        # MPEG-TS PTS in the transcoded segment can expose how far the
        # live transcoder has progressed since its input was opened.
        def fnum(value):
            try:return round(float(value),2)
            except (TypeError,ValueError):return None
        return {"status":"ok" if streams else "no_decodable_streams",
                "segment_timestamp_seconds":fnum(fmt.get("start_time")),
                "segment_duration_seconds":fnum(fmt.get("duration")),
                "segment_bytes":segment.stat().st_size,
                "streams":[{k:v for k,v in stream.items()
                           if k in ("codec_type","codec_name","width","height","pix_fmt")}
                          for stream in streams[:3]]}
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        return {"status":"probe_error","error":type(exc).__name__}


def _disk_dvr_playlist_window(path):
    """Read bounded HLS manifest metadata without exposing media paths or URLs."""
    try:
        if not path.is_file():return {"status":"not_ready"}
        if path.stat().st_size>512*1024:return {"status":"playlist_too_large"}
        lines=path.read_text(errors="replace").splitlines()
        if not lines or lines[0].strip()!="#EXTM3U":
            return {"status":"invalid_playlist"}
        duration=0.0
        count=0
        sequence=None
        for line in lines:
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:sequence=int(line.partition(":")[2])
                except ValueError:pass
            elif line.startswith("#EXTINF:"):
                try:duration+=float(line.partition(":")[2].split(",",1)[0]);count+=1
                except ValueError:pass
        return {"status":"ok","segment_count":count,
                "window_seconds":round(duration,1),
                "first_sequence":sequence,
                "last_sequence":sequence+count-1 if sequence is not None and count else None}
    except OSError:
        return {"status":"read_error"}


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
    if not item.get("parent"):
        result["disk_dvr_playlist"]=_disk_dvr_playlist_window(playlist)
        result["mode"]="rolling_dvr"
        result["dvr_window_target_seconds"]=DVR_MINUTES*60
        result["dvr_segment_target_seconds"]=DVR_SEGMENT_SECONDS
    if item.get("mode")=="continuous_timeshift":
        result["mode"]="continuous_timeshift"
        result["source_follow"]=item.get("source_follow",{})
        result["follow_error"]=item.get("follow_error","")
        result["source_start_sequence"]=item.get("start_sequence")
        result["from_pause_marker"]=item.get("from_pause_marker",False)
        result["resume_adjust_seconds"]=item.get("resume_adjust_seconds",0)
        if item.get("resume_source_pts") is not None:
            result["resume_source_pts"]=item["resume_source_pts"]
        result["initial_source_position"]=item.get("initial_source_position",{})
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
        now=time.monotonic()
        item["last"]=now
        if item.get("parent") in ACTIVE:
            ACTIVE[item["parent"]]["last"]=now
        else:
            # Keep a paused preview around while the browser refreshes
            # the original live tuner lease. No additional media is read.
            for child in ACTIVE.values():
                if child.get("parent")==sid:
                    child["last"]=now
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
    # Tablo native HLS playlists grow with the DVR buffer: allow a bounded
    # 4 MiB text playlist for 20-minute seek tests, never media payloads.
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
                if len(raw)>4*1024*1024:
                    raise HTTPException(502,"Tablo playlist exceeded 4 MiB safety limit")
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
                    or (target.port or 80) not in allowed_ports
                    or target.username or target.password or target.fragment
                    or not target.path.startswith("/stream/")
                    or not target.path.lower().endswith(".ts")):
                raise HTTPException(502,"Tablo playlist contains an unexpected segment destination")
            urls.append(candidate)
            expect_uri=False
    return urls


def _native_segment_layout(origin,media_url,body,sequence=None):
    """Report repeated segment URLs and byte-range usage without exposing URLs."""
    urls=_media_segment_urls(origin,media_url,body)
    base=_summarize_source_playlist(body)["media_sequence"]
    ranges=sum(line.strip().startswith("#EXT-X-BYTERANGE:") for line in body.splitlines())
    maps=sum(line.strip().startswith("#EXT-X-MAP:") for line in body.splitlines())
    distinct=len(set(urls))
    out={"entries":len(urls),"unique_segment_urls":distinct,
         "repeated_url_entries":len(urls)-distinct,
         "byte_range_tags":ranges,"map_tags":maps,
         "has_byte_ranges":bool(ranges),"has_repeated_urls":distinct<len(urls)}
    if sequence is not None and base is not None:
        i=sequence-base
        if 0<=i<len(urls):
            matching=[j for j,u in enumerate(urls) if u==urls[i]]
            out["selected_segment_sequence"]=sequence
            out["selected_url_occurrences"]=len(matching)
            out["selected_url_first_sequence"]=base+matching[0]
            out["selected_url_last_sequence"]=base+matching[-1]
    return out

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


def _probe_native_segment_pts(url):
    """Read a bounded TS sample and report its first video PTS without URLs."""
    import tempfile
    try:
        with requests.get(url,headers={"User-Agent":USER_AGENT,
                "Accept":"video/mp2t","Range":"bytes=0-262143"},
                timeout=(3,8),allow_redirects=False,stream=True) as resp:
            if resp.status_code not in (200,206):
                return {"status":"source_http_error","http_status":resp.status_code}
            sample=bytearray()
            for chunk in resp.iter_content(chunk_size=16384):
                sample.extend(chunk[:max(0,262144-len(sample))])
                if len(sample)>=262144:break
        # Hash only the bounded video bytes to determine whether different
        # Tablo playlist entries actually return identical media content.
        import hashlib
        digest=hashlib.sha256(sample).hexdigest()[:20]
        if len(sample)<376 or sample[0]!=0x47 or sample[188]!=0x47:
            return {"status":"not_ts","sample_bytes":len(sample),
                    "sample_sha256_prefix":digest}
        with tempfile.TemporaryDirectory(prefix="tablo-pts-") as temp:
            path=Path(temp)/"segment.ts"
            path.write_bytes(sample)
            cmd=["ffprobe","-v","error","-select_streams","v:0",
                 "-show_packets","-show_entries","packet=pts_time",
                 "-of","csv=p=0",str(path)]
            result=subprocess.run(cmd,capture_output=True,text=True,
                                  timeout=8,check=False)
        timestamps=[]
        for line in result.stdout.splitlines():
            try:
                value=float(line.strip().split(",",1)[0])
                if value>=0:timestamps.append(value)
            except ValueError:pass
        return {"status":"ok" if timestamps else "no_video_pts",
                "sample_bytes":len(sample),
                "sample_sha256_prefix":digest,
                "first_video_pts_seconds":round(timestamps[0],3) if timestamps else None,
                "last_sample_video_pts_seconds":round(timestamps[-1],3) if timestamps else None}
    except (requests.RequestException,OSError,subprocess.TimeoutExpired) as exc:
        return {"status":"probe_error","error":type(exc).__name__}


def _source_pts_for_sequence(origin,media_url,body,sequence):
    """Use validated native segment URLs only; never include URLs in output."""
    summary=_summarize_source_playlist(body)
    first=summary["media_sequence"]
    if first is None:return {"status":"no_source_sequence"}
    urls=_media_segment_urls(origin,media_url,body)
    index=sequence-first
    if index<0 or index>=len(urls):return {"status":"segment_not_available"}
    return {"source_sequence":sequence,**_probe_native_segment_pts(urls[index])}


def _source_byte_ranges(body,urls):
    """Rebuild explicit byte ranges from native HLS tags, including implicit offsets."""
    ranges=[]
    pending=None
    last_url=None
    last_end=None
    for line in body.splitlines():
        line=line.strip()
        if line.startswith("#EXT-X-BYTERANGE:"):
            if pending is not None:
                raise HTTPException(502,"Duplicate Tablo HLS byte range tag")
            value=line.split(":",1)[1]
            match=re.fullmatch(r"(\d+)(?:@(\d+))?",value)
            if not match or int(match.group(1))<=0:
                raise HTTPException(502,"Invalid Tablo HLS byte range")
            pending=(int(match.group(1)),int(match.group(2)) if match.group(2) is not None else None)
        elif line and not line.startswith("#"):
            if len(ranges)>=len(urls):
                continue
            current_url=urls[len(ranges)]
            if pending is None:
                ranges.append(None)
                last_end=None
            else:
                length,offset=pending
                if offset is None:
                    if current_url!=last_url or last_end is None:
                        raise HTTPException(502,"Tablo HLS implicit byte range has no previous range")
                    offset=last_end
                ranges.append((length,offset))
                last_end=offset+length
            last_url=current_url
            pending=None
    if pending is not None or len(ranges)!=len(urls):
        raise HTTPException(502,"Tablo segment byte ranges are incomplete")
    return ranges


def _historic_clip_manifest(origin,media_url,body,seconds_behind_live=120,clip_seconds=75):
    """Make a bounded, finite HLS timeline for a seek playback proof of concept.

    The original Tablo still holds the buffer; only a short clip is transcoded
    to the existing temporary playback directory, not the entire history.
    """
    if not isinstance(clip_seconds,int) or not 30<=clip_seconds<=300:
        raise HTTPException(400,"Historical clip length must be 30 to 300 seconds")
    if not isinstance(seconds_behind_live,int) or not 30<=seconds_behind_live<=1800:
        raise HTTPException(400,"Preview offset must be 30 to 1800 seconds")
    urls=_media_segment_urls(origin,media_url,body)
    byte_ranges=_source_byte_ranges(body,urls)
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
        chosen.append((urls[index],durations[index],byte_ranges[index]))
        elapsed+=durations[index]
        if elapsed>=clip_seconds:break
    if elapsed<15:
        raise HTTPException(409,"Not enough buffered video for a test clip")
    output=["#EXTM3U","#EXT-X-VERSION:"+("4" if any(byte_ranges) else "3"),
            "#EXT-X-TARGETDURATION:"+str(max(1,int(__import__("math").ceil(max(d for _,d,_ in chosen))))),
            "#EXT-X-MEDIA-SEQUENCE:0"]
    for url,duration,byte_range in chosen:
        output.append("#EXTINF:"+format(duration,".3f")+",")
        if byte_range is not None:
            length,offset=byte_range
            output.append("#EXT-X-BYTERANGE:"+str(length)+"@"+str(offset))
        output.append(url)
    output.append("#EXT-X-ENDLIST")
    return "\n".join(output)+"\n",{"start_seconds_behind_live":round(total-accumulated,1),
                                  "clip_duration_seconds":round(elapsed,1),
                                  "segment_count":len(chosen)}



def _continuous_source_manifest(origin,media_url,body,start_sequence=None,
                                seconds_behind_live=120):
    """Create a live (non-ENDLIST) HLS playlist from Tablo's existing segments.

    Source sequence numbers, unlike playlist indices, remain stable as Tablo
    grows or eventually prunes its native buffer.
    """
    urls=_media_segment_urls(origin,media_url,body)
    byte_ranges=_source_byte_ranges(body,urls)
    durations=[]
    for line in body.splitlines():
        if line.startswith("#EXTINF:"):
            try:durations.append(float(line[8:].split(",",1)[0]))
            except ValueError:raise HTTPException(502,"Invalid Tablo segment duration")
    if not urls or len(durations)!=len(urls) or min(durations)<=0:
        raise HTTPException(502,"Tablo source playlist has incomplete segment metadata")
    summary=_summarize_source_playlist(body)
    base_sequence=summary["media_sequence"]
    if base_sequence is None:
        raise HTTPException(502,"Tablo source playlist has no media sequence")
    if start_sequence is None:
        if not isinstance(seconds_behind_live,int) or not 30<=seconds_behind_live<=1800:
            raise HTTPException(400,"Timeshift position must be 30 to 1800 seconds behind live")
        if sum(durations)<seconds_behind_live+10:
            raise HTTPException(409,"Not enough native Tablo history for timeshift")
        remaining=seconds_behind_live
        index=len(durations)-1
        while index>0 and remaining>0:
            remaining-=durations[index]
            index-=1
        start_sequence=base_sequence+index+1
    first=start_sequence-base_sequence
    if first<0 or first>=len(urls):
        raise HTTPException(409,"Requested pause position is no longer in the Tablo buffer")
    # Avoid silently skipping requested history for extremely long playlists.
    if len(urls)-first>2400:
        raise HTTPException(409,"Requested timeshift window exceeds the current safety cap")
    chosen=list(zip(urls[first:],durations[first:],byte_ranges[first:]))
    output=["#EXTM3U","#EXT-X-VERSION:"+("4" if any(byte_ranges) else "3"),
            "#EXT-X-TARGETDURATION:"+str(max(1,__import__("math").ceil(max(d for _,d,_ in chosen)))),
            "#EXT-X-MEDIA-SEQUENCE:"+str(base_sequence+first)]
    for url,d,byte_range in chosen:
        output.append("#EXTINF:"+format(d,".3f")+",")
        if byte_range is not None:
            length,offset=byte_range
            output.append("#EXT-X-BYTERANGE:"+str(length)+"@"+str(offset))
        output.append(url)
    return "\n".join(output)+"\n",{"start_sequence":start_sequence,
        "first_listed_sequence":base_sequence,
        "last_sequence":base_sequence+len(urls)-1,
        "seconds_behind_source_live":round(sum(durations[first:]),1),
        "available_seconds":round(sum(durations),1),
        "source_segments":len(chosen)}


def _advance_source_sequence(body,source_sequence,seconds):
    """Move a pause marker forward by HLS EXTINF durations, never beyond live."""
    if not isinstance(seconds,int) or seconds<0 or seconds>120:
        raise HTTPException(400,"Resume adjustment must be 0 to 120 seconds")
    if seconds==0:
        return source_sequence,0.0
    summary=_summarize_source_playlist(body)
    first=summary["media_sequence"]
    if first is None:
        raise HTTPException(502,"Tablo playlist is missing a media sequence")
    durations=[]
    for line in body.splitlines():
        if line.startswith("#EXTINF:"):
            try:durations.append(float(line[8:].split(",",1)[0]))
            except ValueError:raise HTTPException(502,"Invalid Tablo segment duration")
    if not durations or min(durations)<=0:
        raise HTTPException(502,"Tablo playlist has invalid segment durations")
    index=source_sequence-first
    if index<0 or index>=len(durations):
        raise HTTPException(409,"Captured pause segment is no longer available")
    # Require a 10-second safety gap from source live; do not jump to live
    # merely because the chosen adjustment exceeds available history.
    latest=max(0,len(durations)-11)
    elapsed=0.0
    while index<latest and elapsed<seconds:
        elapsed+=durations[index]
        index+=1
    if elapsed+0.01<seconds:
        raise HTTPException(409,"Not enough Tablo history has accumulated for this resume adjustment")
    return first+index,round(elapsed,1)


def _pause_source_sequence(body,overlap_seconds=18):
    """Identify a native Tablo segment near the current playback point.

    This is captured at the instant Pause is pressed, not reconstructed
    from the duration of the pause. A short overlap covers live-HLS latency.
    """
    summary=_summarize_source_playlist(body)
    base=summary["media_sequence"]
    durations=[]
    for line in body.splitlines():
        if line.startswith("#EXTINF:"):
            try:durations.append(float(line[8:].split(",",1)[0]))
            except ValueError:raise HTTPException(502,"Invalid Tablo segment duration")
    if base is None or not durations or min(durations)<=0:
        raise HTTPException(502,"Tablo pause point needs a sequenced media playlist")
    if sum(durations)<overlap_seconds+5:
        raise HTTPException(409,"Not enough native Tablo history to capture a pause point")
    age=0.0
    index=len(durations)-1
    while index>0 and age<overlap_seconds:
        age+=durations[index]
        index-=1
    return base+index,round(age,1)


@router.post("/{sid}/pause-point")
def capture_pause_point(sid:str):
    """Remember the Tablo native media sequence when user presses Pause Live."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    _cleanup()
    with LOCK:
        parent=ACTIVE.get(sid)
        if not parent or parent.get("parent") or parent["process"].poll() is not None:
            raise HTTPException(404,"Original Tablo live session is unavailable")
        parent["last"]=time.monotonic()
        origin,source=parent["origin"],parent["source_playlist"]
    media_url,body,_=_source_media_playlist(origin,source)
    sequence,overlap=_pause_source_sequence(body)
    # Diagnostic only: compare the source live edge with the original
    # transcoder's most recently written MPEG-TS presentation timestamp.
    # Never use this value to modify the requested pause segment yet.
    live_probe=_probe_segment(parent["directory"])
    source_pts=_source_pts_for_sequence(origin,media_url,body,sequence)
    source_edge_pts=_source_pts_for_sequence(
        origin,media_url,body,_summarize_source_playlist(body)["last_media_sequence"]-3)
    native_urls=_media_segment_urls(origin,media_url,body)
    native_base=_summarize_source_playlist(body)["media_sequence"]
    first_idx=sequence-native_base
    edge_idx=_summarize_source_playlist(body)["last_media_sequence"]-3-native_base
    native_entry_urls_distinct=(
        0<=first_idx<len(native_urls) and 0<=edge_idx<len(native_urls)
        and native_urls[first_idx]!=native_urls[edge_idx])
    native_samples_match=(
        bool(source_pts.get("sample_sha256_prefix"))
        and source_pts.get("sample_sha256_prefix")==source_edge_pts.get("sample_sha256_prefix"))
    live_age=round(time.monotonic()-parent["created"],1)
    with LOCK:
        if ACTIVE.get(sid) is not parent:
            raise HTTPException(409,"Tablo stream closed before pause marker was stored")
        parent["pause_marker"]={"sequence":sequence,"captured":time.monotonic(),
                                "media_url":media_url,"overlap":overlap}
    return {"captured":True,"source_sequence":sequence,
            "seconds_behind_source_live":overlap,
            "source_playlist_segments":_summarize_source_playlist(body)["segment_count"],
            "source_last_sequence":_summarize_source_playlist(body)["last_media_sequence"],
            "live_transcoder_age_seconds":live_age,
            "live_segment_probe":live_probe,
            "pause_source_pts":source_pts,
            "source_edge_pts":source_edge_pts,
            "native_entry_urls_distinct":native_entry_urls_distinct,
            "native_segment_samples_match":native_samples_match,
            "native_segment_layout":_native_segment_layout(origin,media_url,body,sequence)}


def _follow_tablo_source(sid,stop_event):
    """Refresh the file-based live HLS input while FFmpeg follows it.

    FFmpeg reads Tablo media directly. This worker only retrieves small text
    playlists, not video segments. On errors, retry; do not expose signed URLs.
    """
    while not stop_event.wait(2):
        with LOCK:
            item=ACTIVE.get(sid)
            if not item:return
            origin=item["origin"]
            source=item["source_playlist"]
            start_sequence=item["start_sequence"]
            directory=item["directory"]
            process=item["process"]
        if process.poll() is not None:return
        try:
            media_url,body,_=_source_media_playlist(origin,source)
            manifest,info=_continuous_source_manifest(
                origin,media_url,body,start_sequence=start_sequence)
            target=directory/"continuous-input.m3u8"
            if target.read_text()!=manifest:
                temp=directory/"continuous-input.next"
                temp.write_text(manifest)
                temp.replace(target)
            with LOCK:
                if ACTIVE.get(sid) is item:
                    item["source_follow"]=info
                    item["follow_error"]=""
        except (HTTPException,ValueError,OSError) as exc:
            # Never expose the upstream stream URL or token in a message.
            with LOCK:
                if ACTIVE.get(sid) is item:
                    item["follow_error"]=type(exc).__name__


@router.post("/{sid}/timeshift")
def continuous_timeshift(sid:str,seconds_behind_live:int=120,resume_paused:bool=False,resume_adjust_seconds:int=0):
    """Beta: rolling live HLS output from Tablo's growing native playlist."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}",sid):
        raise HTTPException(404,"Invalid session")
    _cleanup()
    with LOCK:
        parent=ACTIVE.get(sid)
        if not parent or parent.get("parent"):
            raise HTTPException(404,"Original Tablo stream has ended")
        if parent["process"].poll() is not None:
            raise HTTPException(409,"Original Tablo transcoder has stopped")
        if any(x.get("parent")==sid for x in ACTIVE.values()):
            raise HTTPException(409,"A Tablo history session is already running")
        parent["last"]=time.monotonic()
        origin,source=parent["origin"],parent["source_playlist"]
        marker=parent.get("pause_marker") if resume_paused else None
        if resume_paused and not marker:
            raise HTTPException(409,"No pause position was captured; start a new Pause Live test")
        if resume_paused and time.monotonic()-marker["captured"]>1800:
            raise HTTPException(409,"Pause position is older than the supported 30-minute window")
    media_url,body,_=_source_media_playlist(origin,source)
    if resume_paused:
        if media_url!=marker["media_url"]:
            raise HTTPException(409,"Tablo media playlist changed after pausing")
        adjusted_sequence,actual_adjust=_advance_source_sequence(
            body,marker["sequence"],resume_adjust_seconds)
        manifest,info=_continuous_source_manifest(
            origin,media_url,body,start_sequence=adjusted_sequence)
        resume_source_pts=_source_pts_for_sequence(
            origin,media_url,body,adjusted_sequence)
    else:
        if resume_adjust_seconds:
            raise HTTPException(400,"Resume adjustment requires a saved Pause Live marker")
        actual_adjust=0.0
        manifest,info=_continuous_source_manifest(
            origin,media_url,body,seconds_behind_live=seconds_behind_live)
        resume_source_pts=None
    child_id=secrets.token_urlsafe(20)
    directory=STORE/child_id
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    input_path=directory/"continuous-input.m3u8"
    input_path.write_text(manifest)
    input_path.chmod(0o600)
    # -re prevents transcoding far ahead of the viewer. A rolling 90-segment
    # cache bounds temporary files on Unraid; historical media stays on Tablo.
    args=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
          # The HLS demuxer defaults to index -3 (near live), even if our
          # native-buffer playlist begins much earlier. Begin at index 0.
          "-re","-live_start_index","0",
          "-protocol_whitelist","file,http,tcp,crypto",
          "-i",str(input_path),
          "-map","0:v:0","-map","0:a:0?","-vf","yadif",
          "-c:v","libx264","-preset","veryfast","-tune","zerolatency",
          "-pix_fmt","yuv420p","-b:v","3000k","-maxrate","3500k","-bufsize","6000k",
          "-c:a","aac","-ac","2","-b:a","128k",
          "-f","hls","-hls_time","4","-hls_list_size","90",
          "-hls_flags","delete_segments+omit_endlist",
          "-hls_segment_filename",str(directory/"seg_%05d.ts"),
          str(directory/"index.m3u8")]
    try:
        with (directory/"ffmpeg.log").open("wb") as log:
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,stderr=log,close_fds=True)
    except OSError as exc:
        shutil.rmtree(directory,ignore_errors=True)
        raise HTTPException(502,"Could not launch timeshift transcoder: "+type(exc).__name__)
    stop_event=threading.Event()
    now=time.monotonic()
    with LOCK:
        if ACTIVE.get(sid) is not parent:
            process.terminate()
            shutil.rmtree(directory,ignore_errors=True)
            raise HTTPException(409,"Original Tablo stream closed")
        ACTIVE[child_id]={"id":child_id,"directory":directory,"process":process,
                          "last":now,"created":now,"channel":parent["channel"],
                          "parent":sid,"origin":origin,"source_playlist":source,
                          "start_sequence":info["start_sequence"],
                          "source_follow":info,"follow_error":"",
                          "from_pause_marker":bool(resume_paused),
                          "resume_adjust_seconds":actual_adjust,
                          "resume_source_pts":resume_source_pts,
                          "initial_source_position":info,
                          "follower_stop":stop_event,"mode":"continuous_timeshift"}
    thread=threading.Thread(target=_follow_tablo_source,
                            args=(child_id,stop_event),daemon=True)
    thread.start()
    return {"session_id":child_id,
            "playlist_url":"/api/tablo/live/"+child_id+"/index.m3u8",
            "experiment":"continuous_timeshift_beta",
            "start_seconds_behind_live":seconds_behind_live if not resume_paused else None,
            "resumed_from_pause_marker":bool(resume_paused),
            "resume_adjust_seconds":actual_adjust,
            "pause_marker_age_seconds":round(time.monotonic()-marker["captured"],1) if resume_paused else None,
            "source_start_sequence":info["start_sequence"],
            "source_behind_live_seconds":info["seconds_behind_source_live"],
            "note":"Rolling HLS output starts from selected Tablo historical segment."}


@router.post("/{sid}/preview")
def historical_preview(sid:str,seconds_behind_live:int=120,clip_seconds:int=75):
    """Experimental bounded clip from Tablo history; original live tuner untouched."""
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
    manifest,info=_historic_clip_manifest(origin,media_url,body,seconds_behind_live,clip_seconds)
    child_id=secrets.token_urlsafe(20)
    directory=STORE/child_id
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    input_path=directory/"historical-input.m3u8"
    input_path.write_text(manifest)
    input_path.chmod(0o600)
    args=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
          "-protocol_whitelist","file,http,tcp,crypto",
          # Input is a local .m3u8 file; FFmpeg rejects -user_agent here.
          "-i",str(input_path),
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
            "note":"Bounded native-buffer preview; original live session remains active."}


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
    media_url,media_body,_=_source_media_playlist(origin,playlist)
    native_layout=_native_segment_layout(origin,media_url,media_body)
    return {"session_id":sid,"source":"tablo_origin","native_segment_layout":native_layout,"playlist":summary,
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
