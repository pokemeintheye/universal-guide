"""The API guide window starts at the same :00/:30 as tvOS ruler."""
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from app import main,channel_management as cm

class HalfHourAnchorTests(unittest.TestCase):
    def test_api_anchor_and_placeholders_cover_entire_first_half_hour(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        old_db,old_store=main.DB,cm.STORE
        main.DB=Path(temp.name)/"data.db"
        cm.STORE=Path(temp.name)/"settings.db"
        self.addCleanup(setattr,main,"DB",old_db)
        self.addCleanup(setattr,cm,"STORE",old_store)
        main.initialize()
        with main.db() as c:
            c.execute("""INSERT INTO philo_listings
                (channel_id,channel_label,start,stop,title)
                VALUES(?,?,?,?,?)""",
                ("philo:some","Some Network","2026-11-15T19:44:00+00:00",
                 "2026-11-15T20:10:00+00:00","An Actual Show"))
        # Keep the fake current instant near the middle of the half hour.
        # The first 14 minutes still need an explicit empty-program tile.
        real_datetime=main.datetime
        class FixedDateTime(datetime):
            @classmethod
            def now(cls,tz=None):
                return datetime(2026,11,15,19,40,tzinfo=timezone.utc)
        with patch.object(main,"datetime",FixedDateTime),\
             patch("app.channels_dvr._playlist",return_value=("http://localhost:8089","#EXTM3U")),\
             patch("app.channels_epg.schedule",return_value=({},None)):
            result=main.guide(hours=2,source="philo")
        self.assertEqual(result["window_start"],"2026-11-15T19:30:00+00:00")
        row=next(x for x in result["channels"] if x["label"]=="Some Network")
        first=row["programs"][0]
        self.assertEqual(first["title"],"No guide information")
        self.assertEqual(first["start"],"2026-11-15T19:30:00+00:00")
        self.assertEqual(first["stop"],"2026-11-15T19:44:00+00:00")
        self.assertEqual(row["programs"][1]["title"],"An Actual Show")

if __name__=="__main__":
    unittest.main()
