"""Persistent per-channel visibility and manual order for all guide sources.

Keys are the public guide channel IDs (source:provider ID), never display
names; this preserves preferences across station renames and Plex resyncs.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router=APIRouter(prefix="/api/manage",tags=["guide-lineup"])

def _db():
    from app.channel_management import _db
    return _db()

def _auth(request):
    from app.channel_management import require_admin
    require_admin(request)

def _table():
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS guide_lineup(
            channel_id TEXT PRIMARY KEY,
            position INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1)""")
        c.commit()

def current():
    _table()
    with _db() as c:
        return {r["channel_id"]:dict(r) for r in c.execute(
            "SELECT channel_id,position,enabled FROM guide_lineup")}

def sort_lineup(rows,include_hidden=False):
    """Use saved positions; default virtual rows after six ordinary rows.

    Unknown channels are not lost after rescans or Plex library additions.
    A saved custom position always wins over the initial default.
    """
    prefs=current()
    if not prefs:
        ordinary=[x for x in rows if x["source"]!="virtual"]
        virtual=[x for x in rows if x["source"]=="virtual"]
        ordered=ordinary[:6]+virtual+ordinary[6:]
    else:
        default_rank={x["id"]:i for i,x in enumerate(rows)}
        ordered=sorted(rows,key=lambda x:(
            0 if x["id"] in prefs else 1,
            prefs[x["id"]]["position"] if x["id"] in prefs else default_rank[x["id"]]))
    result=[]
    for item in ordered:
        item=dict(item)
        enabled=bool(prefs[item["id"]]["enabled"]) if item["id"] in prefs else True
        item["lineup_enabled"]=enabled
        if enabled or include_hidden:
            result.append(item)
    return result

class LineupUpdate(BaseModel):
    ids: list[str]=Field(min_length=1,max_length=1000)
    enabled: dict[str,bool]=Field(default_factory=dict)

@router.get("/lineup")
def lineup(request: Request):
    _auth(request)
    from app.main import guide
    # Use current real providers, including hidden rows, to avoid inventing
    # channel entries or returning disconnected configuration-only stations.
    guide_rows=guide(hours=2,source="all",include_hidden=True,
                     plex_only=False,favorites_only=False,start=None)["channels"]
    prefs=current()
    return {"channels":[{
       "id":r["id"],"label":r["label"],"source":r["source"],
       "number":r.get("number"),"enabled":bool(r["lineup_enabled"]),
       "program_count":len(r.get("programs",[]))}
       for r in guide_rows],
       "configured":len(prefs)}

@router.put("/lineup")
def save_lineup(data: LineupUpdate,request: Request):
    _auth(request)
    if len(set(data.ids))!=len(data.ids):
        raise HTTPException(422,"Duplicate channel IDs")
    if any(not x or len(x)>350 or ":" not in x for x in data.ids):
        raise HTTPException(422,"Invalid channel ID")
    if set(data.enabled)-set(data.ids):
        raise HTTPException(422,"Visibility settings contain an unknown channel ID")
    from app.main import guide
    actual=guide(hours=2,source="all",include_hidden=True,
                 plex_only=False,favorites_only=False,start=None)["channels"]
    known={x["id"] for x in actual}
    if not set(data.ids)<=known:
        raise HTTPException(409,"Guide channels changed; refresh the lineup before saving")
    old=current()
    # Retain positions for guide channels temporarily missing due to stale
    # source EPG, while keeping existing channel visibility intact.
    with _db() as c:
        c.executemany("""INSERT INTO guide_lineup(channel_id,position,enabled)
            VALUES (?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
            position=excluded.position,enabled=excluded.enabled""",[
             (key,i,int(data.enabled.get(key,bool(old[key]["enabled"]) if key in old else True)))
             for i,key in enumerate(data.ids)])
        c.commit()
    return {"saved":len(data.ids)}
