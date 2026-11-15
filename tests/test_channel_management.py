"""Persistent, runtime-editable channel mappings and Plex virtual schedules."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app import channel_management as cm
from app import channels_dvr as cd
from app import main

M3U = """#EXTM3U
#EXTINF:-1 tvg-chno="6104",HGTV
/devices/ANY/channels/6104/hls/master.m3u8
#EXTINF:-1 tvg-chno="6101",DISCOVERY
/devices/ANY/channels/6101/hls/master.m3u8
#EXTINF:-1 tvg-chno="6222",New Network
/devices/ANY/channels/6222/hls/master.m3u8
"""


class Request:
    headers = {}


class ChannelManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_db = cm.STORE
        cm.STORE = Path(self.tmp.name) / "channels.db"
        self.addCleanup(setattr, cm, "STORE", self.original_db)
        self.original_main_db = main.DB
        main.DB = Path(self.tmp.name) / "guide.db"
        self.addCleanup(setattr, main, "DB", self.original_main_db)
        with main.db() as c:
            c.execute("CREATE TABLE media(id TEXT PRIMARY KEY,kind TEXT,title TEXT,year INT,normalized TEXT,show_title TEXT,season INT,episode INT,guids TEXT)")
            c.execute("""INSERT INTO media(id,kind,title,year,normalized,show_title,season,episode)
                VALUES ('movie:100','Movie Alpha', 'Movie Alpha', 2020, '',NULL,NULL,NULL)""".replace("'Movie Alpha', 'Movie Alpha'","'movie', 'Movie Alpha'"))
            c.execute("""INSERT INTO media(id,kind,title,year,normalized,show_title,season,episode)
                VALUES ('episode:200','episode','Episode One',2021,'','Example Show',1,1)""")

    def test_default_channel_6101_and_priority_switch(self):
        assert cm.configured_stations()["6101"]["priority"] == "channels-dvr"
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            self.assertIn("tve-6101", [x["playback_id"] for x in cd.live_guide_options()["options"]])
        body = cm.StationUpdate(number="6101", name="Discovery", guide_label="Discovery",
                                priority="philo", enabled=True)
        cm.save_channel("6101", body, Request())
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            self.assertNotIn("tve-6101", [x["playback_id"] for x in cd.live_guide_options()["options"]])
            with self.assertRaises(HTTPException):
                cd.live_stream("tve-6101")
        self.assertEqual(cm.configured_stations()["6101"]["priority"], "philo")
        cm.reset_channel("6101", Request())
        self.assertEqual(cm.configured_stations()["6101"]["priority"], "channels-dvr")

    def test_new_station_is_added_from_real_export_without_code_change(self):
        body = cm.StationUpdate(number="6222", name="New Network",
                                guide_label="New Network", priority="channels-dvr")
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            cm.save_channel("6222", body, Request())
            self.assertEqual(cd.live_stream("tve-6222")["channel_number"], "6222")
            self.assertIn("tve-6222", [x["playback_id"] for x in cd.live_guide_options()["options"]])
        self.assertEqual(cm.configured_stations()["6222"]["name"], "New Network")

    def test_unexported_station_is_not_allowed(self):
        body = cm.StationUpdate(number="6999", name="Missing",guide_label="Missing")
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            with self.assertRaises(HTTPException):
                cm.save_channel("6999", body, Request())

    def test_virtual_repeating_schedule_contains_plex_keys(self):
        body = cm.VirtualUpdate(name="Movie Night", items=[
            cm.VirtualItem(rating_key=100, duration_minutes=100),
            cm.VirtualItem(rating_key=200, duration_minutes=30)])
        created = cm.create_virtual(body, Request())
        self.assertEqual(len(cm.virtual_definitions()), 1)
        start = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        rows = cm.virtual_guide(start, start + timedelta(hours=6))
        self.assertEqual(rows[0]["source"], "virtual")
        self.assertEqual(rows[0]["label"], "Movie Night")
        self.assertGreaterEqual(len(rows[0]["programs"]), 4)
        self.assertEqual({x["plex"]["rating_key"] for x in rows[0]["programs"]},
                         {"100", "200"})
        self.assertTrue(all(x["start"] < x["stop"] for x in rows[0]["programs"]))
        self.assertTrue(all(x["program_kind"] in ("episode", "movie") for x in rows[0]["programs"]))
        cm.delete_virtual(created["id"], Request())
        self.assertEqual(cm.virtual_definitions(), [])


if __name__ == "__main__":
    unittest.main()
