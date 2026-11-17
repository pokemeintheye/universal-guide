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
    logic: dict | None = None  # Nested any/all/none groups, or a predicate
    genres: list[str] = Field(default_factory=list, max_length=12)
    labels: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    shows: list[str] = Field(default_factory=list, max_length=30)
    year_min: int | None = Field(default=None, ge=1880, le=2200)
    year_max: int | None = Field(default=None, ge=1880, le=2200)
    order: str = "shuffle"  # legacy order, preserved for saved channels
    episode_order: str | None = None  # chronological, shuffle, newest
    movie_order: str | None = None  # shuffle, oldest, newest
    collection_order: str = "off"  # off, release (keep films grouped in release order)
    seed: int = Field(default=0, ge=0, le=1000000)
    default_episode_minutes: int = Field(default=30, ge=1, le=360)
    default_movie_minutes: int = Field(default=120, ge=1, le=360)

def validate_rules(rule):
    if rule.media_type not in ("episodes", "movies", "all"):
        raise HTTPException(422,"Invalid content type")
    if rule.order not in ("shuffle","chronological","newest"):
        raise HTTPException(422,"Invalid programming order")
    if rule.episode_order not in (None,"chronological","shuffle","newest"):
        raise HTTPException(422,"Invalid TV episode order")
    if rule.movie_order not in (None,"shuffle","oldest","newest"):
        raise HTTPException(422,"Invalid movie order")
    if rule.collection_order not in ("off","release"):
        raise HTTPException(422,"Invalid movie collection order")
    if rule.year_min is not None and rule.year_max is not None and rule.year_min > rule.year_max:
        raise HTTPException(422,"Year range is reversed")
    for v in rule.genres + rule.shows + rule.labels + rule.tags:
        if not v.strip() or len(v)>100 or any(ord(c)<32 for c in v):
            raise HTTPException(422,"Invalid genre or TV show")
    if rule.media_type == "movies" and rule.shows and rule.logic is None:
        raise HTTPException(422,"Show filters only apply to episodes")
    if rule.logic is not None:
        validate_logic(rule.logic)

def _normalize(v):
    return str(v or "").casefold().strip()

def _metadata_values(raw):
    """Plex Labels and Collections/Tags are stored as JSON string arrays."""
    if not raw:
        return set()
    try:
        values=json.loads(raw) if isinstance(raw,str) else raw
    except (ValueError,TypeError):
        return set()
    return {_normalize(v) for v in values if isinstance(v,str) and v.strip()} if isinstance(values,list) else set()



LOGIC_FIELDS = {"genre", "year", "label", "tag", "show", "kind"}
LOGIC_OPERATORS = {"is", "is_not", "is_only", "is_only_not", "gte", "lte"}
LOGIC_GROUPS = {"all", "any", "none"}

def validate_logic(node):
    """Validate bounded JSON expression trees before saving or evaluating."""
    total = [0]
    def visit(expr, depth):
        total[0] += 1
        if total[0] > 100 or depth > 5 or not isinstance(expr, dict):
            raise HTTPException(422, "Rule expression is too large or too deeply nested")
        if "group" in expr:
            if set(expr) != {"group", "children"} or expr["group"] not in LOGIC_GROUPS:
                raise HTTPException(422, "Invalid logical group")
            children = expr["children"]
            if not isinstance(children, list) or not 1 <= len(children) <= 30:
                raise HTTPException(422, "Logical groups must contain at least one rule")
            for child in children:
                visit(child, depth+1)
        else:
            if set(expr) != {"field", "operator", "value"}:
                raise HTTPException(422, "Invalid metadata condition")
            field, op, value = expr["field"], expr["operator"], expr["value"]
            if field not in LOGIC_FIELDS or op not in LOGIC_OPERATORS:
                raise HTTPException(422, "Invalid metadata field or operator")
            if field == "year":
                if op not in ("is", "is_not", "gte", "lte") or type(value) is not int or not 1880 <= value <= 2200:
                    raise HTTPException(422, "Year must be a valid numeric year")
            elif field == "genre" and op in ("is_only", "is_only_not"):
                # New editor uses a single genre. Saved multi-select rules
                # remain readable, now with exact-set rather than subset semantics.
                selected = value if isinstance(value, list) else [value]
                if (not 1 <= len(selected) <= 30
                    or any(not isinstance(v, str) or not v.strip() or len(v) > 100
                           or any(ord(c) < 32 for c in v) for v in selected)):
                    raise HTTPException(422, "Select a valid genre for IS ONLY")
            else:
                if op not in ("is", "is_not") or not isinstance(value, str) or not value.strip() or len(value) > 100 or any(ord(c)<32 for c in value):
                    raise HTTPException(422, "Invalid metadata comparison")
                if field == "kind" and _normalize(value) not in ("movie", "episode"):
                    raise HTTPException(422, "Media type must be Movie or Episode")
    visit(node, 0)


