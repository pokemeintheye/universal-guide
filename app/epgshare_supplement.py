"""Supplement missing Philo schedule channels using published US2 station IDs.

This is EPG discovery, NOT Philo player-link discovery or subscription verification.
Only explicitly named US/East source IDs are admitted. Never match fuzzy titles.
"""
import gzip
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

URL="https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"
MAX_COMPRESSED=20*1024*1024
MAX_XML=150*1024*1024
# Exact station IDs in the publicly published epg_ripper_US2.txt listing.
# No West/Pacific, Canadian, or generic partial-name matching is permitted.
STATIONS={
    "A&E": "A.and.E.HD.East.us2",
    "AccuWeather Network": "AccuWeather.HD.us2",
    "AMC": "AMC.HD.us2",
    "American Heroes Channel": "American.Heroes.Channel.HD.us2",
    "Animal Planet": "Animal.Planet.HD.us2",
    "aspireTV": "ASPiRE.HD.us2",
    "AXS TV": "AXS.TV.us2",
    "BBC America": "BBC.America.HD.us2",
    "BET": "BET.HD.us2",
    "BET Her": "BET.Her.us2",
    "Catchy Comedy": "Catchy.Comedy.us2",
    "CLEO TV": "Cleo.TV.HD.us2",
    "CMT": "CMT.HD.us2",
    "Comedy Central": "Comedy.Central.HD.us2",
    "Cooking Channel": "Cooking.Channel.HD.us2",
    "Crime + Investigation": "Crime.and.Investigation.Network.HD.us2",
    "Dabl": "Dabl.us2",
    "Destination America": "Destination.America.HD.us2",
    "Discovery Channel": "Discovery.Channel.HD.us2",
    "Discovery Family": "Discovery.Family.Channel.HD.us2",
    "Discovery Life": "Discovery.Life.Channel.us2",
    "Discovery Turbo": "Discovery.Turbo.TV.us2",
    "EarthX": "EarthX.us2",
    "FETV": "Family.Entertainment.Television.us2",
    "Food Network": "Food.Network.HD.us2",
    "FYI": "FYI.Channel.HD.us2",
    "Game Show Network": "Game.Show.Network.HD.us2",
    "Great American Faith & Living": "Great.American.Faith.and.Living.us2",
    "Great American Family": "Great.American.Family.HD.us2",
    "Hallmark Channel": "Hallmark.Channel.HD.us2",
    "Hallmark Family": "Hallmark.Family.us2",
    "Hallmark Mystery": "Hallmark.Mystery.HD.us2",
    "Heroes & Icons": "Heroes.and.Icons.Network.SD.us2",
    "HGTV": "Home.and.Garden.Television.HD.us2",
    "HISTORY": "History.HD.us2",
    "HSN": "HSN.Home.Shopping.Network.HD.us2",
    "IFC": "IFC.HD.us2",
    "INSP": "INSP.HD.us2",
    "Investigation Discovery": "Investigation.Discovery.HD.us2",
    "Law&Crime": "Law.and.Crime.us2",
    "Lifetime": "Lifetime.HD.us2",
    "LMN": "LMN.HD.us2",
    "Logo": "Logo.us2",
    "Magnolia Network": "Magnolia.Network.HD.us2",
    "MeTV": "Me.TV.Network.us2",
    "MeTV Toons": "MeTV.Toons.us2",
    "MeTV+": "MeTV.Plus.us2",
    "Military History Channel": "Military.History.Channel.us2",
    "MTV": "MTV.-.Music.Television.HD.us2",
    "MTV Classic": "MTV.Classic.us2",
    "MTV Live": "MTVLIVE.us2",
    "MTV2": "MTV2:.Music.Television.HD.us2",
    "Nick Jr.": "Nick.Jr.HD.us2",
    "Nickelodeon": "Nickelodeon.HD.us2",
    "Nicktoons": "Nicktoons.us2",
    "Oprah Winfrey Network": "Oprah.Winfrey.Network.HD.us2",
    "Paramount Network": "Paramount.Network.HD.us2",
    "Pop TV": "POP.HD.us2",
    "QVC": "QVC.HD.us2",
    "Science Channel": "Science.Channel.HD.us2",
    "Smithsonian Channel": "Smithsonian.HD.Network.us2",
    "Start TV": "Start.TV.Network.us2",
    "Story Television": "Story.us2",
    "Sundance TV": "SundanceTV.HD.us2",
    "Tastemade": "Tastemade.us2",
    "TeenNick": "Teen.Nick.us2",
    "TLC": "TLC.HD.(US).us2",
    "Travel Channel": "The.Travel.Channel.HD.us2",
    "TV Land": "TV.Land.HD.us2",
    "TV One": "TV.ONE.HD.us2",
    "UPtv": "UPtv.us2",
    "VH1": "VH1.HD.us2",
    "Vice": "Vice.HD.us2",
    "We TV": "WE.tv.HD.us2"
}

def fetch_missing(primary_channels, allowed_channels, xml_date, xmltv_kind, xmltv_episode):
    """One bounded download to supplement missing channels; never replace primary."""
    wanted={name:identifier for name,identifier in STATIONS.items()
            if name in allowed_channels and name not in primary_channels}
    result={"source":"EPGShare US2","requested":len(wanted),"matched":0,
            "added":0,"programs":0,"missing":[],"error":""}
    if not wanted:return {},result
    try:
        with requests.get(URL,timeout=(8,65),stream=True) as response:
            response.raise_for_status()
            compressed=bytearray()
            for chunk in response.iter_content(chunk_size=128*1024):
                compressed.extend(chunk)
                if len(compressed)>MAX_COMPRESSED:
                    raise ValueError("compressed EPG size limit exceeded")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            xml=stream.read(MAX_XML+1)
        if len(xml)>MAX_XML:raise ValueError("EPG XML size limit exceeded")
        root=ET.fromstring(xml)
        available={node.get("id") for node in root.findall("channel")}
        active={identifier:name for name,identifier in wanted.items() if identifier in available}
        result["matched"]=len(active)
        result["missing"]=sorted(name for name,identifier in wanted.items() if identifier not in available)[:30]
        by_channel={name:[] for name in active.values()}
        now=datetime.now(timezone.utc).isoformat()
        cutoff=(datetime.now(timezone.utc)+timedelta(days=4)).isoformat()
        for node in root.findall("programme"):
            name=active.get(node.get("channel"))
            if name is None:continue
            start=xml_date(node.get("start"))
            stop=xml_date(node.get("stop"))
            if not start or not stop or stop<=now or start>=cutoff:continue
            get=lambda tag:(node.findtext(tag) or "").strip()
            title=get("title")
            if not title:continue
            raw_year=get("date")
            year=int(raw_year[:4]) if re.match(r"^\\d{4}",raw_year) else 0
            by_channel[name].append((
                name,start,stop,title,get("sub-title"),year,
                get("desc")[:600],xmltv_kind(node),*xmltv_episode(node)))
        populated={name:rows for name,rows in by_channel.items() if rows}
        result["added"]=len(populated)
        result["programs"]=sum(map(len,populated.values()))
        if not populated:result["error"]="No usable future schedule data found for missing Philo channels"
        return populated,result
    except (requests.RequestException,OSError,ET.ParseError,ValueError,OverflowError) as exc:
        result["error"]=type(exc).__name__+": "+str(exc)[:150]
        return {},result
