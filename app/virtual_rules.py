"""Deterministic rule-based programming for Plex virtual channels.

Use metadata already synced into Universal Guide and keep the same channel
timeline across every viewer, app restart and guide refresh.
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field

class SmartRules(BaseModel):
    media_type: str = "episodes"  # episodes, movies, all
    genres: list[str] = Field(default_factory=list, max_length=12)
    shows: list[str] = Field(default_factory=list, max_length=30)
    year_min: int | None = Field(default=None, ge=1880, le=2200)
    year_max: int | None = Field(default=None, ge=1880, le=2200)
    order: str = "shuffle"  # shuffle, chronological, newest
    seed: int = Field(default=0, ge=0, le=1000000)
    default_episode_minutes: int = Field(default=30, ge=1, le=360)
    default_movie_minutes: int = Field(default=120, ge=1, le=360)

def validate_rules(rule):
    if rule.media_type not in ("episodes", "movies", "all"):
        raise HTTPException(422,"Invalid content type")
    if rule.order not in ("shuffle","chronological","newest"):
        raise HTTPException(422,"Invalid programming order")
    if rule.year_min is not None and rule.year_max is not None and rule.year_min > rule.year_max:
        raise HTTPException(422,"Year range is reversed")
    for v in rule.genres + rule.shows:
        if not v.strip() or len(v)>100 or any(ord(c)<32 for c in v):
            raise HTTPException(422,"Invalid genre or TV show")
    if rule.media_type == "movies" and rule.shows:
        raise HTTPException(422,"Show filters only apply to episodes")

def _normalize(v):
    return str(v or "").casefold().strip()

def matching_items(catalog, rule):
    validate_rules(rule)
    genres={_normalize(x) for x in rule.genres}
    shows={_normalize(x) for x in rule.shows}
    result=[]
    for item in catalog:
        if item["kind"] not in ("movie","episode"):
            continue
        if rule.media_type=="movies" and item["kind"]!="movie":
            continue
        if rule.media_type=="episodes" and item["kind"]!="episode":
            continue
        year=int(item.get("year") or 0)
        if rule.year_min is not None and year<rule.year_min:
            continue
        if rule.year_max is not None and (not year or year>rule.year_max):
            continue
        item_genres={_normalize(x) for x in (item.get("genres") or "").split(",") if x}
        if genres and not genres.intersection(item_genres):
            continue
        if shows and _normalize(item.get("show_title")) not in shows:
            continue
        if not str(item.get("id","")).rsplit(":",1)[-1].isdigit():
            continue
        result.append(item)
    # Sort consistently for predictable seed-based shuffling, and retain
    # season/episode chronology for show marathons.
    def chronology(m):
        return (_normalize(m.get("show_title") or m.get("title")),
                int(m.get("season") or 0),int(m.get("episode") or 0),
                int(m.get("year") or 0),str(m["id"]))
    if rule.order == "newest":
        result.sort(key=lambda m:(-int(m.get("year") or 0),)+chronology(m))
    else:
        result.sort(key=chronology)
        if rule.order == "shuffle":
            result.sort(key=lambda m:hashlib.sha256(
                (str(rule.seed)+"|"+str(m["id"])).encode()).digest())
    return result

def pool(catalog,rule):
    result=matching_items(catalog,rule)
    return {"matching":len(result),"movies":sum(x["kind"]=="movie" for x in result),
            "episodes":sum(x["kind"]=="episode" for x in result),
            "sample":[{"title":x.get("title"),"show":x.get("show_title"),
                       "year":x.get("year"),"rating_key":x["id"].rsplit(":",1)[-1]}
                      for x in result[:15]]}

def duration_minutes(item,rule):
    value=int(item.get("duration_ms") or 0)
    if value>0:
        return max(1,min(360,math.ceil(value/60000)))
    return rule.default_movie_minutes if item["kind"]=="movie" else rule.default_episode_minutes

def build_cycle(catalog,rule):
    return [(item,duration_minutes(item,rule)) for item in matching_items(catalog,rule)]

def schedule_cycle(playlist,window_start,window_end):
    """Yield only overlapping programs, anchored to a stable UTC epoch.

    Runtime depends on the total cycle duration, not wall-clock minute
    offsets or a random generator invocation for each request.
    """
    if not playlist:
        return
    epoch=datetime(2020,1,1,tzinfo=timezone.utc)
    total=sum(minutes for _,minutes in playlist)
    if total<=0:
        return
    start=window_start.astimezone(timezone.utc)
    end=window_end.astimezone(timezone.utc)
    offset_minutes=math.floor((start-epoch).total_seconds()/60)
    start_cycle=(offset_minutes//total)*total
    cursor=epoch+timedelta(minutes=start_cycle)
    while cursor<end:
        for item,minutes in playlist:
            stop=cursor+timedelta(minutes=minutes)
            if stop>start and cursor<end:
                yield item,cursor,stop
            cursor=stop
            if cursor>=end:
                break
