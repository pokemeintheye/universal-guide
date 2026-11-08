import os, re, time, sqlite3, threading, xml.etree.ElementTree as ET
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
REFRESH=max(5,int(os.getenv("REFRESH_MINUTES","30")))*60
TABLO_URL=os.getenv("TABLO_URL","").rstrip("/")
TABLO_REFRESH=max(1,int(os.getenv("TABLO_REFRESH_HOURS","6")))*3600
PHILO_EPG_URL="https://raw.githubusercontent.com/acidjesuz/EPGTalk/master/US_guide.xml.gz"
PHILO_REFRESH=max(6,int(os.getenv("PHILO_REFRESH_HOURS","24")))*3600
app=FastAPI(title="Universal TV Guide",version="0.1.0")
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
        CREATE TABLE IF NOT EXISTS tablo_listings(id TEXT PRIMARY KEY,channel_id TEXT,channel_label TEXT,start TEXT,stop TEXT,title TEXT,subtitle TEXT,year INT,description TEXT,season INT,episode INT);\n        CREATE INDEX IF NOT EXISTS idx_tablo_time ON tablo_listings(start,stop);\n        CREATE TABLE IF NOT EXISTS philo_listings(id INTEGER PRIMARY KEY AUTOINCREMENT,channel_id TEXT,channel_label TEXT,start TEXT,stop TEXT,title TEXT,subtitle TEXT,year INT,description TEXT);\n        CREATE INDEX IF NOT EXISTS idx_philo_time ON philo_listings(start,stop);\n        CREATE TABLE IF NOT EXISTS channel_prefs(source TEXT,channel_id TEXT,favorite INTEGER NOT NULL DEFAULT 0,hidden INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(source,channel_id));\n        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);""")
initialize()

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
                    title=obj.get("title","")
                    rows.append(("movie:"+str(obj.get("ratingKey")),"movie",title,int(obj.get("year","0") or 0),norm(title),"",None,None,",".join(x.get("id","") for x in obj.findall("Guid"))))
                elif kind=="show" and obj.get("type")=="show" and obj.get("ratingKey"):
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
        return {"media_count":len(rows),"libraries":libraries}
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
                    rows.append((node.get("channel"),a,b,get("title"),get("sub-title"),year,get("desc")[:600]))
            if node.tag in ("channel","programme"):node.clear()
        with db() as c:
            c.execute("DELETE FROM listings")
            c.executemany("INSERT INTO listings(channel_id,channel_label,start,stop,title,subtitle,year,description) VALUES (?,?,?,?,?,?,?,?)",[(ch,names.get(ch,ch),a,b,title,sub,year,desc) for ch,a,b,title,sub,year,desc in rows])
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
        channel_names={}
        entries=[]
        root=ET.fromstring(xml)
        for channel in root.findall("channel"):
            labels=[x.text.strip() for x in channel.findall("display-name") if x.text]
            matches={philo_name(v) for v in labels}
            matches.discard(None)
            # Accept unambiguous matches only. Avoid falsely labeling unrelated networks.
            if len(matches)==1:channel_names[channel.get("id")]=next(iter(matches))
        now=datetime.now(timezone.utc)
        for p in root.findall("programme"):
            ch=p.get("channel")
            if ch not in channel_names:continue
            start=xml_date(p.get("start"));stop=xml_date(p.get("stop"))
            if not start or not stop:continue
            if stop<now.isoformat() or start>(now+timedelta(days=8)).isoformat():continue
            value=lambda key:(p.findtext(key) or "").strip()
            date=value("date")
            year=int(date[:4]) if re.match(r"^\\d{4}",date) else 0
            entries.append((ch,channel_names[ch],start,stop,value("title"),value("sub-title"),year,value("desc")[:600]))
        if not entries:raise RuntimeError("No matching Philo lineup schedules found in EPG; preserving existing guide")
        with db() as c:
            c.execute("DELETE FROM philo_listings")
            c.executemany("INSERT INTO philo_listings(channel_id,channel_label,start,stop,title,subtitle,year,description) VALUES (?,?,?,?,?,?,?,?)",entries)
            putmeta(c,"last_philo_sync",datetime.now(timezone.utc).isoformat())
            putmeta(c,"philo_channel_count",len(set(e[0] for e in entries)))
        errors["philo"]=""
        return {"programs":len(entries),"matched_channels":len(set(e[0] for e in entries)),"published_philo_channels":len(PHILO_NAMES)}
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
        return (path,channel_path,label,start.isoformat(),stop.isoformat(),title,subtitle,int(year or 0),str(desc)[:600],season,number)
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
        with db() as c:
            c.execute("DELETE FROM tablo_listings")
            c.executemany("INSERT OR REPLACE INTO tablo_listings VALUES (?,?,?,?,?,?,?,?,?,?,?)",rows)
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

def match(p,catalog):
    title=norm(p["title"]);year=p["year"] or 0
    if not title:return None
    for m in catalog:
        if m["kind"]=="movie" and m["normalized"]==title and (not year or not m["year"] or abs(m["year"]-year)<=1):
            return {"kind":"movie","title":m["title"],"rating_key":m["id"].split(":",1)[1]}
    subtitle=norm(p["subtitle"])
    if subtitle:
        for m in catalog:
            if m["kind"]=="episode" and m["normalized"]==title and norm(m["title"])==subtitle:
                return {"kind":"episode","title":m["title"],"show":m["show_title"],"rating_key":m["id"].split(":",1)[1]}
    return None

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
    return {"plex_configured":bool(PLEX_URL and PLEX_TOKEN and not PLEX_TOKEN.startswith("REPLACE_")),"media_count":media,"listings_count":listings,"plex_sync":metadata("last_plex_sync"),"guide_sync":metadata("last_guide_sync"),"plex_error":errors["plex"],"guide_error":errors["guide"],"xmltv_file_present":GUIDE.exists(),"tablo_configured":bool(TABLO_URL),"tablo_count":tablo_count,"tablo_sync":metadata("last_tablo_sync"),"tablo_error":errors["tablo"],"tablo_truncated":metadata("tablo_truncated","0")=="1","philo_count":philo_count,"philo_channels":int(metadata("philo_channel_count","0")),"philo_sync":metadata("last_philo_sync"),"philo_error":errors["philo"]}

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
        items=c.execute("""SELECT 'tablo' source,channel_id,channel_label,COUNT(*) programs FROM tablo_listings GROUP BY channel_id
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

