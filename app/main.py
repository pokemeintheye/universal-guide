import os, re, time, sqlite3, threading, xml.etree.ElementTree as ET
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import gzip
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

DB=Path("/data/guide.db")
GUIDE=Path(os.getenv("GUIDE_XMLTV","/data/guide.xml"))
PLEX_URL=os.getenv("PLEX_URL","").rstrip("/")
PLEX_TOKEN=os.getenv("PLEX_TOKEN","")
PLEX_ALLOWED_LABEL=os.getenv("PLEX_ALLOWED_LABEL","").strip()
PLEX_LABEL_MODE=os.getenv("PLEX_LABEL_MODE","allow").strip().casefold()
if PLEX_LABEL_MODE not in ("allow","exclude"):
    raise ValueError("PLEX_LABEL_MODE must be 'allow' or 'exclude'")
REFRESH=max(5,int(os.getenv("REFRESH_MINUTES","30")))*60
TABLO_URL=os.getenv("TABLO_URL","").rstrip("/")
TABLO_REFRESH=max(1,int(os.getenv("TABLO_REFRESH_HOURS","6")))*3600
PHILO_EPG_URL="https://raw.githubusercontent.com/acidjesuz/EPGTalk/master/US_guide.xml.gz"
PHILO_REFRESH=max(6,int(os.getenv("PHILO_REFRESH_HOURS","24")))*3600
app=FastAPI(title="Universal TV Guide",version="0.1.0")
from app.philo_links import links_for as philo_handoff_links, channel_catalog as known_philo_channels
from app.epgshare_supplement import fetch_missing as fetch_supplemental_epg
from app.playback import router as playback_router
app.include_router(playback_router)
from app.tablo_live import router as tablo_live_router
app.include_router(tablo_live_router)
sync_lock=threading.Lock()
tablo_lock=threading.Lock()
philo_lock=threading.Lock()
errors={"plex":"","guide":"","tablo":"","philo":""}

@contextmanager
def db():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()

def initialize():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript("""CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,kind TEXT,title TEXT,year INT,normalized TEXT,show_title TEXT,season INT,episode INT,guids TEXT);
        CREATE INDEX IF NOT EXISTS idx_media_norm ON media(normalized);
        CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id TEXT,channel_label TEXT,start TEXT,stop TEXT,title TEXT,subtitle TEXT,year INT,description TEXT);
        CREATE INDEX IF NOT EXISTS idx_guide_time ON listings(start,stop);
        CREATE TABLE IF NOT EXISTS tablo_listings(id TEXT PRIMARY KEY,channel_id TEXT,channel_label TEXT,start TEXT,stop TEXT,title TEXT,subtitle TEXT,year INT,description TEXT,season INT,episode INT);\n        CREATE INDEX IF NOT EXISTS idx_tablo_time ON tablo_listings(start,stop);\n        CREATE TABLE IF NOT EXISTS philo_listings(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id TEXT,channel_label TEXT,start TEXT,stop TEXT,title TEXT,subtitle TEXT,year INT,description TEXT);\n        CREATE INDEX IF NOT EXISTS idx_philo_time ON philo_listings(start,stop);\n        CREATE TABLE IF NOT EXISTS channel_prefs(source TEXT,channel_id TEXT,favorite INTEGER NOT NULL DEFAULT 0,hidden INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(source,channel_id));\n        CREATE TABLE IF NOT EXISTS tablo_channels(channel_id TEXT PRIMARY KEY,number TEXT,network TEXT,callsign TEXT);\n        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);""")
        # Existing installations retain their databases; add classification safely.
        for table in ("listings","tablo_listings","philo_listings"):
            cols={x["name"] for x in c.execute("PRAGMA table_info("+table+")")}
            if "program_kind" not in cols:
                c.execute("ALTER TABLE "+table+" ADD COLUMN program_kind TEXT DEFAULT 'unknown'")
            if table in ("listings","philo_listings"):
                for key in ("season","episode"):
                    if key not in cols:
                        c.execute("ALTER TABLE "+table+" ADD COLUMN "+key+" INTEGER")
initialize()

def enforce_plex_label_policy():
    """Do not serve old unfiltered results after configuring a label."""
    policy=PLEX_LABEL_MODE+":"+PLEX_ALLOWED_LABEL.casefold() if PLEX_ALLOWED_LABEL else ""
    with db() as c:
        previous=c.execute("SELECT value FROM meta WHERE key='plex_label_policy'").fetchone()
        old=previous["value"] if previous else ""
        if old!=policy:
            c.execute("DELETE FROM media")
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('plex_label_policy',?)",
                      (policy,))
            c.execute("DELETE FROM meta WHERE key='last_plex_sync'")

enforce_plex_label_policy()

def metadata(key,default=""):
    with db() as c:
        row=c.execute("SELECT value FROM meta WHERE key=?",(key,)).fetchone()
        return row["value"] if row else default

def putmeta(c,k,v):
    c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",(k,str(v)))

def norm(t):
    return re.sub(r"[^a-z0-9]+"," ",(t or "").casefold()).strip()

def plex_xml(path,params=None):
    if not PLEX_URL or not PLEX_TOKEN or PLEX_TOKEN.startswith("REPLACE_"):
        raise RuntimeError("Configure PLEX_URL and PLEX_TOKEN in Unraid container settings")
    try:
        r=requests.get(PLEX_URL+path,headers={"X-Plex-Token":PLEX_TOKEN,"Accept":"application/xml"},params=params,timeout=30)
        r.raise_for_status()
        return ET.fromstring(r.content)
    except (requests.RequestException,ET.ParseError) as e:
        raise RuntimeError("Plex request failed: "+str(e)[:180]) from e

def pages(path):
    start=0
    while True:
        root=plex_xml(path,{"X-Plex-Container-Start":start,"X-Plex-Container-Size":250,"includeGuids":1})
        items=[n for n in root if n.tag in ("Directory","Video")]
        yield from items
        total=int(root.get("totalSize",root.get("size",len(items))))
        if not items or start+len(items)>=total:break
        start+=len(items)

def plex_item_labels(node):
    """Read the Label elements, not genres or collections."""
    return {label.get("tag","").strip().casefold()
            for label in node.findall("Label") if label.get("tag")}


def plex_item_has_allowed_label(node):
    """Apply Plex-style allow/exclude rules to full movie or show metadata.

    A listing may omit labels, so absence in the listing is not proof of
    absence in Plex. Metadata fetch failures abort the sync rather than
    silently granting an excluded item access.
    """
    if not PLEX_ALLOWED_LABEL:
        return True
    key=node.get("ratingKey")
    if not key or not re.fullmatch(r"\d+",key):
        return False
    wanted=PLEX_ALLOWED_LABEL.casefold()
    labels=plex_item_labels(node)
    if wanted in labels:
        return PLEX_LABEL_MODE!="exclude"
    detail=plex_xml("/library/metadata/"+key)
    for item in detail:
        if item.tag in ("Directory","Video") and item.get("ratingKey")==key:
            labeled=wanted in plex_item_labels(item)
            return labeled if PLEX_LABEL_MODE=="allow" else not labeled
    # Unknown metadata is never considered safe to index under either mode.
    return False


