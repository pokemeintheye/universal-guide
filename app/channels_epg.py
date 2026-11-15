"""Cached Channels DVR XMLTV listings for TVE stations.

The user-configured Channels DVR server provides /devices/ANY/guide/xmltv.
Only match exact channel numbers (lcn/display-name) or a unique station name.
No guide entries from other stations are substituted. Fail closed to the
existing Philo guide if the DVR guide cannot be retrieved or matched.
"""
import io
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from app.channels_dvr import _base

PATH = "/devices/ANY/guide/xmltv"
MAX_BYTES = 35_000_000
CACHE_SECONDS = max(120, min(3600, int(os.getenv("CHANNELS_EPG_CACHE_SECONDS", "600"))))
_LOCK = threading.Lock()
_CACHE = {"at": 0, "duration": 0, "rows": {}, "error": None}
DATE = re.compile(r"^(\d{14})(?:\s*([+-]\d{4}))?")
NUMBER = re.compile(r"^\d{1,6}$")


def _date(raw):
    match = DATE.match(raw or "")
    if not match:
        return None
    stamp = match.group(1)
    try:
        result = datetime.strptime(stamp, "%Y%m%d%H%M%S")
        offset = match.group(2)
        if offset:
            direction = 1 if offset[0] == "+" else -1
            minutes = direction * (int(offset[1:3]) * 60 + int(offset[3:]))
            result = result.replace(tzinfo=timezone(timedelta(minutes=minutes)))
        else:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except ValueError:
        return None


def _norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _first(node, tag):
    item = node.find(tag)
    return (item.text or "").strip() if item is not None else ""


def _fetch(duration):
    # This URL is constructed from the already-validated LAN-only server
    # configuration, never from user input or an XML field.
    base = _base()
    response = requests.get(base + PATH, params={"duration": duration},
                            stream=True, allow_redirects=False, timeout=(3, 12))
    try:
        response.raise_for_status()
        size = 0
        chunks = []
        for part in response.iter_content(chunk_size=131072):
            size += len(part)
            if size > MAX_BYTES:
                raise ValueError("Channels DVR guide XMLTV exceeds safety limit")
            chunks.append(part)
        return b"".join(chunks)
    finally:
        response.close()


def parse_xmltv(data):
    root = ET.parse(io.BytesIO(data)).getroot()
    if root.tag != "tv":
        raise ValueError("Unexpected Channels DVR guide document")
    # IDs in XMLTV need not be channel numbers; use explicit lcn or
    # display-name before unique label matching.
    stations = {}
    label_to_ids = {}
    for node in root.findall("channel"):
        identifier = node.get("id")
        if not identifier:
            continue
        names = [x.text.strip() for x in node.findall("display-name") if x.text]
        lcns = [x.text.strip() for x in node.findall("lcn") if x.text]
        numbers = set(n for n in lcns + names if NUMBER.fullmatch(n))
        labels = set(_norm(n) for n in names if n and not NUMBER.fullmatch(n))
        stations[identifier] = {"numbers": numbers, "labels": labels}
        for label in labels:
            label_to_ids.setdefault(label, set()).add(identifier)
    entries = {}
    for p in root.findall("programme"):
        identifier = p.get("channel")
        if identifier not in stations:
            continue
        start, stop = _date(p.get("start")), _date(p.get("stop"))
        if not start or not stop or stop <= start:
            continue
        title = _first(p, "title")
        if not title:
            continue
        subtitle = _first(p, "sub-title")
        year_text = _first(p, "date")
        year = int(year_text[:4]) if re.fullmatch(r"\d{4}", year_text[:4]) else None
        season = episode = None
        for node in p.findall("episode-num"):
            value = (node.text or "").strip()
            if node.get("system") == "xmltv_ns":
                parts = value.split(".")
                try:
                    if len(parts) > 1 and parts[0].isdigit() and parts[1].isdigit():
                        season, episode = int(parts[0]) + 1, int(parts[1]) + 1
                except (ValueError, IndexError):
                    pass
                break
        category = " ".join((x.text or "") for x in p.findall("category")).lower()
        kind = "movie" if "movie" in category or "film" in category else "series"
        entries.setdefault(identifier, []).append({
            "title": title, "subtitle": subtitle,
            "start": start.isoformat(), "stop": stop.isoformat(),
            "description": _first(p, "desc")[:1200], "year": year,
            "season": season, "episode": episode, "program_kind": kind,
        })
    return stations, label_to_ids, entries


def _map_stations(xmltv, configured):
    stations, label_to_ids, entries = xmltv
    resolved = {}
    for number, settings in configured.items():
        if not settings["enabled"] or settings["priority"] != "channels-dvr":
            continue
        explicit = [sid for sid, item in stations.items() if number in item["numbers"]]
        identifier = None
        if len(explicit) == 1:
            identifier = explicit[0]
        elif not explicit:
            # Only use a name when the XMLTV match is unambiguous.
            aliases = settings["aliases"] + [settings["name"]]
            ids = set()
            for alias in aliases:
                ids.update(label_to_ids.get(_norm(alias), set()))
            if len(ids) == 1:
                identifier = next(iter(ids))
        if identifier:
            resolved[number] = entries.get(identifier, [])
    return resolved


def schedule(start, end, configured):
    """Return {channel_number: [programs]}, plus a diagnostic error.

    Cache up to an hour of exported guide data; no private-network request
    occurs if no station is configured for Channels DVR.
    """
    active = {n: s for n, s in configured.items()
              if s["enabled"] and s["priority"] == "channels-dvr"}
    if not active:
        return {}, None
    now = time.monotonic()
    duration = max(3600, min(86400, int((end - datetime.now(timezone.utc)).total_seconds()) + 1800))
    with _LOCK:
        if _CACHE["at"] and now - _CACHE["at"] < CACHE_SECONDS and _CACHE["duration"] >= duration:
            xmltv, err = _CACHE["rows"], _CACHE["error"]
        else:
            try:
                xmltv = parse_xmltv(_fetch(duration))
                err = None
            except (requests.RequestException, ValueError, ET.ParseError, OSError) as exc:
                xmltv, err = None, "Channels DVR EPG unavailable: " + type(exc).__name__
            _CACHE.update(at=now, duration=duration, rows=xmltv, error=err)
    if xmltv is None:
        return {}, err
    result = _map_stations(xmltv, active)
    a, b = start.isoformat(), end.isoformat()
    return {number: [dict(p) for p in programs if p["start"] < b and p["stop"] > a]
            for number, programs in result.items()}, None
