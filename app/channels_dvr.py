"""Read-only Channels DVR M3U discovery for a single verified test channel.

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


@router.get("/test/hgtv")
def test_hgtv():
    """Discover the validated HGTV channel URL without starting playback."""
    base = _base()
    try:
        response = requests.get(base + M3U_PATH, timeout=(4, 15), allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 3_000_000:
            raise HTTPException(502, "Channels DVR playlist is unexpectedly large")
        stream = _channel_stream(response.text, base, TEST_CHANNEL)
    except requests.RequestException as exc:
        raise HTTPException(502, "Unable to retrieve the Channels DVR M3U playlist") from exc
    if not stream:
        raise HTTPException(404, "Channel 6104 was not found in the Channels DVR M3U export")
    return {"channel_number": TEST_CHANNEL, "name": "HGTV", "stream_url": stream,
            "source": "channels-dvr", "playback_tested": False}
