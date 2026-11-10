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


# Broadcast networks append flags that are not part of program names.
BROADCAST_FLAGS=r"(?:new|live|repeat|rerun|premiere|series premiere|season premiere|season finale|finale)"

def clean_artwork_title(value):
    title=str(value or "").strip()
    marker=rf"\s*[\[(]\s*{BROADCAST_FLAGS}\s*[\])]\s*"
    while True:
        cleaned=re.sub(rf"^{marker}|{marker}$"," ",title,flags=re.I).strip()
        if cleaned==title or not cleaned:
            return title
        title=cleaned

def normalize_title(value):
    value=unicodedata.normalize("NFKD",clean_artwork_title(value))
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
    title=clean_artwork_title(title)
    if len(title)<2:return None
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


TMDB_API="https://api.themoviedb.org/3/search/movie"
TMDB_POSTER="https://image.tmdb.org/t/p/w342"
def find_tmdb_movie_artwork(title,year=None,api_key=None):
    """Conservative movie-only match, using a server-side TMDB v3 API key.

    Reject ambiguous same-title results unless EPG supplies a release year.
    Keep all credentials out of responses and image URLs.
    """
    title=clean_artwork_title(title)
    if not api_key or not (2<=len(title)<=120):
        return None
    params={"query":title,"language":"en-US","include_adult":"false","page":1,
            "api_key":api_key}
    if isinstance(year,int) and 1880<=year<=2100:
        params["year"]=year
    response=requests.get(TMDB_API,params=params,
        headers={"Accept":"application/json"},
        timeout=(3,7),allow_redirects=False)
    try:
        response.raise_for_status()
        data=response.json()
    finally:
        response.close()
    options=[]
    for movie in (data.get("results",[]) if isinstance(data,dict) else [])[:20]:
        if not isinstance(movie,dict) or movie.get("adult"):continue
        names=(movie.get("title"),movie.get("original_title"))
        if not any(normalize_title(name)==normalize_title(title) for name in names):
            continue
        poster=movie.get("poster_path")
        if not isinstance(poster,str) or not re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)",poster,re.I):
            continue
        release=movie.get("release_date") or ""
        movie_year=int(release[:4]) if re.match(r"^\d{4}-",release) else None
        if isinstance(year,int) and 1880<=year<=2100 and movie_year!=year:
            continue
        movie_id=movie.get("id")
        if not isinstance(movie_id,int) or movie_id<=0:continue
        options.append((movie,movie_id,poster))
    # No year? Avoid confusing remakes sharing the same name.
    if len(options)!=1:return None
    movie,movie_id,poster=options[0]
    return {"image_url":TMDB_POSTER+poster,
        "credit_url":"https://www.themoviedb.org/movie/"+str(movie_id),
        "match":"movie","source":"TMDB"}


TMDB_TV_API="https://api.themoviedb.org/3/search/tv"


def find_tmdb_tv_artwork(title,api_key=None):
    """Find a series poster only when TMDB has one unambiguous exact-title hit.

    Called after TVmaze has no valid artwork. Never accepts fuzzy candidates,
    and avoids randomly choosing between series sharing the same title.
    """
    title=clean_artwork_title(title)
    if not api_key or not (2<=len(title)<=120):
        return None
    response=requests.get(TMDB_TV_API,params={
        "query":title,"language":"en-US","include_adult":"false","page":1,
        "api_key":api_key
    },headers={"Accept":"application/json"},timeout=(3,7),
        allow_redirects=False)
    try:
        response.raise_for_status()
        data=response.json()
    finally:
        response.close()
    candidates=[]
    for show in (data.get("results",[]) if isinstance(data,dict) else [])[:20]:
        if not isinstance(show,dict) or show.get("adult"):continue
        if not any(normalize_title(name)==normalize_title(title)
                   for name in (show.get("name"),show.get("original_name"))):
            continue
        poster=show.get("poster_path")
        if not isinstance(poster,str) or not re.fullmatch(
                r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)",poster,re.I):
            continue
        show_id=show.get("id")
        if not isinstance(show_id,int) or show_id<=0:continue
        candidates.append((show_id,poster))
    if len(candidates)!=1:return None
    show_id,poster=candidates[0]
    return {"image_url":TMDB_POSTER+poster,
            "credit_url":"https://www.themoviedb.org/tv/"+str(show_id),
            "match":"series","source":"TMDB"}


def safe_tmdb_image_url(url):
    return bool(isinstance(url,str) and re.fullmatch(
        r"https://image\.tmdb\.org/t/p/w342/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)",url,re.I))
