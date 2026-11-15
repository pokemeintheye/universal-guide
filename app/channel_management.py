"""Persisted Unraid channel preferences and Plex virtual-channel definitions.

Configuration is stored in /data so ordinary channel changes do not depend
on a Docker image rebuild. Keep the container LAN-only or set GUIDE_ADMIN_TOKEN.
"""
import os
import re
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.virtual_rules import SmartRules, validate_rules, pool as rule_pool, build_cycle, schedule_cycle

router = APIRouter(prefix="/api/manage", tags=["channel-management"])
STORE = Path(os.getenv("GUIDE_CHANNEL_DB", "/data/channel-management.db"))
ADMIN_TOKEN = os.getenv("GUIDE_ADMIN_TOKEN", "").strip()
NUMBER = re.compile(r"[0-9]{1,6}\Z")
VIRTUAL_ID = re.compile(r"[a-f0-9]{16}\Z")
MAX_CHANNELS = 80


def _db():
    STORE.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(STORE, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE IF NOT EXISTS tve_channels(
        number TEXT PRIMARY KEY, name TEXT NOT NULL, guide_label TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'channels-dvr',
        enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS virtual_channels(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS virtual_items(
        channel_id TEXT NOT NULL, position INTEGER NOT NULL, rating_key INTEGER NOT NULL,
        duration_minutes INTEGER NOT NULL,
        PRIMARY KEY(channel_id,position),
        FOREIGN KEY(channel_id) REFERENCES virtual_channels(id));
    CREATE TABLE IF NOT EXISTS virtual_rule_config(
        channel_id TEXT PRIMARY KEY, rules_json TEXT NOT NULL);
    """)
    return c


def require_admin(request: Request):
    if ADMIN_TOKEN and not secrets.compare_digest(
        request.headers.get("x-guide-admin-token", ""), ADMIN_TOKEN):
        raise HTTPException(401, "Administrator token required")


def configured_stations():
    """Current configured lineup: preinstalled defaults with persisted overrides."""
    from app.channels_dvr import TVE_STATIONS, GUIDE_LABELS
    result = {
        number: {"number": number, "name": name,
                 "guide_label": GUIDE_LABELS.get(number, [name])[0],
                 "aliases": list(GUIDE_LABELS.get(number, [name])),
                 "priority": "channels-dvr", "enabled": True}
        for number, name in TVE_STATIONS.items()
    }
    with _db() as c:
        for row in c.execute("SELECT * FROM tve_channels"):
            r = dict(row)
            number = r["number"]
            old = result.get(number, {})
            aliases = list(dict.fromkeys([r["guide_label"]] + old.get("aliases", [])))
            result[number] = {"number": number, "name": r["name"],
                              "guide_label": r["guide_label"], "aliases": aliases,
                              "priority": r["priority"], "enabled": bool(r["enabled"])}
    return result


class StationUpdate(BaseModel):
    number: str = Field(min_length=1, max_length=6)
    name: str = Field(min_length=1, max_length=90)
    guide_label: str = Field(min_length=1, max_length=90)
    priority: str = "channels-dvr"
    enabled: bool = True


def _validate_station(v):
    if not NUMBER.fullmatch(v.number):
        raise HTTPException(422, "Channel number must contain 1–6 digits")
    if v.priority not in ("channels-dvr", "philo"):
        raise HTTPException(422, "Choose Channels DVR or Philo for primary playback")
    if not v.name.strip() or not v.guide_label.strip():
        raise HTTPException(422, "Name and guide label are required")
    if any(ord(c) < 32 for c in v.name + v.guide_label):
        raise HTTPException(422, "Control characters are not allowed")


@router.get("/channels")
def list_channels(request: Request):
    require_admin(request)
    from app.channels_dvr import _playlist, _channel_stream
    configured = configured_stations()
    discovered = {}
    error = None
    try:
        base, playlist = _playlist()
        # Include all exported TVE stations. Presence is not proof that
        # provider authorization and playback will succeed.
        lines = playlist.splitlines()
        for line in lines:
            if not line.startswith("#EXTINF:"):
                continue
            tags = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            number = tags.get("tvg-chno") or tags.get("channel-number")
            if number and NUMBER.fullmatch(number):
                label = line.rsplit(",", 1)[-1].strip()[:90]
                discovered[number] = label or number
        available = {n for n in discovered if _channel_stream(playlist, base, n)}
    except HTTPException as exc:
        available = set()
        error = str(exc.detail)
    rows = []
    for n in sorted(set(configured) | set(discovered), key=lambda x: int(x)):
        saved = configured.get(n)
        rows.append({"number": n, "name": (saved or {}).get("name") or discovered[n],
                     "guide_label": (saved or {}).get("guide_label") or discovered[n],
                     "priority": (saved or {}).get("priority", "channels-dvr"),
                     "enabled": (saved or {}).get("enabled", False),
                     "configured": bool(saved), "discovered": n in discovered,
                     "available": n in available})
    return {"channels": rows, "error": error,
            "note": "Available means exported by Channels DVR, not guaranteed to play"}


@router.put("/channels/{number}")
def save_channel(number: str, body: StationUpdate, request: Request):
    require_admin(request)
    _validate_station(body)
    if number != body.number or not NUMBER.fullmatch(number):
        raise HTTPException(422, "Channel number mismatch")
    from app.channels_dvr import TVE_STATIONS, _playlist, _channel_stream
    existing = configured_stations()
    if number not in existing:
        if len(existing) >= MAX_CHANNELS:
            raise HTTPException(409, "Maximum configured channel count reached")
        base, playlist = _playlist()
        if not _channel_stream(playlist, base, number):
            raise HTTPException(422, "New channel must appear in the current Channels DVR export")
    with _db() as c:
        c.execute("""INSERT INTO tve_channels(number,name,guide_label,priority,enabled)
            VALUES (?,?,?,?,?) ON CONFLICT(number) DO UPDATE SET
            name=excluded.name, guide_label=excluded.guide_label,
            priority=excluded.priority, enabled=excluded.enabled""",
            (number, body.name.strip(), body.guide_label.strip(),
             body.priority, int(body.enabled)))
        c.commit()
    return {"saved": True, "number": number}


@router.delete("/channels/{number}")
def reset_channel(number: str, request: Request):
    require_admin(request)
    if not NUMBER.fullmatch(number):
        raise HTTPException(422, "Invalid channel number")
    with _db() as c:
        c.execute("DELETE FROM tve_channels WHERE number=?", (number,))
        c.commit()
    return {"reset": True, "number": number}


def virtual_definitions():
    with _db() as c:
        channels = []
        for row in c.execute("SELECT * FROM virtual_channels ORDER BY name"):
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            rules_row=c.execute("SELECT rules_json FROM virtual_rule_config WHERE channel_id=?",
                                (item["id"],)).fetchone()
            item["mode"]="smart" if rules_row else "manual"
            item["rules"]=json.loads(rules_row["rules_json"]) if rules_row else None
            item["items"] = [dict(x) for x in c.execute(
                "SELECT rating_key,duration_minutes FROM virtual_items WHERE channel_id=? ORDER BY position",
                (item["id"],))]
            channels.append(item)
    return channels


class VirtualItem(BaseModel):
    rating_key: int = Field(gt=0)
    duration_minutes: int = Field(ge=1, le=360)


class VirtualUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    mode: str = "manual"
    rules: SmartRules | None = None
    items: list[VirtualItem] = Field(default_factory=list, max_length=100)


def _validated_virtual(body):
    if not body.name.strip() or any(ord(c) < 32 for c in body.name):
        raise HTTPException(422, "Invalid virtual channel name")
    if body.mode not in ("manual","smart"):
        raise HTTPException(422,"Choose manual or smart channel mode")
    if body.mode == "smart":
        if body.rules is None:
            raise HTTPException(422,"Smart channel rules are required")
        validate_rules(body.rules)
        return
    if not body.items:
        raise HTTPException(422, "Choose at least one Plex movie or episode")
    from app.main import db
    with db() as c:
        ids = {str(x["id"]) for x in c.execute("SELECT id FROM media")}
    missing = [x.rating_key for x in body.items
               if not any(item.endswith(":" + str(x.rating_key)) for item in ids)]
    if missing:
        raise HTTPException(422, "These Plex items aren't in the current synced library: " +
                            ", ".join(str(x) for x in missing[:5]))


@router.get("/plex-items")
def plex_items(request: Request, q: str = ""):
    require_admin(request)
    from app.main import db
    needle = q.strip().lower()[:80]
    with db() as c:
        rows = c.execute("""SELECT id,kind,title,show_title,year,season,episode
            FROM media WHERE (id LIKE 'movie:%' OR id LIKE 'episode:%')
              AND (? = '' OR instr(lower(COALESCE(title,'')), ?) > 0
                       OR instr(lower(COALESCE(show_title,'')), ?) > 0)
            ORDER BY COALESCE(show_title,title),season,episode,title LIMIT 100""",
            (needle, needle, needle)).fetchall()
    return {"items": [
        {"rating_key": int(r["id"].rsplit(":", 1)[-1]), "kind": r["kind"],
         "title": r["title"], "show_title": r["show_title"],
         "year": r["year"], "season": r["season"], "episode": r["episode"]}
        for r in rows if r["id"].rsplit(":", 1)[-1].isdigit()
    ]}


@router.post("/virtual/preview")
def preview_rules(body: SmartRules, request: Request):
    require_admin(request)
    validate_rules(body)
    from app.main import db
    with db() as c:
        catalog=[dict(x) for x in c.execute("SELECT * FROM media")]
    return rule_pool(catalog,body)


@router.get("/virtual/filters")
def rule_filters(request: Request):
    require_admin(request)
    from app.main import db
    from app.virtual_rules import _metadata_values
    with db() as c:
        catalog=[dict(x) for x in c.execute(
            "SELECT kind,show_title,genres,plex_labels,plex_tags FROM media")]
    genres=set()
    shows=set()
    labels=set()
    tags=set()
    for item in catalog:
        genres.update(g.strip() for g in (item.get("genres") or "").split(",") if g.strip())
        labels.update(_metadata_values(item.get("plex_labels")))
        tags.update(_metadata_values(item.get("plex_tags")))
        if item["kind"]=="episode" and item.get("show_title"):
            shows.add(item["show_title"])
    return {"genres":sorted(genres,key=str.casefold),
            "shows":sorted(shows,key=str.casefold),
            "labels":sorted(labels,key=str.casefold),
            "tags":sorted(tags,key=str.casefold)}


@router.get("/virtual")
def list_virtual(request: Request):
    require_admin(request)
    return {"channels": virtual_definitions()}


@router.post("/virtual")
def create_virtual(body: VirtualUpdate, request: Request):
    require_admin(request)
    _validated_virtual(body)
    with _db() as c:
        if c.execute("SELECT count(*) FROM virtual_channels").fetchone()[0] >= 50:
            raise HTTPException(409, "Virtual channel limit reached")
        channel_id = secrets.token_hex(8)
        c.execute("INSERT INTO virtual_channels VALUES (?,?,?)",
                  (channel_id, body.name.strip(), int(body.enabled)))
        c.executemany("INSERT INTO virtual_items VALUES (?,?,?,?)",
                      [(channel_id, i, x.rating_key, x.duration_minutes)
                       for i, x in enumerate(body.items)])
        if body.mode == "smart":
            c.execute("INSERT INTO virtual_rule_config VALUES (?,?)",
                      (channel_id,body.rules.model_dump_json()))
        c.commit()
    return {"id": channel_id, "saved": True}


@router.put("/virtual/{channel_id}")
def update_virtual(channel_id: str, body: VirtualUpdate, request: Request):
    require_admin(request)
    _validated_virtual(body)
    if not VIRTUAL_ID.fullmatch(channel_id):
        raise HTTPException(404, "Unknown virtual channel")
    with _db() as c:
        if not c.execute("SELECT 1 FROM virtual_channels WHERE id=?", (channel_id,)).fetchone():
            raise HTTPException(404, "Unknown virtual channel")
        c.execute("UPDATE virtual_channels SET name=?,enabled=? WHERE id=?",
                  (body.name.strip(), int(body.enabled), channel_id))
        c.execute("DELETE FROM virtual_items WHERE channel_id=?", (channel_id,))
        c.executemany("INSERT INTO virtual_items VALUES (?,?,?,?)",
                      [(channel_id, i, x.rating_key, x.duration_minutes)
                       for i, x in enumerate(body.items)])
        c.execute("DELETE FROM virtual_rule_config WHERE channel_id=?", (channel_id,))
        if body.mode == "smart":
            c.execute("INSERT INTO virtual_rule_config VALUES (?,?)",
                      (channel_id,body.rules.model_dump_json()))
        c.commit()
    return {"id": channel_id, "saved": True}


@router.delete("/virtual/{channel_id}")
def delete_virtual(channel_id: str, request: Request):
    require_admin(request)
    if not VIRTUAL_ID.fullmatch(channel_id):
        raise HTTPException(404, "Unknown virtual channel")
    with _db() as c:
        c.execute("DELETE FROM virtual_items WHERE channel_id=?", (channel_id,))
        c.execute("DELETE FROM virtual_rule_config WHERE channel_id=?", (channel_id,))
        c.execute("DELETE FROM virtual_channels WHERE id=?", (channel_id,))
        c.commit()
    return {"deleted": True}


def virtual_guide(window_start, window_end):
    """Build a repeatable 24/7 schedule from explicitly selected Plex items.

    This is a schedule of on-demand items, not an uninterrupted linear stream.
    Each guide program retains its exact Plex rating key for current tvOS.
    """
    from app.main import db
    definitions = [x for x in virtual_definitions() if x["enabled"] and
                   (x["items"] or x["mode"]=="smart")]
    if not definitions:
        return []
    with db() as c:
        catalog = {int(x["id"].rsplit(":", 1)[-1]): dict(x)
                   for x in c.execute("SELECT * FROM media")
                   if str(x["id"]).rsplit(":", 1)[-1].isdigit()}
    start = window_start.astimezone(timezone.utc)
    end = window_end.astimezone(timezone.utc)
    rows = []
    for channel in definitions:
        if channel["mode"] == "smart":
            rule=SmartRules.model_validate(channel["rules"])
            playlist=build_cycle(list(catalog.values()),rule)
        else:
            playlist = [(catalog[x["rating_key"]], x["duration_minutes"])
                        for x in channel["items"] if x["rating_key"] in catalog]
        if not playlist:
            continue
        programs = []
        for item,cursor,stop in schedule_cycle(playlist,start,end):
            kind = "movie" if item.get("kind") == "movie" else "episode"
            title = item["title"] if kind == "movie" else (item.get("show_title") or item["title"])
            programs.append({
                "id": "virtual:" + channel["id"] + ":" + cursor.isoformat(),
                "channel_id": channel["id"], "channel_label": channel["name"],
                "source": "virtual", "start": cursor.isoformat(), "stop": stop.isoformat(),
                "title": title, "subtitle": "" if kind == "movie" else item["title"],
                "year": item.get("year"), "program_kind": kind,
                "season": item.get("season"), "episode": item.get("episode"),
                "description": "Scheduled from your Plex library",
                "plex": {"kind": kind, "rating_key": str(item["id"].rsplit(":", 1)[-1]),
                         "title": item["title"], "show": item.get("show_title")},
                "favorite": False, "hidden": False})
        rows.append({"id": "virtual:" + channel["id"], "label": channel["name"],
                     "source": "virtual", "favorite": False, "programs": programs})
    return rows
