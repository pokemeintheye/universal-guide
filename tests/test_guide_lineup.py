"""Cross-source channel order and visibility persist in Unraid appdata."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from app import main,channel_management as cm,guide_lineup as lineup
from app import channels_dvr as cd

class Request:
    headers={}

class GuideLineupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_store,old_db=cm.STORE,main.DB
        cm.STORE=Path(self.tmp.name)/"lineup.db"
        main.DB=Path(self.tmp.name)/"guide.db"
        self.addCleanup(setattr,cm,"STORE",old_store)
        self.addCleanup(setattr,main,"DB",old_db)
        main.initialize()
        with main.db() as c:
            c.execute("INSERT INTO media(id,kind,title,year,normalized) VALUES ('movie:501','movie','Movie',2020,'movie')")
            c.execute("""INSERT INTO philo_listings
                (channel_id,channel_label,start,stop,title) VALUES
                ('philo:news','News','2026-11-15T12:00:00+00:00','2026-11-15T21:00:00+00:00','News Today')""")
            c.execute("""INSERT INTO philo_listings
                (channel_id,channel_label,start,stop,title) VALUES
                ('philo:comedy','Comedy Central','2026-11-15T12:00:00+00:00','2026-11-15T21:00:00+00:00','Comedy')""")
        self.cid=cm.create_virtual(cm.VirtualUpdate(name="Movie Night",
                 items=[cm.VirtualItem(rating_key=501,duration_minutes=100)]),Request())["id"]

    def test_default_position_no_longer_forces_virtual_first(self):
        rows=[{"id":"philo:a","label":"A","source":"philo"},
              {"id":"philo:b","label":"B","source":"philo"},
              {"id":"virtual:1","label":"V","source":"virtual"}]
        result=lineup.sort_lineup(rows)
        self.assertEqual([x["id"] for x in result],["philo:a","philo:b","virtual:1"])

    def test_disable_and_custom_position_applies_to_guide(self):
        with patch("app.channels_dvr._playlist",return_value=("http://192.168.1.10:8089","#EXTM3U")),\
             patch("app.channels_epg.schedule",return_value=({},None)):
            before=main.guide(hours=2,start="2026-11-15T13:00:00+00:00")
            names=[x["id"] for x in before["channels"]]
            vid="virtual:"+self.cid
            self.assertIn(vid,names)
            second=next(x for x in names if x.startswith("philo:"))
            # Reorder all and hide a Philo source.
            new=[second,vid]+[x for x in names if x not in (second,vid)]
            saved=lineup.save_lineup(lineup.LineupUpdate(ids=new,enabled={second:False,vid:True}),Request())
            self.assertEqual(saved["saved"],len(new))
            visible=main.guide(hours=2,start="2026-11-15T13:00:00+00:00")["channels"]
            self.assertEqual(visible[0]["id"],vid)
            self.assertNotIn(second,[x["id"] for x in visible])
            all_rows=main.guide(hours=2,start="2026-11-15T13:00:00+00:00",include_hidden=True)["channels"]
            self.assertEqual(all_rows[0]["id"],second)
            self.assertFalse(all_rows[0]["lineup_enabled"])
            self.assertEqual(lineup.current()[vid]["position"],1)

    def test_settings_stored_by_opaque_channel_id(self):
        with patch("app.channels_dvr._playlist",return_value=("http://127.0.0.1:8089","#EXTM3U")),\
             patch("app.channels_epg.schedule",return_value=({},None)):
            initial=lineup.lineup(Request())["channels"]
            keys=[x["id"] for x in initial]
            target=next(x for x in keys if x.startswith("philo:"))
            lineup.save_lineup(lineup.LineupUpdate(ids=keys,enabled={target:False}),Request())
            self.assertFalse(next(x for x in lineup.lineup(Request())["channels"] if x["id"]==target)["enabled"])
            self.assertTrue(lineup.current()[target]["enabled"]==0)
            # Unconfigured newly appearing rows remain visible.
            self.assertTrue(lineup.sort_lineup([{"id":"new:1","source":"philo"}])[0]["lineup_enabled"])

if __name__=="__main__": unittest.main()