def sync_plex():
    if not sync_lock.acquire(blocking=False):
        raise RuntimeError("Plex refresh already in progress")
    try:
        rows=[]
        libraries=0
        for sec in plex_xml("/library/sections").findall("Directory"):
            kind=sec.get("type")
            if kind not in ("movie","show"):continue
            libraries+=1
            for obj in pages("/library/sections/"+sec.get("key")+"/all"):
                if kind=="movie" and obj.get("type")=="movie":
                    if not plex_item_has_allowed_label(obj):continue
                    title=obj.get("title","")
                    rows.append(("movie:"+str(obj.get("ratingKey")),"movie",title,int(obj.get("year","0") or 0),norm(title),"",None,None,",".join(x.get("id","") for x in obj.findall("Guid"))))
                elif kind=="show" and obj.get("type")=="show" and obj.get("ratingKey"):
                    # A show-level label includes its episodes in the guide.
                    if not plex_item_has_allowed_label(obj):continue
                    show=obj.get("title","")
                    for ep in pages("/library/metadata/"+obj.get("ratingKey")+"/allLeaves"):
                        if ep.get("type")!="episode":continue
                        rows.append(("episode:"+str(ep.get("ratingKey")),"episode",ep.get("title",""),int(ep.get("year","0") or 0),norm(show),show,int(ep.get("parentIndex","0") or 0),int(ep.get("index","0") or 0),",".join(x.get("id","") for x in ep.findall("Guid"))))
        with db() as c:
            c.execute("DELETE FROM media")
            c.executemany("INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?)",rows)
            putmeta(c,"last_plex_sync",datetime.now(timezone.utc).isoformat())
            putmeta(c,"plex_sections",libraries)
        errors["plex"]=""
        return {"media_count":len(rows),"libraries":libraries,"plex_label_filter_enabled":bool(PLEX_ALLOWED_LABEL),"plex_label_mode":PLEX_LABEL_MODE if PLEX_ALLOWED_LABEL else "off"}
    except Exception as e:
        errors["plex"]=str(e)
        raise
    finally:
        sync_lock.release()

def xml_date(raw):
    try:
        raw=(raw or "").strip()
        text,_,offset=raw.partition(" ")
        dt=datetime.strptime(text[:14],"%Y%m%d%H%M%S")
        if offset:
            sign=1 if offset[0]=="+" else -1
            zone=timezone(sign*timedelta(hours=int(offset[1:3]),minutes=int(offset[3:5])))
        else:zone=timezone.utc
        return dt.replace(tzinfo=zone).astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,IndexError):return None

def xmltv_kind(program):
    categories=[norm(n.text) for n in program.findall("category") if n.text]
    if any(c in ("movie","movies","film","films","feature film") for c in categories):
        return "movie"
    if any(c in ("series","tv series","tv show","episode") for c in categories):
        return "series"
    return "unknown"

def xmltv_episode(program):
    """Parse explicit XMLTV episode IDs; XMLTV_NS numbers are zero-based."""
    for item in program.findall("episode-num"):
        raw=(item.text or "").strip()
        system=(item.get("system") or "").lower().strip()
        if system=="xmltv_ns":
            m=re.fullmatch(r"(\d+)\.(\d+)\.(?:\d+)?(?:/\d+)?",raw)
            if m:
                return int(m.group(1))+1,int(m.group(2))+1
        elif system in ("onscreen","common"):
            m=re.fullmatch(r"[Ss]?(\d+)\s*[EeXx]\s*(\d+)",raw)
            if m:
                return int(m.group(1)),int(m.group(2))
    return None,None

def import_guide():
    if not GUIDE.exists():
        return {"imported":0,"note":"Supply guide.xml in the appdata folder"}
    try:
        names={}
        rows=[]
        for event,node in ET.iterparse(GUIDE,events=("end",)):
            if node.tag=="channel":
                labels=[v.text for v in node.findall("display-name") if v.text]
                names[node.get("id")]=labels[0] if labels else node.get("id")
            elif node.tag=="programme":
                a=xml_date(node.get("start"));b=xml_date(node.get("stop"))
                if a and b:
                    get=lambda tag:(node.findtext(tag) or "").strip()
                    year_text=get("date")
                    year=int(year_text[:4]) if re.match(r"^\d{4}",year_text) else 0
                    rows.append((node.get("channel"),a,b,get("title"),get("sub-title"),year,get("desc")[:600],xmltv_kind(node),*xmltv_episode(node)))
            if node.tag in ("channel","programme"):node.clear()
        with db() as c:
            c.execute("DELETE FROM listings")
            c.executemany("INSERT INTO listings(channel_id,channel_label,start,stop,title,subtitle,year,description,program_kind,season,episode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",[(ch,names.get(ch,ch),a,b,title,sub,year,desc,kind,season,episode) for ch,a,b,title,sub,year,desc,kind,season,episode in rows])
            putmeta(c,"last_guide_sync",datetime.now(timezone.utc).isoformat())
            putmeta(c,"guide_mtime",GUIDE.stat().st_mtime)
        errors["guide"]=""
        return {"imported":len(rows),"channels":len(names)}
    except Exception as e:
        errors["guide"]=str(e)
        raise



# Published Philo Essential live-channel lineup, checked November 2026. The EPG feed
# is third party, not supplied by or affiliated with Philo.
PHILO_NAMES="""A&E|AccuWeather Network|AMC|American Heroes Channel|Animal Planet|aspireTV|AXS TV|BBC America|BET|BET Her|Catchy Comedy|CLEO TV|CMT|Comedy Central|Cooking Channel|Crime + Investigation|Dabl|Destination America|Discovery Channel|Discovery Family|Discovery Life|Discovery Turbo|EarthX|FETV|Food Network|FYI|Game Show Network|Great|Great American Faith & Living|Great American Family|Hallmark Channel|Hallmark Family|Hallmark Mystery|Heroes & Icons|HGTV|HISTORY|HSN|IFC|INSP|Investigation Discovery|Law&Crime|Lifetime|LMN|Logo|Magnolia Network|MeTV|MeTV Toons|MeTV+|Military History Channel|MTV|MTV Classic|MTV Live|MTV2|Nick Jr.|Nickelodeon|Nicktoons|Oprah Winfrey Network|Paramount Network|Pop TV|QVC|Science Channel|Smithsonian Channel|Start TV|Story Television|Sundance TV|Tastemade|TeenNick|TLC|Travel Channel|TV Land|TV One|UPtv|VH1|Vice|We TV|WEST""".split("|")
PHILO_ALIASES={"a e":"A&E","history":"HISTORY","id":"Investigation Discovery","investigation discovery":"Investigation Discovery","own":"Oprah Winfrey Network","gac family":"Great American Family","gaf":"Great American Family","hallmark":"Hallmark Channel","hallmark movies mysteries":"Hallmark Mystery","hallmark drama":"Hallmark Family","lmn":"LMN","lifetime movie network":"LMN","game show network":"Game Show Network","gsn":"Game Show Network","crime investigation":"Crime + Investigation","sundance":"Sundance TV","we":"We TV","wetv":"We TV","up":"UPtv","up tv":"UPtv","pop":"Pop TV","smithsonian":"Smithsonian Channel","h i":"Heroes & Icons","bbc america":"BBC America","mtv 2":"MTV2","nick junior":"Nick Jr."}
def philo_name(label):
    normalized=norm(label)
    canonical={norm(n):n for n in PHILO_NAMES}
    if normalized in canonical:return canonical[normalized]
    return PHILO_ALIASES.get(normalized)

