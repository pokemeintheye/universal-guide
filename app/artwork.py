"""Conservative, cached-TV-guide artwork resolution using TVmaze's free API.

TVmaze data/images are CC BY-SA and require attribution in the UI.
The show title must match exactly after simple normalization; do not attach
fuzzy results to unrelated programs. No Plex tokens leave this module.
"""
import re
import unicodedata
from urllib.parse import urlparse
import requests

TVMAZE="https://api.tvmaze.com"
HEADERS={"User-Agent":"UniversalGuide/0.1 (personal local television guide)",
         "Accept":"application/json"}


def normalize_title(value):
    value=unicodedata.normalize("NFKD",str(value or ""))
    value="".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+"," ",value.casefold()).strip()


def safe_image_url(url):
    if not isinstance(url,str):return None
    parsed=urlparse(url)
    if parsed.scheme!="https" or parsed.hostname!="static.tvmaze.com":
        return None
    if parsed.username or parsed.password or parsed.port or parsed.fragment:
        return None
    if not re.fullmatch(r"/uploads/images/(?:medium_portrait|original|medium_landscape|medium_original|original_landscape|medium)[^?#]*\.(?:jpg|jpeg|png|webp)",parsed.path,re.I):
        # TVmaze often uses paths like /uploads/images/medium_landscape/123/456.jpg.
        return None
    return url


def _get(path,params=None):
    response=requests.get(TVMAZE+path,headers=HEADERS,params=params,
                          timeout=(3,7),allow_redirects=False)
    try:
        if response.status_code==429:raise RuntimeError("Artwork provider throttled")
        if response.status_code==404:return None
        response.raise_for_status()
        return response.json()
    finally:
        response.close()


def find_tvmaze_artwork(title,season=None,episode=None,subtitle=None):
    """Return image, attribution, and match level or None for no reliable hit.

    Prefer compact artwork for faster cached image downloads; fall back to original. Only select a TVmaze
    show with an exact normalized title match, not a fuzzy suggestion.
    """
    if not isinstance(title,str) or not (2<=len(title.strip())<=120):
        return None
    search=_get("/search/shows",{"q":title})
    if not isinstance(search,list):return None
    matches=[item.get("show") for item in search[:12] if isinstance(item,dict)]
    shows=[sh for sh in matches if isinstance(sh,dict)
           and normalize_title(sh.get("name"))==normalize_title(title)]
    if not shows:return None
    # Match by episode number for duplicate series names when possible.
    for show in shows[:3]:
        show_id=show.get("id")
        if not isinstance(show_id,int) or show_id<1:continue
        show_url=show.get("url")
        if not isinstance(show_url,str) or not show_url.startswith("https://www.tvmaze.com/shows/"):
            show_url="https://www.tvmaze.com/shows/"+str(show_id)
        if isinstance(season,int) and isinstance(episode,int) and season>=0 and episode>0:
            detail=_get("/shows/"+str(show_id)+"/episodebynumber",
                        {"season":season,"number":episode})
            if isinstance(detail,dict):
                # Episode-number match is specific enough even if EPG subtitle
                # is abbreviated or missing.
                image=detail.get("image")
                url=safe_image_url(image.get("medium") or image.get("original")) if isinstance(image,dict) else None
                if url:
                    return {"image_url":url,"credit_url":show_url,"match":"episode"}
        # Fallback: show image rather than incorrectly suggesting another episode.
        image=show.get("image")
        url=safe_image_url(image.get("medium") or image.get("original")) if isinstance(image,dict) else None
        if url:
            return {"image_url":url,"credit_url":show_url,"match":"series"}
    return None
