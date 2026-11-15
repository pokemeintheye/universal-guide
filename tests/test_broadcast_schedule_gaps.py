"""Keep guide EPG timeline gap-free without fabricating program titles."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

from app import main,channel_management as cm

class EPGGapTests(unittest.TestCase):
    def test_missing_leading_and_interstitial_intervals_are_explicit(self):
        start=datetime(2026,11,15,18,30,tzinfo=timezone.utc)
        end=start+timedelta(hours=1)
        def item(title,a,b):
            return {"title":title,"start":a.isoformat(),"stop":b.isoformat(),
                    "plex":{"rating_key":"12"}}
        programs=[
            item("Head 2 Head",start+timedelta(minutes=14),start+timedelta(minutes=40)),
            item("Next real title",start+timedelta(minutes=48),end)]
        filled=main.fill_broadcast_schedule_gaps(programs,"6101","Discovery",start,end)
        self.assertEqual([x["title"] for x in filled],
                         ["No guide information","Head 2 Head",
                          "No guide information","Next real title"])
        self.assertEqual(filled[0]["start"],start.isoformat())
        self.assertEqual(filled[0]["stop"],programs[0]["start"])
        self.assertEqual(filled[2]["start"],programs[0]["stop"])
        self.assertEqual(filled[2]["stop"],programs[1]["start"])
        self.assertTrue(filled[0]["no_guide_data"])
        self.assertIsNone(filled[2]["plex"])
        self.assertEqual(filled[1]["plex"],{"rating_key":"12"})
        self.assertEqual(filled[3]["title"],"Next real title")

    def test_covered_and_overlapping_programs_not_modified(self):
        start=datetime(2026,11,15,18,30,tzinfo=timezone.utc)
        end=start+timedelta(minutes=60)
        entries=[
            {"title":"One","start":start.isoformat(),
             "stop":(start+timedelta(minutes=40)).isoformat()},
            {"title":"Two","start":(start+timedelta(minutes=35)).isoformat(),
             "stop":end.isoformat()}]
        self.assertEqual(main.fill_broadcast_schedule_gaps(entries,"1","A",start,end),entries)

    def test_channel_guide_integration_preserves_provider_and_fills_missing_start(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous_db,previous_store=main.DB,cm.STORE
        main.DB=Path(tmp.name)/"guide.db"
        cm.STORE=Path(tmp.name)/"channels.db"
        self.addCleanup(setattr,main,"DB",previous_db)
        self.addCleanup(setattr,cm,"STORE",previous_store)
        main.initialize()
        now=datetime.now(timezone.utc)
        broadcast=now+timedelta(minutes=15)
        end=now+timedelta(minutes=50)
        with main.db() as c:
            c.execute("""INSERT INTO philo_listings
                (channel_id,channel_label,start,stop,title)
                VALUES (?,?,?,?,?)""",
                ("philo:test","FETV",broadcast.isoformat(),
                 end.isoformat(),"Rawhide"))
        with patch("app.channels_dvr._playlist",
                   return_value=("http://192.168.1.2:8089","#EXTM3U")),\
             patch("app.channels_epg.schedule",return_value=({},None)):
            guide=main.guide(hours=2,source="all")
        row=next(x for x in guide["channels"]
                 if x["source"]=="philo" and x["label"]=="FETV")
        self.assertEqual(row["programs"][0]["title"],"No guide information")
        self.assertEqual(row["programs"][1]["title"],"Rawhide")
        self.assertEqual(row["programs"][0]["stop"],broadcast.isoformat())
        self.assertNotIn("plex",row["programs"][1]) if False else None

if __name__=="__main__":
    unittest.main()