def matches_logic(item, expr):
    """Evaluate a validated condition on metadata already indexed from Plex."""
    if "group" in expr:
        children = expr["children"]
        if expr["group"] == "all":
            # Adjacent IS ONLY genres in one AND group define the exact
            # required genre set. For example Comedy AND Drama means BOTH
            # genres, with no third genre. Other conditions are evaluated
            # normally, including nested OR/NOT groups and year filters.
            exclusive = [child for child in children
                         if child.get("field") == "genre"
                         and child.get("operator") == "is_only"]
            remaining = [child for child in children if child not in exclusive]
            if exclusive:
                expected = {_normalize(genre)
                            for cond in exclusive
                            for genre in (cond["value"] if isinstance(cond["value"], list)
                                          else [cond["value"]])}
                actual = {_normalize(v) for v in (item.get("genres") or "").split(",") if v.strip()}
                if not actual or actual != expected:
                    return False
            return all(matches_logic(item, child) for child in remaining)
        matches = [matches_logic(item, child) for child in children]
        if expr["group"] == "any":
            return any(matches)
        return not any(matches)  # none = NOT (A OR B)
    field, op, wanted = expr["field"], expr["operator"], expr["value"]
    if field == "year":
        year = int(item.get("year") or 0)
        if not year:
            return False
        return {"is": year == wanted, "is_not": year != wanted,
                "gte": year >= wanted, "lte": year <= wanted}[op]
    if field == "genre":
        values = {_normalize(x) for x in (item.get("genres") or "").split(",") if x.strip()}
        if op in ("is_only", "is_only_not"):
            selected = wanted if isinstance(wanted, list) else [wanted]
            expected = {_normalize(x) for x in selected}
            exact = bool(values) and values == expected
            # A standalone IS ONLY requires exactly the selected genre(s).
            # An AND group combines individual IS ONLY criteria above.
            # IS ONLY NOT negates that exact comparison, except on titles
            # with no genre metadata.
            if not values:
                return False
            return exact if op == "is_only" else not exact
    elif field == "label":
        values = _metadata_values(item.get("plex_labels"))
    elif field == "tag":
        values = _metadata_values(item.get("plex_tags"))
    elif field == "show":
        values = {_normalize(item.get("show_title"))} if item.get("show_title") else set()
    else:
        values = {_normalize(item.get("kind"))}
    present = _normalize(wanted) in values
    return present if op == "is" else not present

def matching_items(catalog, rule):
    validate_rules(rule)
    genres={_normalize(x) for x in rule.genres}
    shows={_normalize(x) for x in rule.shows}
    labels={_normalize(x) for x in rule.labels}
    tags={_normalize(x) for x in rule.tags}
    result=[]
    for item in catalog:
        if item["kind"] not in ("movie","episode"):
            continue
        if rule.media_type=="movies" and item["kind"]!="movie":
            continue
        if rule.media_type=="episodes" and item["kind"]!="episode":
            continue
        if rule.logic is not None:
            if not matches_logic(item, rule.logic):
                continue
            if not str(item.get("id","")).rsplit(":",1)[-1].isdigit():
                continue
            result.append(item)
            continue
        year=int(item.get("year") or 0)
        if rule.year_min is not None and year<rule.year_min:
            continue
        if rule.year_max is not None and (not year or year>rule.year_max):
            continue
        item_genres={_normalize(x) for x in (item.get("genres") or "").split(",") if x}
        metadata_match=(
            (not genres or bool(genres.intersection(item_genres))) and
            (not labels or bool(labels.intersection(_metadata_values(item.get("plex_labels"))))) and
            (not tags or bool(tags.intersection(_metadata_values(item.get("plex_tags")))))
        )
        if rule.media_type=="all" and shows:
            # Mixed channels have two independent sources: selected TV shows
            # and movies matching metadata filters. Requiring a movie to have
            # a show_title, or requiring a selected sitcom to share a movie's
            # genre, previously produced an empty pool.
            if item["kind"]=="episode":
                if _normalize(item.get("show_title")) not in shows:
                    continue
            elif not (genres or labels or tags) or not metadata_match:
                # A show-only selection must not add every Plex movie.
                continue
        else:
            if not metadata_match:
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

