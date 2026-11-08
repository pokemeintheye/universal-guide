import os, re, time, sqlite3, threading, xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

DB=Path("/data/guide.db")
GUIDE=Path(os.getenv("GUIDE_XMLTV","/data/guide.xml"))
PLEX_URL=os.getenv("PLEX_URL","").rstrip("/")
PLEX_TOKEN=os.getenv("PLEX_TOKEN","")
REFRESH=max(5,int(os.getenv("REFRESH_MINUTES","30")))*60
app=FastAPI(title="Universal TV Guide",version="0.1.0")
sync_lock=threading.Lock()
errors={"plex":"","guide":""}

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
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);""")
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
    return {"plex_configured":bool(PLEX_URL and PLEX_TOKEN and not PLEX_TOKEN.startswith("REPLACE_")),"media_count":media,"listings_count":listings,"plex_sync":metadata("last_plex_sync"),"guide_sync":metadata("last_guide_sync"),"plex_error":errors["plex"],"guide_error":errors["guide"],"xmltv_file_present":GUIDE.exists()}

@app.post("/api/sync/plex")
def refresh_plex():
    try:return sync_plex()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.post("/api/sync/guide")
def refresh_guide():
    try:return import_guide()
    except Exception as e:raise HTTPException(502,detail=str(e))

@app.get("/api/search")
def search(q:str=Query("",max_length=200)):
    if len(q.strip())<2:return []
    with db() as c:
        return [dict(v) for v in c.execute("SELECT kind,title,show_title,year,season,episode FROM media WHERE title LIKE ? OR show_title LIKE ? LIMIT 40",(f"%{q}%",f"%{q}%")).fetchall()]

@app.get("/api/guide")
def guide(hours:int=Query(8,ge=1,le=24),plex_only:bool=False):
    now=datetime.now(timezone.utc);until=now+timedelta(hours=hours)
    with db() as c:
        listings=c.execute("SELECT * FROM listings WHERE start < ? AND stop > ? ORDER BY channel_label,start",(until.isoformat(),now.isoformat())).fetchall()
        catalog=c.execute("SELECT * FROM media").fetchall()
    channels={}
    for item in listings:
        entry=dict(item)
        entry["plex"]=match(entry,catalog)
        if plex_only and not entry["plex"]:continue
        ch=entry["channel_id"]
        channels.setdefault(ch,{"id":ch,"label":entry["channel_label"],"programs":[]})["programs"].append(entry)
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
            time.sleep(60)
    threading.Thread(target=worker,daemon=True).start()