def sync_philo():
    if not philo_lock.acquire(blocking=False):
        raise RuntimeError("Philo EPG refresh already in progress")
    try:
        response=requests.get(PHILO_EPG_URL,timeout=(10,100),stream=True)
        response.raise_for_status()
        # Maximum 30 MB compressed / 180 MB XML; fixed trusted feed URL only.
        compressed=bytearray()
        for chunk in response.iter_content(chunk_size=1024*256):
            compressed.extend(chunk)
            if len(compressed)>30*1024*1024:raise RuntimeError("EPG download exceeded size limit")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gz:
            xml=gz.read(180*1024*1024+1)
        if len(xml)>180*1024*1024:raise RuntimeError("Uncompressed EPG exceeded size limit")
        # One guide row per actual network: the free feed can contain multiple
        # station IDs (time-shifted feeds and duplicate sources) for AMC, etc.
        channel_names={}
        root=ET.fromstring(xml)
        # Source-level evidence to distinguish missing HGTV data from
        # unmatched feed channel labels. No schedule is invented.
        hgtv_candidates={}
        for channel in root.findall("channel"):
            labels=[x.text.strip() for x in channel.findall("display-name") if x.text]
            feed_id=channel.get("id")
            matches={philo_name(v) for v in labels}
            matches.discard(None)
            if len(matches)==1:
                channel_names[feed_id]=next(iter(matches))
            if any(re.search(r"\\bhgtv\\b|home\\s*(?:&|and|n)\\s*garden",
                             v,re.I) for v in labels+[str(feed_id or "")]):
                if len(hgtv_candidates)<25:
                    hgtv_candidates[feed_id]={"id":str(feed_id or "")[:100],
                        "labels":labels[:6],"matched_as":channel_names.get(feed_id),
                        "future_programs_in_source":0}
        now=datetime.now(timezone.utc)
        by_feed={}
        for p in root.findall("programme"):
            feed_id=p.get("channel")
            if feed_id not in channel_names and feed_id not in hgtv_candidates:continue
            start=xml_date(p.get("start"));stop=xml_date(p.get("stop"))
            if not start or not stop:continue
            if feed_id in hgtv_candidates and stop>now.isoformat() and start<=(now+timedelta(days=8)).isoformat():
                hgtv_candidates[feed_id]["future_programs_in_source"]+=1
            if feed_id not in channel_names:continue
            if stop<now.isoformat() or start>(now+timedelta(days=8)).isoformat():continue
            value=lambda key:(p.findtext(key) or "").strip()
            date=value("date")
            year=int(date[:4]) if re.match(r"^\\d{4}",date) else 0
            by_feed.setdefault(feed_id,[]).append((
                channel_names[feed_id],start,stop,value("title"),
                value("sub-title"),year,value("desc")[:600],xmltv_kind(p),*xmltv_episode(p)
            ))
        # Prefer the feed variant with the most usable schedule coverage.
        # Channel IDs are stable canonical names, not unstable feed-specific IDs.
        winners={}
        for feed_id,entries in by_feed.items():
            label=channel_names[feed_id]
            score=(sum(max(0,(datetime.fromisoformat(row[2])-max(now,datetime.fromisoformat(row[1]))).total_seconds()) for row in entries),len(entries))
            if label not in winners or score>winners[label][0]:
                winners[label]=(score,feed_id,entries)
        if not winners:
            raise RuntimeError("No matching Philo lineup schedules found in EPG; preserving existing guide")
        # Fill gaps for all published Philo station names with an explicit
        # US/Eastern US2 ID. The primary EPG remains authoritative. This
        # supplemental EPG does not discover authenticated Philo player IDs.
        extra,supplement_status=fetch_supplemental_epg(
            set(winners),set(PHILO_NAMES),xml_date,xmltv_kind,xmltv_episode)
        for name,entries in extra.items():
            winners[name]=((0,len(entries)),"us2-supplement",entries)
        hgtv_status={"source":"EPGShare US2","programs":len(extra.get("HGTV",[])),
                     "error":supplement_status.get("error",""),
                     "selected_channel_id":"Home.and.Garden.Television.HD.us2" if "HGTV" in extra else None,
                     "matched_channels":1 if "HGTV" in extra else 0}
        rows=[]
        for label,(_,feed_id,entries) in winners.items():
            key="philo:"+norm(label)
            rows.extend((key,label,*row[1:]) for row in entries)
        with db() as c:
            # Migrate prior favorites/hide flags to the new canonical channel IDs.
            for old in c.execute("SELECT DISTINCT channel_id,channel_label FROM philo_listings").fetchall():
                key="philo:"+norm(old["channel_label"])
                pref=c.execute("SELECT favorite,hidden FROM channel_prefs WHERE source='philo' AND channel_id=?",(old["channel_id"],)).fetchone()
                if pref:
                    c.execute("""INSERT INTO channel_prefs(source,channel_id,favorite,hidden)
                       VALUES ('philo',?,?,?) ON CONFLICT(source,channel_id) DO UPDATE SET
                       favorite=MAX(channel_prefs.favorite,excluded.favorite),
                       hidden=MAX(channel_prefs.hidden,excluded.hidden)""",(key,pref["favorite"],pref["hidden"]))
            c.execute("DELETE FROM philo_listings")
            c.executemany("""INSERT INTO philo_listings
              (channel_id,channel_label,start,stop,title,subtitle,year,description,program_kind,season,episode)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",rows)
            putmeta(c,"last_philo_sync",datetime.now(timezone.utc).isoformat())
            putmeta(c,"philo_channel_count",len(winners))
            putmeta(c,"hgtv_epg_candidates",json.dumps(list(hgtv_candidates.values())))
            putmeta(c,"hgtv_fallback_status",json.dumps(hgtv_status))
            putmeta(c,"supplemental_epg_status",json.dumps(supplement_status))
            putmeta(c,"primary_epg_channels",json.dumps(sorted(set(winners)-set(extra))))
            putmeta(c,"supplemental_epg_channels",json.dumps(sorted(extra)))
        errors["philo"]=""
        return {"programs":len(rows),"matched_channels":len(winners),"duplicate_variants_removed":max(0,len(by_feed)-(len(winners)-len(extra))),"published_philo_channels":len(PHILO_NAMES)}
    except Exception as e:
        errors["philo"]=str(e)
        raise
    finally:
        philo_lock.release()

def tablo_get(path):
    """Read-only queries to a trusted local legacy Tablo host."""
    parsed=urlparse(TABLO_URL)
    if parsed.scheme!="http" or not parsed.hostname:
        raise RuntimeError("Set TABLO_URL to http://YOUR-TABLO-IP:8885")
    if not path.startswith("/guide/") or "//" in path or ".." in path:
        raise ValueError("Unsupported Tablo path")
    response=requests.get(TABLO_URL+path,timeout=(3,12),headers={"Accept":"application/json"})
    response.raise_for_status()
    return response.json()

def fetch_tablo_airing(path):
    try:
        data=tablo_get(path)
        details=data.get("airing_details") or {}
        raw=details.get("datetime")
        duration=int(details.get("duration") or 0)
        if not raw or duration<=0:return None
        start=datetime.fromisoformat(raw.replace("Z","+00:00")).astimezone(timezone.utc)
        stop=start+timedelta(seconds=duration)
        if stop<datetime.now(timezone.utc)-timedelta(hours=1):return None
        channel=(details.get("channel") or {}).get("channel") or {}
        channel_path=details.get("channel_path") or ""
        label=str(channel.get("major") or "")+"."+str(channel.get("minor") or "")
        if not channel.get("major"):label=channel.get("call_sign") or channel_path
        title=details.get("show_title") or (data.get("event") or {}).get("title") or ""
        episode=data.get("episode") or {}
        movie=data.get("movie") or {}
        subtitle=episode.get("title") or ""
        desc=episode.get("description") or movie.get("description") or (data.get("event") or {}).get("description") or ""
        year=movie.get("release_year") or movie.get("year") or 0
        season=episode.get("season_number")
        number=episode.get("number")
        kind="movie" if "/movies/" in path or data.get("movie") else ("series" if "/series/" in path else "other")
        return (path,channel_path,label,start.isoformat(),stop.isoformat(),title,subtitle,int(year or 0),str(desc)[:600],season,number,kind,channel.get("network") or "",channel.get("call_sign") or "")
    except (requests.RequestException,ValueError,TypeError,KeyError):
        return None

def sync_tablo():
    if not TABLO_URL:raise RuntimeError("Configure TABLO_URL first")
    if not tablo_lock.acquire(blocking=False):
        raise RuntimeError("Tablo refresh already in progress")
    try:
        paths=tablo_get("/guide/airings")
        if not isinstance(paths,list):raise RuntimeError("Unexpected Tablo airings response")
        paths=[p for p in paths if isinstance(p,str) and p.startswith("/guide/")]
        if not paths:raise RuntimeError("Tablo returned no program references; preserving previous schedule")
        # Bound requests so a malformed or unexpectedly huge response cannot overload the device.
        max_items=4000
        truncated=len(paths)>max_items
        paths=paths[:max_items]
        rows=[]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures=[pool.submit(fetch_tablo_airing,p) for p in paths]
            for f in as_completed(futures):
                row=f.result()
                if row:rows.append(row)
        if not rows:raise RuntimeError("No usable airing details returned; preserving previous schedule")
        # Airing records embed station network names and callsigns.
        # Prefer them even when separate channel lookups fail.
        channel_by_id={}
        for row in rows:
            channel_id,number,network,callsign=row[1],row[2],row[12],row[13]
            previous=channel_by_id.get(channel_id)
            if previous is None or (not previous[2] and network):
                channel_by_id[channel_id]=(channel_id,number,str(network),str(callsign))
        # Supplement from the channel endpoint if available.
        try:
            channel_paths=tablo_get("/guide/channels")
            if isinstance(channel_paths,list):
                for channel_path in channel_paths:
                    if not isinstance(channel_path,str) or not channel_path.startswith("/guide/channels/"):
                        continue
                    try:
                        details=(tablo_get(channel_path).get("channel") or {})
                        major,minor=details.get("major"),details.get("minor")
                        number=f"{major}.{minor}" if major is not None and minor is not None else ""
                        network=str(details.get("network") or "")
                        callsign=str(details.get("call_sign") or "")
                        prev=channel_by_id.get(channel_path)
                        if prev is None or (not prev[2] and network):
                            channel_by_id[channel_path]=(channel_path,number,network,callsign)
                    except (requests.RequestException,ValueError,TypeError):
                        continue
        except (requests.RequestException,ValueError,TypeError):
            pass
        channel_info=list(channel_by_id.values())
        with db() as c:
            if channel_info:
                c.executemany("INSERT OR REPLACE INTO tablo_channels(channel_id,number,network,callsign) VALUES (?,?,?,?)",channel_info)
            c.execute("DELETE FROM tablo_listings")
            c.executemany("INSERT OR REPLACE INTO tablo_listings(id,channel_id,channel_label,start,stop,title,subtitle,year,description,season,episode,program_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",[row[:12] for row in rows])
            putmeta(c,"last_tablo_sync",datetime.now(timezone.utc).isoformat())
            putmeta(c,"tablo_truncated",int(truncated))
            putmeta(c,"tablo_requested",len(paths))
        errors["tablo"]=""
        return {"imported":len(rows),"requested":len(paths),"truncated":truncated}
    except Exception as e:
        errors["tablo"]=str(e)
        raise
    finally:
        tablo_lock.release()

def fill_tablo_schedule_gaps(programs,channel_id,channel_label,start,stop):
    """Show a tuneable no-EPG block for every uncovered time interval.

    Do not replace legitimate Tablo listings; trim placeholders around them.
    """
    if start>=stop:return programs
    ordered=sorted(programs,key=lambda item:(item["start"],item["stop"]))
    filled=[]
    cursor=start
    for item in ordered:
        a=max(start,str(item["start"]))
        b=min(stop,str(item["stop"]))
        if a>cursor:
            filled.append({"channel_id":channel_id,"channel_label":channel_label,
                "start":cursor,"stop":a,"title":"No guide information",
                "subtitle":"","description":"Program listings are unavailable. Select Watch Live to tune this channel.",
                "program_kind":"other","season":None,"episode":None,
                "plex":None,"no_guide_data":True})
        filled.append(item)
        if b>cursor:cursor=b
    if cursor<stop:
        filled.append({"channel_id":channel_id,"channel_label":channel_label,
            "start":cursor,"stop":stop,"title":"No guide information",
            "subtitle":"","description":"Program listings are unavailable. Select Watch Live to tune this channel.",
            "program_kind":"other","season":None,"episode":None,
            "plex":None,"no_guide_data":True})
    return filled


def series_title_matches(guide_title,plex_title):
    """Only use unambiguous identifiers for known US title variants."""
    guide=norm(guide_title)
    stored=norm(plex_title)
    if guide==stored:
        return True
    # Comedy Central broadcasts the US series. Never match the UK series
    # merely because both are called The Office.
    if guide=="the office" and stored in (
        "the office us","the office u s","the office 2005",
        "the office us 2005","the office u s 2005"
    ):
        return True
    return False


def episode_title_alias(value):
    """Conservative secondary title key: ignore only an initial 'The'."""
    title=norm(value)
    return title[4:] if title.startswith("the ") else title


def numbered_episode_part(value):
    """Recognize Plex's terminal '(1)', '(2)', '(3)' part suffixes only."""
    raw=str(value or "").strip()
    found=re.search(r"\s+\(\s*([123])\s*\)\s*$",raw)
    if not found:return None
    base=raw[:found.start()].strip()
    return (episode_title_alias(base),int(found.group(1))) if base else None


def episode_payload(m):
    return {"kind":"episode","title":m["title"],"show":m["show_title"],
            "rating_key":m["id"].split(":",1)[1]}


def matched_numbered_parts(series,subtitle,index):
    """Offer distinct Plex episodes when guide omits the part number.

    Require a unique consecutively numbered group beginning with (1).
    Never silently choose one part when the feed lacks the discriminator.
    """
    key=episode_title_alias(subtitle)
    if not key:return None
    candidates=index["part_title"].get((series,key),[])
    distinct={part:m for part,m in candidates}
    if len(distinct)!=len(candidates) or len(distinct) not in (2,3):
        return None
    if set(distinct)!=set(range(1,len(distinct)+1)):
        return None
    # Distinct content IDs are required for the separate-parts UI.
    items=[distinct[part] for part in sorted(distinct)]
    if len({m["id"] for m in items})!=len(items):return None
    payload=[episode_payload(m) for m in items]
    return {"kind":"episode","title":" / ".join(m["title"] for m in items),
            "show":items[0]["show_title"],
            "rating_key":payload[0]["rating_key"],"parts":payload}


def build_media_index(catalog):
    """Build hash indexes once per guide request instead of scanning every Plex
    episode for every guide program (the previous O(programs * media) delay)."""
    movies={}
    episodes_by_number={}
    episodes_by_title={}
    episodes_by_alias={}
    parts_by_title={}
    series_counts={}
    for m in catalog:
        if m["kind"]=="movie":
            movies.setdefault(m["normalized"],[]).append(m)
        elif m["kind"]=="episode":
            show=norm(m["show_title"])
            series_counts[show]=series_counts.get(show,0)+1
            keys=[show]
            if show in ("the office us","the office u s","the office 2005",
                        "the office us 2005","the office u s 2005"):
                keys.append("the office")
            for key in keys:
                episodes_by_number.setdefault((key,m["season"],m["episode"]),[]).append(m)
                episodes_by_title.setdefault((key,norm(m["title"])),[]).append(m)
                episodes_by_alias.setdefault((key,episode_title_alias(m["title"])),[]).append(m)
                part=numbered_episode_part(m["title"])
                if part:
                    parts_by_title.setdefault((key,part[0]),[]).append((part[1],m))
    return {"movies":movies,"number":episodes_by_number,"title":episodes_by_title,
            "alias_title":episodes_by_alias,"part_title":parts_by_title,
            "series_counts":series_counts}


def plex_series_candidates(guide_title,catalog):
    """For diagnostics only; avoid scanning the catalog in guide requests."""
    return [m for m in catalog if m["kind"]=="episode"
            and series_title_matches(guide_title,m["show_title"])]


def match_broadcast_segments(series,subtitle,index):
    """Match two/three explicitly named TV segments to separate Plex episodes.

    Broadcasters often air several short cartoons in one half-hour EPG slot,
    while Plex inventories each independently. Never invent a sequential
    episode or match against another series; each segment title must uniquely
    identify a Plex episode in the named series.
    """
    raw=str(subtitle or "").strip()
    # Only clearly separated segment names; avoid splitting ordinary titles
    # containing slashes without spaces.
    separator=r"(?:\s+[/&+]\s+|;\s+)"
    if not re.search(separator,raw):
        return None
    names=[p.strip() for p in re.split(separator,raw)]
    if not 2<=len(names)<=3 or any(len(n)<3 for n in names):
        return None
    parts=[]
    for name in names:
        matches=index["title"].get((series,norm(name)),[])
        if len(matches)!=1:
            # Partial, misleading matches do not look like a full broadcast.
            return None
        item=matches[0]
        key=item["id"].split(":",1)[1]
        if any(p["rating_key"]==key for p in parts):
            return None
        parts.append({"kind":"episode","title":item["title"],"show":item["show_title"],
                      "rating_key":key})
    return {"kind":"episode","title":" / ".join(p["title"] for p in parts),
            "show":parts[0]["show"],"rating_key":parts[0]["rating_key"],
            "parts":parts}


def match(p,index):
    """Only match Plex movies with movies, and exact episodes with episodes."""
    title=norm(p["title"])
    if not title:return None
    kind=p.get("program_kind","unknown")
    year=int(p.get("year") or 0)
    if kind=="movie":
        candidates=index["movies"].get(title,[])
        if year:
            candidates=[m for m in candidates if m["year"] and abs(m["year"]-year)<=1]
        if len(candidates)==1:
            m=candidates[0]
            return {"kind":"movie","title":m["title"],"rating_key":m["id"].split(":",1)[1]}
        return None
    if kind in ("other","movie"):
        return None
    season=p.get("season")
    number=p.get("episode")
    subtitle=norm(p.get("subtitle"))
    if season is not None and number is not None:
        by_number=index["number"].get((title,season,number),[])
        if len(by_number)==1 and (not subtitle or
                norm(by_number[0]["title"])==subtitle or
                episode_title_alias(by_number[0]["title"])==episode_title_alias(p.get("subtitle")) or
                (numbered_episode_part(by_number[0]["title"]) is not None and
                 numbered_episode_part(by_number[0]["title"])[0]==episode_title_alias(p.get("subtitle")))):
            m=by_number[0]
            return {"kind":"episode","title":m["title"],"show":m["show_title"],
                    "rating_key":m["id"].split(":",1)[1]}
    if subtitle:
        by_title=index["title"].get((title,subtitle),[])
        if len(by_title)==1:
            m=by_title[0]
            return {"kind":"episode","title":m["title"],"show":m["show_title"],
                    "rating_key":m["id"].split(":",1)[1]}
        multi=match_broadcast_segments(title,p.get("subtitle"),index)
        if multi:return multi
        # Exact title matching takes priority, then safe variants.
        # Never choose arbitrarily between two Plex part-numbered episodes.
        alias=index["alias_title"].get((title,episode_title_alias(p.get("subtitle"))),[])
        if len(alias)==1 and not numbered_episode_part(alias[0]["title"]):
            return episode_payload(alias[0])
        parts=matched_numbered_parts(title,p.get("subtitle"),index)
        if parts:return parts
    return None

def assign_scheduled_episode_parts(programs):
    """Resolve two-part Plex episodes by adjacent EPG airing order.

    Do not assign a part from a single isolated listing or a repeated airing
    with an intervening program. The fallback multiple-parts selector remains.
    """
    for i in range(len(programs)-1):
        first,second=programs[i],programs[i+1]
        a,b=first.get("plex"),second.get("plex")
        if not a or not b or len(a.get("parts",[]))!=2 or len(b.get("parts",[]))!=2:
            continue
        if ([p["rating_key"] for p in a["parts"]]!=
            [p["rating_key"] for p in b["parts"]]):
            continue
        if (norm(first.get("title"))!=norm(second.get("title")) or
            episode_title_alias(first.get("subtitle"))!=episode_title_alias(second.get("subtitle"))):
            continue
        try:
            end=datetime.fromisoformat(str(first["stop"]).replace("Z","+00:00"))
            start=datetime.fromisoformat(str(second["start"]).replace("Z","+00:00"))
            if end.tzinfo is None or start.tzinfo is None:continue
            gap=(start-end).total_seconds()
        except (ValueError,TypeError,KeyError):
            continue
        # Genuine two-parters generally occupy consecutive scheduled slots.
        if not 0<=gap<=120:continue
        # Each source has been sorted by channel then start before grouping.
        for row,part in ((first,a["parts"][0]),(second,b["parts"][1])):
            row["plex"]=dict(part)
            row["plex"]["scheduled_part"]=1 if row is first else 2


@app.get("/static/manifest.webmanifest")
def iphone_manifest():
    return FileResponse(Path(__file__).parent/"static"/"manifest.webmanifest",
                        media_type="application/manifest+json")

@app.get("/")
def home():
    return FileResponse(Path(__file__).parent/"static"/"index.html")

@app.get("/api/status")
def status():
    with db() as c:
        media=c.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        listings=c.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        tablo_count=c.execute("SELECT COUNT(*) FROM tablo_listings").fetchone()[0]
        philo_count=c.execute("SELECT COUNT(*) FROM philo_listings").fetchone()[0]
    return {"plex_configured":bool(PLEX_URL and PLEX_TOKEN and not PLEX_TOKEN.startswith("REPLACE_")),"plex_label_filter_enabled":bool(PLEX_ALLOWED_LABEL),"plex_label_mode":PLEX_LABEL_MODE if PLEX_ALLOWED_LABEL else "off","media_count":media,"listings_count":listings,"plex_sync":metadata("last_plex_sync"),"guide_sync":metadata("last_guide_sync"),"plex_error":errors["plex"],"guide_error":errors["guide"],"xmltv_file_present":GUIDE.exists(),"tablo_configured":bool(TABLO_URL),"tablo_count":tablo_count,"tablo_sync":metadata("last_tablo_sync"),"tablo_error":errors["tablo"],"tablo_truncated":metadata("tablo_truncated","0")=="1","philo_count":philo_count,"philo_channels":int(metadata("philo_channel_count","0")),"philo_sync":metadata("last_philo_sync"),"philo_error":errors["philo"]}

@app.post("/api/sync/plex")
def refresh_plex():
    try:return sync_plex()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.post("/api/sync/guide")
def refresh_guide():
    try:return import_guide()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.post("/api/sync/philo")
def refresh_philo():
    try:return sync_philo()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.get("/api/channels")
def channels():
    with db() as c:
        items=c.execute("""SELECT 'tablo' source,t.channel_id,
               COALESCE(NULLIF(t.number,''),NULLIF(t.network,''),NULLIF(t.callsign,''),t.channel_id) channel_label,
               (SELECT COUNT(*) FROM tablo_listings l WHERE l.channel_id=t.channel_id) programs
               FROM tablo_channels t
           UNION ALL SELECT 'philo',channel_id,channel_label,COUNT(*) FROM philo_listings GROUP BY channel_id
           UNION ALL SELECT 'xmltv',channel_id,channel_label,COUNT(*) FROM listings GROUP BY channel_id""").fetchall()
        preferences={(x["source"],x["channel_id"]):dict(x) for x in c.execute("SELECT * FROM channel_prefs")}
    rows=[]
    for item in items:
        entry=dict(item)
        entry.update({k:bool(v) for k,v in preferences.get((entry["source"],entry["channel_id"]),{}).items() if k in ("favorite","hidden")})
        entry.setdefault("favorite",False);entry.setdefault("hidden",False)
        rows.append(entry)
    return sorted(rows,key=lambda item:(item["source"],item["channel_label"]))

@app.put("/api/channels/{source}/{channel_id}")
def set_channel(source:str,channel_id:str,favorite:bool=False,hidden:bool=False):
    if source not in ("tablo","philo","xmltv") or not channel_id or len(channel_id)>300:
        raise HTTPException(400,"Invalid channel")
    with db() as c:
        c.execute("INSERT OR REPLACE INTO channel_prefs(source,channel_id,favorite,hidden) VALUES (?,?,?,?)",(source,channel_id,int(favorite),int(hidden)))
    return {"source":source,"channel_id":channel_id,"favorite":favorite,"hidden":hidden}

@app.post("/api/sync/tablo")
def refresh_tablo():
    try:return sync_tablo()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.get("/api/guide/art/{rating_key}")
def guide_plex_art(rating_key:int):
    """Optional guide artwork for indexed Plex titles only, without browser tokens."""
    if rating_key<=0:raise HTTPException(404,"No Plex artwork")
    with db() as connection:
        item=connection.execute("SELECT id FROM media WHERE id IN (?,?)",
            ("movie:"+str(rating_key),"episode:"+str(rating_key))).fetchone()
    if not item:raise HTTPException(404,"Not in Plex library index")
    try:
        root=plex_xml("/library/metadata/"+str(rating_key))
        video=root.find("Video")
        if video is None:raise HTTPException(404,"No Plex image")
        image_path=video.get("art") or video.get("thumb") or video.get("grandparentThumb")
        if not image_path or not re.fullmatch(
            r"/library/metadata/\d+/(?:art|thumb)/\d+",image_path):
            raise HTTPException(404,"Plex artwork unavailable")
        image=requests.get(PLEX_URL+image_path,
            headers={"X-Plex-Token":PLEX_TOKEN,"Accept":"image/jpeg,image/png"},
            timeout=(5,15),allow_redirects=False)
        try:
            image.raise_for_status()
            content_type=image.headers.get("Content-Type","").split(";",1)[0]
            if content_type not in ("image/jpeg","image/png") or len(image.content)>2_000_000:
                raise HTTPException(502,"Invalid artwork response")
            from fastapi.responses import Response
            return Response(content=image.content,media_type=content_type,
                headers={"Cache-Control":"private, max-age=300"})
        finally:image.close()
    except requests.RequestException:
        raise HTTPException(502,"Plex artwork unavailable")
    except RuntimeError:
        raise HTTPException(503,"Plex artwork unavailable")


@app.get("/api/playback/plex/{rating_key}")
def plex_handoff(rating_key:int):
    """Safe, token-free Plex Web handoff for testing exact-library navigation.
    This does NOT start video playback or seek to a position.
    """
    if rating_key<=0:raise HTTPException(400,"Invalid Plex rating key")
    with db() as c:
        item=c.execute("SELECT id,kind,title FROM media WHERE id IN (?,?)",("movie:"+str(rating_key),"episode:"+str(rating_key))).fetchone()
    if not item:raise HTTPException(404,"This rating key is not in the indexed Plex catalog")
    try:
        identity=plex_xml("/identity")
        machine_id=identity.get("machineIdentifier")
        if not machine_id:
            machine_id=plex_xml("/").get("machineIdentifier")
        if not machine_id or not re.fullmatch(r"[A-Za-z0-9-]{8,100}",machine_id):
            raise RuntimeError("Plex did not provide a valid server identifier")
    except Exception as e:
        raise HTTPException(502,"Cannot determine Plex server identity: "+str(e)[:120])
    from urllib.parse import quote
    details_key=quote("/library/metadata/"+str(rating_key),safe="")
    web_url="https://app.plex.tv/desktop/#!/server/"+machine_id+"/details?key="+details_key
    return {"title":item["title"],"kind":item["kind"],"web_url":web_url,
            "capability":"open_details_only",
            "note":"Opens the exact Plex library item details; automatic playback and join-in-progress are not yet implemented."}

@app.get("/api/match-diagnostics")
def match_diagnostics(q:str=Query(...,min_length=2,max_length=100)):
    """LAN-only troubleshooting: show guide episode metadata and Plex match status."""
    with db() as c:
        catalog=c.execute("SELECT * FROM media").fetchall()
        index=build_media_index(catalog)
        entries=[]
        for table,source in (("philo_listings","philo"),("tablo_listings","tablo"),("listings","xmltv")):
            found=c.execute("SELECT * FROM "+table+" WHERE title LIKE ? ORDER BY start DESC LIMIT 20",("%"+q+"%",)).fetchall()
            for item in found:
                p=dict(item)
                match_result=match(p,index)
                episodes=plex_series_candidates(p["title"],catalog)
                entries.append({"source":source,"channel":p["channel_label"],
                    "start":p["start"],"title":p["title"],"subtitle":p.get("subtitle"),
                    "season":p.get("season"),"episode":p.get("episode"),
                    "kind":p.get("program_kind"),"plex_series_episode_count":len(episodes),
                    "matched":bool(match_result),
                    "matched_title":match_result["title"] if match_result else None,
                    "matched_parts":[part["title"] for part in match_result.get("parts",[])]
                                    if match_result else []})
    # Show actual Plex series labels for troubleshooting naming variants,
    # without returning media paths, tokens or personally identifying data.
    related={}
    for item in catalog:
        if item["kind"]!="episode":continue
        show=item["show_title"] or ""
        if norm(q) in norm(show) or (norm(q)=="the office" and norm(show).startswith("the office")):
            related[show]=related.get(show,0)+1
    return {"query":q,"plex_similar_series":[{"title":k,"episode_count":v}
            for k,v in sorted(related.items())[:20]],"programs":entries[:50]}

@app.get("/api/search")
def search(q:str=Query("",max_length=200)):
    if len(q.strip())<2:return []
    with db() as c:
        return [dict(v) for v in c.execute("SELECT kind,title,show_title,year,season,episode FROM media WHERE title LIKE ? OR show_title LIKE ? LIMIT 40",(f"%{q}%",f"%{q}%")).fetchall()]

@app.get("/api/guide/find")
def find_guide(q:str=Query(...,min_length=2,max_length=100),source:str="all",limit:int=Query(40,ge=1,le=100)):
    """Fast guide title lookup for jumping to a future scheduled program."""
    if source not in ("all","philo","tablo","xmltv"):
        raise HTTPException(400,"Unsupported source")
    result=[]
    now=datetime.now(timezone.utc).isoformat()
    with db() as c:
        for table,kind in (("philo_listings","philo"),("tablo_listings","tablo"),("listings","xmltv")):
            if source not in ("all",kind):continue
            rows=c.execute("SELECT channel_id,channel_label,start,stop,title,subtitle FROM "+table+
                " WHERE stop > ? AND title LIKE ? ORDER BY start LIMIT ?",(now,"%"+q+"%",limit)).fetchall()
            for row in rows:
                item=dict(row)
                item["source"]=kind
                result.append(item)
    result.sort(key=lambda x:x["start"])
    return result[:limit]

@app.get("/api/philo/coverage")
def philo_coverage():
    """Read-only EPG coverage report for curated Philo channel links."""
    now=datetime.now(timezone.utc).isoformat()
    with db() as c:
        counts={row["channel_label"]:row["count"] for row in c.execute(
            "SELECT channel_label, COUNT(*) AS count FROM philo_listings WHERE stop > ? GROUP BY channel_label",
            (now,)).fetchall()}
        prefs={row["channel_id"]:dict(row) for row in c.execute(
            "SELECT channel_id, favorite, hidden FROM channel_prefs WHERE source='philo'").fetchall()}
    result=[]
    for channel in known_philo_channels():
        name=channel["name"]
        pref=prefs.get("philo:"+norm(name),{})
        result.append({"name":name,"future_epg_programs":counts.get(name,0),
                       "hidden":bool(pref.get("hidden",0)),
                       "favorite":bool(pref.get("favorite",0)),
                       "verification":channel["verification"]})
    try:
        raw_candidates=json.loads(metadata("hgtv_epg_candidates","[]"))
    except (ValueError,TypeError):
        raw_candidates=[]
    try:
        fallback_status=json.loads(metadata("hgtv_fallback_status","{}"))
    except (ValueError,TypeError):
        fallback_status={}
    try:
        supplemental_status=json.loads(metadata("supplemental_epg_status","{}"))
        supplemental_names=json.loads(metadata("supplemental_epg_channels","[]"))
        primary_names=json.loads(metadata("primary_epg_channels","[]"))
    except (ValueError,TypeError):
        supplemental_status,supplemental_names,primary_names={},[],[]
    known_links={c["name"] for c in known_philo_channels()}
    return {"last_philo_sync":metadata("last_philo_sync"),
            "philo_error":errors["philo"],"hgtv_feed_candidates":raw_candidates,
            "hgtv_fallback":fallback_status,
            "supplemental_epg":supplemental_status,
            "epg_sources":{"primary":primary_names,"supplemental":supplemental_names},
            "philo_handoff_coverage":{"mapped":len(known_links),
                                     "lineup_total":len(PHILO_NAMES),
                                     "not_mapped":sorted(set(PHILO_NAMES)-known_links)},
            "channels":result}


@app.get("/api/guide")
def guide(hours:int=Query(8,ge=1,le=24),plex_only:bool=False,favorites_only:bool=False,source:str="all",include_hidden:bool=False,start:str|None=None):
    now=datetime.now(timezone.utc)
    if start:
        try:
            now=datetime.fromisoformat(start.replace("Z","+00:00"))
            if now.tzinfo is None:raise ValueError("Timezone required")
            now=now.astimezone(timezone.utc)
        except (ValueError,TypeError):
            raise HTTPException(400,"start must be an ISO timestamp with timezone")
        current=datetime.now(timezone.utc)
        if now<current-timedelta(days=3) or now>current+timedelta(days=8):
            raise HTTPException(400,"start is outside the supported guide window")
    until=now+timedelta(hours=hours)
    with db() as c:
        listings=c.execute("SELECT * FROM listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        tablo=c.execute("SELECT * FROM tablo_listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        philo=c.execute("SELECT * FROM philo_listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        prefs={(x["source"],x["channel_id"]):x for x in c.execute("SELECT * FROM channel_prefs")}
        tablo_info={x["channel_id"]:dict(x) for x in c.execute("SELECT * FROM tablo_channels")}
        catalog=c.execute("SELECT * FROM media").fetchall()
    index=build_media_index(catalog)
    channels={}
    for collection,source_name in ((listings,"xmltv"),(tablo,"tablo"),(philo,"philo")):
      if source not in ("all",source_name):continue
      for item in collection:
        entry=dict(item)
        entry["source"]=source_name
        pref=prefs.get((source_name,entry["channel_id"]))
        entry["favorite"]=bool(pref["favorite"]) if pref else False
        entry["hidden"]=bool(pref["hidden"]) if pref else False
        if (entry["hidden"] and not include_hidden) or (favorites_only and not entry["favorite"]):continue
        entry["plex"]=match(entry,index)
        if source_name=="philo":
            entry["philo_links"]=philo_handoff_links(
                entry["channel_label"],entry["title"],entry.get("subtitle"),
                entry.get("season"),entry.get("episode"))
        if plex_only and not entry["plex"]:continue
        ch=entry["source"]+":"+entry["channel_id"]
        channel=channels.setdefault(ch,{"id":ch,"label":entry["channel_label"],"source":source_name,"favorite":entry["favorite"],"programs":[]})
        if source_name=="tablo":
            info=tablo_info.get(entry["channel_id"],{})
            channel["number"]=info.get("number") or entry["channel_label"]
            channel["network"]=info.get("network") or info.get("callsign") or ""
        channel["programs"].append(entry)
    for channel in channels.values():
        assign_scheduled_episode_parts(channel["programs"])
    # Schedule-independent OTA channel rows: a channel is tuneable even when
    # Tablo provides no guide airings (including entire empty EPG windows).
    if source in ("all","tablo") and not plex_only:
        for channel_id,info in tablo_info.items():
            if not re.fullmatch(r"/guide/channels/[A-Za-z0-9_-]{1,80}",channel_id):
                continue
            pref=prefs.get(("tablo",channel_id))
            favorite=bool(pref["favorite"]) if pref else False
            hidden=bool(pref["hidden"]) if pref else False
            if (hidden and not include_hidden) or (favorites_only and not favorite):
                continue
            key="tablo:"+channel_id
            number=info.get("number") or ""
            label=number or info.get("network") or info.get("callsign") or channel_id.rsplit("/",1)[-1]
            channel=channels.setdefault(key,{"id":key,"label":label,
                "source":"tablo","favorite":favorite,"programs":[]})
            channel["number"]=number or label
            channel["network"]=info.get("network") or info.get("callsign") or ""
            channel["programs"]=fill_tablo_schedule_gaps(
                channel["programs"],channel_id,label,now.isoformat(),until.isoformat())
    # A public Philo channel link does not imply the EPG has listings.
    # Keep known channels discoverable without inventing broadcast schedules.
    if source in ("all","philo") and not plex_only:
        for item in known_philo_channels():
            key="philo:philo:"+norm(item["name"])
            pref=prefs.get(("philo","philo:"+norm(item["name"])))
            if (pref and bool(pref["hidden"]) and not include_hidden) or (
                    favorites_only and not (pref and bool(pref["favorite"]))):
                continue
            if key not in channels:
                channels[key]={"id":key,"label":item["name"],"source":"philo",
                               "favorite":bool(pref["favorite"]) if pref else False,
                               "programs":[],"schedule_unavailable":True,
                               "philo_channel_url":item["url"],
                               "philo_verification":item["verification"]}
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"window_start":now.isoformat(),"window_end":until.isoformat(),"channels":list(channels.values())}

@app.on_event("startup")
def start_background_refresh():
    def worker():
        while True:
            try:
                last=metadata("last_plex_sync")
                if not last or time.time()-datetime.fromisoformat(last).timestamp()>REFRESH:sync_plex()
            except Exception:pass
            try:
                if GUIDE.exists() and float(metadata("guide_mtime","0"))!=GUIDE.stat().st_mtime:import_guide()
            except Exception:pass
            try:
                if TABLO_URL:
                    last=metadata("last_tablo_attempt")
                    if not last or time.time()-datetime.fromisoformat(last).timestamp()>TABLO_REFRESH:
                        with db() as c:putmeta(c,"last_tablo_attempt",datetime.now(timezone.utc).isoformat())
                        sync_tablo()
            except Exception:pass
            try:
                last=metadata("last_philo_attempt")
                if not last or time.time()-datetime.fromisoformat(last).timestamp()>PHILO_REFRESH:
                    with db() as c:putmeta(c,"last_philo_attempt",datetime.now(timezone.utc).isoformat())
                    sync_philo()
            except Exception:pass
            time.sleep(60)
    threading.Thread(target=worker,daemon=True).start()