def _chronology(item):
    return (_normalize(item.get("show_title") or item.get("title")),
            int(item.get("season") or 0), int(item.get("episode") or 0),
            int(item.get("year") or 0), str(item["id"]))


def _stable_shuffle_key(seed, value):
    return hashlib.sha256((str(seed)+"|"+str(value)).encode()).digest()


def _ordered_tv_episodes(episodes, rule):
    """Maintain series chronology, even while movies are interleaved."""
    order=rule.episode_order or ("chronological" if rule.order=="chronological" else rule.order)
    if order=="newest":
        return sorted(episodes,key=lambda x:(-int(x.get("year") or 0),)+_chronology(x))
    if order=="shuffle":
        return sorted(episodes,key=lambda x:_stable_shuffle_key(rule.seed,x["id"]))
    # Round-robin between TV shows, retaining season/episode order within
    # each series. This prevents one long-running show monopolizing a mixed
    # channel while still never playing episode 2 before episode 1.
    series={}
    for item in episodes:
        series.setdefault(_normalize(item.get("show_title") or item.get("title")),[]).append(item)
    for group in series.values():
        group.sort(key=lambda x:(int(x.get("season") or 0),int(x.get("episode") or 0),
                                 str(x["id"])))
    result=[]
    names=sorted(series)
    while any(series.values()):
        for name in names:
            if series[name]:
                result.append(series[name].pop(0))
    return result


def _movie_blocks(movies, rule):
    """Return movie blocks; Plex collections stay together in release order.

    Plex movies may be in multiple collections. Assign each to the first
    alphabetically sorted collection to avoid duplicates and non-determinism.
    Standalone movies form their own blocks.
    """
    groups={}
    for item in movies:
        collections=sorted(_metadata_values(item.get("plex_collections")),key=str.casefold)
        collection=collections[0] if collections and rule.collection_order=="release" else None
        key=("collection",collection) if collection else ("movie",str(item["id"]))
        groups.setdefault(key,[]).append(item)
    blocks=list(groups.values())
    for block in blocks:
        block.sort(key=lambda x:(int(x.get("year") or 0) or 9999,
                                 _normalize(x.get("title")),str(x["id"])))
    order=rule.movie_order or {"chronological":"oldest","newest":"newest","shuffle":"shuffle"}[rule.order]
    if order=="shuffle":
        blocks.sort(key=lambda block:_stable_shuffle_key(
            rule.seed,"|".join(sorted(str(x["id"]) for x in block))))
    elif order=="newest":
        blocks.sort(key=lambda block:(-min(int(x.get("year") or 0) or 9999 for x in block),
                                      _normalize(block[0].get("title"))))
    else:
        blocks.sort(key=lambda block:(min(int(x.get("year") or 0) or 9999 for x in block),
                                      _normalize(block[0].get("title"))))
    return blocks


def ordered_programming(catalog, rule):
    """Order TV and movies independently and keep collection films contiguous.

    Older rules retain their previous ordering unless new options are used.
    New mixed channels alternate an episode and a movie (or an entire movie
    collection). TV series order is preserved across those insertions.
    """
    items=matching_items(catalog,rule)
    if rule.episode_order is None and rule.movie_order is None and rule.collection_order=="off":
        return items
    episodes=[x for x in items if x["kind"]=="episode"]
    movies=[x for x in items if x["kind"]=="movie"]
    tv=_ordered_tv_episodes(episodes,rule)
    blocks=_movie_blocks(movies,rule)
    if not tv:
        return [item for block in blocks for item in block]
    if not blocks:
        return tv
    result=[]
    ep_index=0
    for block in blocks:
        if ep_index<len(tv):
            result.append(tv[ep_index])
            ep_index+=1
        result.extend(block)
    result.extend(tv[ep_index:])
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
    return [(item,duration_minutes(item,rule)) for item in ordered_programming(catalog,rule)]

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
