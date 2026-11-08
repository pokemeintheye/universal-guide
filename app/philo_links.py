"""Curated, verified Philo Universal Links; not an unofficial Philo API.

Catalog entries are manually confirmed on an iPhone. No title-to-ID guessing.
Channel and episode matches stay separate so a program never opens the wrong show.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG=Path(__file__).with_name("philo_links.json")


def normalize(value):
    return re.sub(r"[^a-z0-9]+"," ",str(value or "").casefold()).strip()


def _url(identifier,kind):
    # A caller cannot supply arbitrary links; only curated HTTPS IDs are accepted.
    if not isinstance(identifier,str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}",identifier):
        return None
    if kind=="channel":
        return "https://www.philo.com/player/channel/"+identifier
    if kind=="show":
        return "https://www.philo.com/player/show/"+identifier
    return None


@lru_cache(maxsize=1)
def catalog():
    try:
        data=json.loads(CATALOG.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}


def channel_catalog():
    """Known channel destinations only; no subscription/EPG availability implied."""
    result=[]
    for item in catalog().get("channels",[]):
        url=_url(item.get("id"),"channel")
        if not url or not item.get("name"):continue
        result.append({"name":item["name"],"url":url,
                       "verification":item.get("verification","public-listed")})
    return result


def links_for(channel_label,title,subtitle=None,season=None,episode=None):
    """Return only explicit, verified links for the correct guide record."""
    data=catalog()
    found={}
    channel=normalize(channel_label)
    show=normalize(title)
    for item in data.get("channels",[]):
        if channel==normalize(item.get("name")):
            url=_url(item.get("id"),"channel")
            if url:
                found["channel"]={"url":url,"label":"Open "+item["name"]+" in Philo",
                                  "autoplay":False,"level":"channel",
                                  "verification":item.get("verification","public-listed")}
            break
    for item in data.get("shows",[]):
        if show!=normalize(item.get("name")):continue
        url=_url(item.get("id"),"show")
        if url:
            found["show"]={"url":url,"label":"Open "+item["name"]+" in Philo",
                           "autoplay":False,"level":"series",
                           "verification":item.get("verification","public-listed")}
        for ep in item.get("episodes",[]):
            expected_title=normalize(ep.get("subtitle"))
            if not expected_title or normalize(subtitle)!=expected_title:
                continue
            # Exact episode deep links demand all three matching fields.
            # Subtitles like "Pilot" recur across seasons and some EPG sources
            # omit episode numbers; never guess in either case.
            if season is None or episode is None:continue
            if str(season)!=str(ep.get("season")):continue
            if str(episode)!=str(ep.get("episode")):continue
            code=ep.get("id")
            if url and isinstance(code,str) and re.fullmatch(r"[A-Za-z0-9_-]{8,100}",code):
                found["episode"]={"url":url+"?episode="+code,
                     "label":"Open this episode in Philo","autoplay":False,"level":"episode",
                     "verification":ep.get("verification",item.get("verification","public-listed"))}
        break
    return found
