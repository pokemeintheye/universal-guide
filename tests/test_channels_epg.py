"""Channels DVR XMLTV mapping and playback-source guide priority."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import channels_epg as epg
from app import channel_management as cm
from app import main

DOC = b"""<?xml version="1.0" encoding="utf-8"?>
<tv>
 <channel id="station.discovery"><display-name>Discovery</display-name><lcn>6101</lcn></channel>
 <channel id="station.hgtv"><display-name>HGTV</display-name><lcn>6104</lcn></channel>
 <channel id="station.other"><display-name>Other</display-name><lcn>6111</lcn></channel>
 <programme channel="station.discovery" start="20261115120000 -0500" stop="20261115130000 -0500">
   <title>Discovery Actual Listing</title><sub-title>Deep Ocean</sub-title>
   <episode-num system="xmltv_ns">1.2.</episode-num><desc>Channels DVR data</desc>
 </programme>
 <programme channel="station.hgtv" start="20261115120000 -0500" stop="20261115130000 -0500">
   <title>HGTV Channels Title</title></programme>
 <programme channel="station.other" start="20261115120000 -0500" stop="20261115130000 -0500">
   <title>Incorrect Station Program</title></programme>
</tv>"""


class GuidePriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original_db, original_store = main.DB, cm.STORE
        main.DB = Path(self.tmp.name) / "guide.db"
        cm.STORE = Path(self.tmp.name) / "channel.db"
        self.addCleanup(setattr, main, "DB", original_db)
        self.addCleanup(setattr, cm, "STORE", original_store)
        main.initialize()
        epg._CACHE.update(at=0, duration=0, rows={}, error=None)
        with main.db() as c:
            c.execute("""INSERT INTO philo_listings(channel_id,channel_label,start,stop,title,subtitle,description)
                 VALUES (?,?,?,?,?,?,?)""", (
                "philo:discovery", "Discovery",
                "2026-11-15T17:00:00+00:00", "2026-11-15T18:00:00+00:00",
                "Wrong Philo Listing", "", ""))
            c.execute("""INSERT INTO philo_listings(channel_id,channel_label,start,stop,title,subtitle,description)
                 VALUES (?,?,?,?,?,?,?)""", (
                "philo:hgtv", "HGTV",
                "2026-11-15T17:00:00+00:00", "2026-11-15T18:00:00+00:00",
                "Philo HGTV Listing", "", ""))

    def test_xmltv_uses_exact_channel_number_and_local_timezone(self):
        xmltv = epg.parse_xmltv(DOC)
        mapped = epg._map_stations(xmltv, cm.configured_stations())
        self.assertEqual(mapped["6101"][0]["title"], "Discovery Actual Listing")
        self.assertEqual(mapped["6101"][0]["start"], "2026-11-15T17:00:00+00:00")
        self.assertEqual(mapped["6101"][0]["season"], 2)
        self.assertEqual(mapped["6101"][0]["episode"], 3)
        self.assertEqual(mapped["6104"][0]["title"], "HGTV Channels Title")
        self.assertNotIn("Incorrect Station Program", [x["title"] for x in mapped["6101"]])

    def test_philo_primary_retains_philo_guide_and_dvr_primary_uses_dvr(self):
        start = "2026-11-15T17:00:00+00:00"
        rows = epg._map_stations(epg.parse_xmltv(DOC), cm.configured_stations())
        with patch("app.channels_epg.schedule", return_value=(rows, None)), \
             patch("app.channels_dvr._playlist", return_value=("http://192.168.2.4:8089", "#EXTM3U")):
            result = main.guide(hours=1, start=start)
        discovery = next(c for c in result["channels"] if c["source"] == "philo" and c["label"] == "Discovery")
        self.assertEqual(discovery["programs"][0]["title"], "Discovery Actual Listing")
        self.assertEqual(discovery["guide_data_source"], "channels-dvr")
        class Request:
            headers = {}
        cm.save_channel("6101", cm.StationUpdate(
            number="6101",name="Discovery",guide_label="Discovery",
            priority="philo"), Request())
        with patch("app.channels_epg.schedule", return_value=(rows, None)), \
             patch("app.channels_dvr._playlist", return_value=("http://192.168.2.4:8089", "#EXTM3U")):
            result = main.guide(hours=1, start=start)
        discovery = next(c for c in result["channels"] if c["source"] == "philo" and c["label"] == "Discovery")
        self.assertEqual(discovery["programs"][0]["title"], "Wrong Philo Listing")

    def test_missing_philo_epg_uses_dvr_listing_without_inventing_schedule(self):
        from app import channels_dvr as cd
        class Request:
            headers = {}
        cm.save_channel("6101", cm.StationUpdate(
            number="6101",name="Discovery Channel",guide_label="Discovery",
            priority="channels-dvr"), Request())
        # A Channels-only row is used if Philo doesn't have the station.
        with main.db() as c:
            c.execute("DELETE FROM philo_listings WHERE channel_label='Discovery'")
        rows = epg._map_stations(epg.parse_xmltv(DOC), cm.configured_stations())
        playlist = """#EXTM3U
#EXTINF:-1 tvg-chno="6101",DISCOVERY
/devices/ANY/channels/6101/hls/master.m3u8
"""
        with (patch("app.channels_epg.schedule", return_value=(rows, None)),
              patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", playlist))):
            result = main.guide(hours=1, start="2026-11-15T17:00:00+00:00")
        # Discovery may still have a verified Philo handoff without any
        # actual Philo EPG rows. Preserve that channel and use DVR listings.
        discovery = next(c for c in result["channels"]
                         if c.get("number") == "6101" and
                         c["programs"] and
                         c.get("guide_data_source") == "channels-dvr")
        self.assertEqual(discovery["programs"][0]["title"], "Discovery Actual Listing")

    def test_export_failure_does_not_destroy_existing_philo_programs(self):
        with patch("app.channels_epg.schedule", return_value=({}, "offline")), \
             patch("app.channels_dvr._playlist", return_value=("http://192.168.2.4:8089", "#EXTM3U")):
            result = main.guide(hours=1, start="2026-11-15T17:00:00+00:00")
        discovery = next(c for c in result["channels"] if c["source"] == "philo" and c["label"] == "Discovery")
        self.assertEqual(discovery["programs"][0]["title"], "Wrong Philo Listing")


if __name__ == "__main__":
    unittest.main()