@app.get("/api/search")
def search(q:str=Query("",max_length=200)):
    if len(q.strip())<2:return []
    with db() as c:
        return [dict(v) for v in c.execute("SELECT kind,title,show_title,year,season,episode FROM media WHERE title LIKE ? OR show_title LIKE ? LIMIT 40",(f"%{q}%",f"%{q}%")).fetchall()]

@app.get("/api/guide")
def guide(hours:int=Query(8,ge=1,le=24),plex_only:bool=False,favorites_only:bool=False,source:str="all",include_hidden:bool=False):
    now=datetime.now(timezone.utc);until=now+timedelta(hours=hours)
    with db() as c:
        listings=c.execute("SELECT * FROM listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        tablo=c.execute("SELECT * FROM tablo_listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        philo=c.execute("SELECT * FROM philo_listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        prefs={(x["source"],x["channel_id"]):x for x in c.execute("SELECT * FROM channel_prefs")}
        catalog=c.execute("SELECT * FROM media").fetchall()
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
        entry["plex"]=match(entry,catalog)
        if plex_only and not entry["plex"]:continue
        ch=entry["source"]+":"+entry["channel_id"]
        channels.setdefault(ch,{"id":ch,"label":entry["channel_label"],"source":source_name,"favorite":entry["favorite"],"programs":[]})["programs"].append(entry)
    return {"generated_at":now.isoformat(),"channels":list(channels.values())}

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
