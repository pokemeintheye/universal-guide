"""HGTV-only supplemental EPG. Never changes other channel schedules."""
import gzip
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

URL="https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
MAX_COMPRESSED=20*1024*1024
MAX_XML=150*1024*1024

def _channel_score(labels,channel_id):
    """Only the US Eastern HGTV schedule; avoid Canada or Pacific feeds."""
    texts=[str(v or "").strip().casefold() for v in labels]
    for label in texts:
        clean=re.sub(r"[^a-z0-9]+"," ",label).strip()
        if clean in ("hgtv usa eastern feed","hgtv us eastern feed",
                     "hgtv hd east","hgtv east","hgtv usa east"):
            return 100
        if clean in ("hgtv usa","hgtv us","hgtv","hgtv hd"):
            return 70
    # Some sources carry an informative ID but an abbreviated display name.
    normalized=re.sub(r"[^a-z0-9]+"," ",str(channel_id or "").casefold())
    if normalized.startswith("hgtv usa eastern feed ") and "us2" in normalized:
        return 85
    return 0

def fetch_hgtv(xml_date,xmltv_kind,xmltv_episode):
    """Read only HGTV US/East airings. Return (rows, status); no side effects.

    Status contains only counts, channel IDs and error types, never credentials.
    """
    status={"source":"EPGShare US2","url":URL,"selected_channel_id":None,
            "matched_channels":0,"programs":0,"error":""}
    try:
        with requests.get(URL,timeout=(8,65),stream=True) as response:
            response.raise_for_status()
            compressed=bytearray()
            for chunk in response.iter_content(chunk_size=128*1024):
                compressed.extend(chunk)
                if len(compressed)>MAX_COMPRESSED:
                    raise ValueError("supplement compressed size limit exceeded")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            data=stream.read(MAX_XML+1)
        if len(data)>MAX_XML:raise ValueError("supplement XML size limit exceeded")
        root=ET.fromstring(data)
        matches=[]
        for channel in root.findall("channel"):
            labels=[(n.text or "").strip() for n in channel.findall("display-name")]
            channel_id=channel.get("id","")
            score=_channel_score(labels,channel_id)
            if score:matches.append((score,channel_id,labels))
        status["matched_channels"]=len(matches)
        if not matches:
            status["error"]="HGTV US/East not found in supplemental feed"
            return [],status
        matches.sort(key=lambda row:(-row[0],row[1]))
        best_score=matches[0][0]
        ids={identifier for score,identifier,_ in matches if score==best_score}
        now=datetime.now(timezone.utc)
        cutoff=now+timedelta(days=4)
        by_id={identifier:[] for identifier in ids}
        for program in root.findall("programme"):
            identifier=program.get("channel")
            if identifier not in by_id:continue
            start=xml_date(program.get("start"))
            stop=xml_date(program.get("stop"))
            if not start or not stop or stop<=now.isoformat() or start>=cutoff.isoformat():
                continue
            text=lambda tag:(program.findtext(tag) or "").strip()
            title=text("title")
            if not title:continue
            raw_year=text("date")
            year=int(raw_year[:4]) if re.match(r"^\\d{4}",raw_year) else 0
            by_id[identifier].append((
                "HGTV",start,stop,title,text("sub-title"),year,
                text("desc")[:600],xmltv_kind(program),*xmltv_episode(program)))
        # Prefer the best-covered equally ranked feed, never merge time-shifted variants.
        channel_id=max(sorted(ids),key=lambda key:len(by_id[key]))
        rows=by_id[channel_id]
        status["selected_channel_id"]=channel_id[:100]
        status["programs"]=len(rows)
        status["error"]="" if rows else "HGTV matched but supplemental schedule has no future programs"
        return rows,status
    except (requests.RequestException,OSError,ET.ParseError,ValueError,OverflowError) as exc:
        status["error"]=type(exc).__name__+": "+str(exc)[:130]
        return [],status
