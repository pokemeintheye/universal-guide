"""Channel-specific schedule updates for the Tablo DVR overlay."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app import main, tablo_live


class TestTabloOverlaySchedule(unittest.TestCase):
    def test_schedule_retains_previous_and_next_show(self):
        with TemporaryDirectory() as directory:
            with patch.object(main,"DB",Path(directory)/"guide.db"):
                main.initialize()
                now=datetime.now(timezone.utc)
                starts=[now-timedelta(minutes=50),now+timedelta(minutes=10)]
                stops=[now+timedelta(minutes=10),now+timedelta(minutes=70)]
                channel="/guide/channels/abc123"
                with main.db() as connection:
                    connection.execute(
                        "INSERT INTO tablo_channels(channel_id,number,network,callsign) "
                        "VALUES(?,?,?,?)",(channel,"12.1","NBC","WXII"))
                    for idx,(start,stop) in enumerate(zip(starts,stops)):
                        connection.execute(
                            "INSERT INTO tablo_listings(id,channel_id,channel_label,start,stop,"
                            "title,subtitle) VALUES(?,?,?,?,?,?,?)",
                            (f"program{idx}",channel,"12.1",start.isoformat(),
                             stop.isoformat(),f"Show {idx+1}","Episode"))
                with patch.object(tablo_live,"_settings",
                                  return_value=("http://127.0.0.1:8885",main.DB)):
                    result=tablo_live.channel_schedule(channel)
                self.assertEqual(result["channel"]["number"],"12.1")
                self.assertEqual([p["title"] for p in result["programs"]],
                                 ["Show 1","Show 2"])
                self.assertEqual(result["programs"][0]["start"],starts[0].isoformat())

    def test_channel_id_is_validated(self):
        with self.assertRaises(HTTPException) as context:
            tablo_live.channel_schedule("../../other")
        self.assertEqual(context.exception.status_code,400)


if __name__=="__main__":
    unittest.main()
