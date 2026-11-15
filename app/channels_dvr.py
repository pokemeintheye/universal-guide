"""Read-only Channels DVR M3U discovery for user-supplied TVE stations.

Configure CHANNELS_DVR_URL to the Channels server's LAN base URL. This module
does not request Philo credentials or expose arbitrary stream URLs supplied
by a client.
"""
import ipaddress
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/channels-dvr", tags=["channels-dvr"])
CHANNELS_DVR_URL = os.getenv("CHANNELS_DVR_URL", "").strip().rstrip("/")
# Channel numbers supplied by the owner; stream existence is verified in M3U.
TVE_STATIONS = {
    "6043": "A&E", "6044": "History", "6045": "FYI", "6046": "Lifetime",
    "6085": "Sundance TV", "6086": "AMC", "6087": "BBC America",
    "6088": "IFC", "6089": "We TV", "6100": "OWN",
    "6102": "Food Network", "6103": "Animal Planet", "6104": "HGTV",
    "6105": "TLC", "6106": "Travel Channel", "6107": "Investigation Discovery",
    "6108": "Magnolia Network", "6109": "Cooking Channel",
    "6110": "Science Channel", "6111": "American Heroes Channel",
    "6112": "Destination America", "6113": "Discovery Life",
    "6114": "MotorTrend",
}
TEST_CHANNEL = "6104"
M3U_PATH = "/devices/ANY/channels.m3u?format=hls&codec=copy"


def _base():
    parsed = urlparse(CHANNELS_DVR_URL)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query:
        raise HTTPException(503, "Set CHANNELS_DVR_URL to a trusted local http://IP:8089 address")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise HTTPException(503, "Use a literal private LAN IPv4/IPv6 address for CHANNELS_DVR_URL")
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise HTTPException(503, "CHANNELS_DVR_URL must point to a trusted private LAN server")
    return CHANNELS_DVR_URL


def _channel_stream(playlist, base, number):
    lines = playlist.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        # Match the exported channel number, not arbitrary title text.
        tags = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
        values = (tags.get("tvg-chno"), tags.get("channel-number"), tags.get("tvg-id"))
        if number not in values and not re.search(rf'(?<!\d){re.escape(number)}(?!\d)', tags.get("tvg-id", "")):
            continue
        for candidate in lines[index + 1:]:
            candidate = candidate.strip()
            if not candidate or candidate.startswith("#"):
                continue
            url = urljoin(base + "/", candidate)
            parsed, source = urlparse(url), urlparse(base)
            # Avoid reflecting external/untrusted endpoints to the Apple TV.
            if (parsed.scheme != source.scheme or parsed.hostname != source.hostname or
                    parsed.port != source.port or parsed.username or parsed.password):
                raise HTTPException(502, "Channels exported a stream outside its configured host")
            return url
    return None


def _playlist():
    base = _base()
    try:
        response = requests.get(base + M3U_PATH, timeout=(4, 15), allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 3_000_000:
            raise HTTPException(502, "Channels DVR playlist is unexpectedly large")
        return base, response.text
    except requests.RequestException as exc:
        raise HTTPException(502, "Unable to retrieve the Channels DVR M3U playlist") from exc


def _resolve(number):
    if number not in TVE_STATIONS:
        raise HTTPException(404, "This channel is not in the configured TVE station list")
    base, playlist = _playlist()
    stream = _channel_stream(playlist, base, number)
    if not stream:
        raise HTTPException(404, f"Channel {number} is not in the current Channels DVR M3U export")
    return {"channel_number": number, "name": TVE_STATIONS[number],
            "stream_url": stream, "source": "channels-dvr", "playback_tested": False}


@router.get("/channels")
def channels():
    """List configured channels present in the current M3U export."""
    base, playlist = _playlist()
    available = []
    for number, name in TVE_STATIONS.items():
        if _channel_stream(playlist, base, number):
            available.append({"channel_number": number, "name": name})
    return {"channels": available, "source": "channels-dvr",
            "playback_tested": False}


@router.get("/live/{number}")
def live_channel(number: str):
    """Resolve a listed TVE channel without starting a recording."""
    return _resolve(number)


@router.get("/test/hgtv")
def test_hgtv():
    """Backward-compatible HGTV discovery endpoint."""
    return _resolve(TEST_CHANNEL)
