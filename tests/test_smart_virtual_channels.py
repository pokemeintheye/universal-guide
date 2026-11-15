"""Metadata-driven Plex virtual channel scheduling and migration tests."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from app import channel_management as cm
from app import virtual_rules as vr
from app import main

class Request:
    headers={}

class SmartChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_db,old_store=main.DB,cm.STORE
        main.DB=Path(self.tmp.name)/"guide.db"
        cm.STORE=Path(self.tmp.name)/"virtual.db"
        self.addCleanup(setattr,main,"DB",old_db)
        self.addCleanup(setattr,cm,"STORE",old_store)
        main.initialize()
        with main.db() as c:
            c.executemany("""INSERT INTO media
                (id,kind,title,show_title,year,season,episode,genres,duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?)""",[
                ("episode:100","episode","Pilot","The Office",2005,1,1,"Comedy,Workplace",1320000),
                ("episode:101","episode","Second","The Office",2005,1,2,"Comedy",1430000),
                ("episode:102","episode","Pilot","The Simpsons",1989,1,1,"Comedy,Animation",1320000),
                ("movie:200","movie","Space Movie","",1995,None,None,"Sci-Fi,Adventure",7200000),
                ("movie:201","movie","Another Film","",2012,None,None,"Comedy",6000000),
            ])

    def test_genre_show_year_and_type_filters(self):
        with main.db() as c:
            items=[dict(x) for x in c.execute("SELECT * FROM media")]
        r=vr.SmartRules(media_type="episodes",genres=["Comedy"],
             shows=["The Office"],year_min=2000,order="chronological")
        self.assertEqual([m["id"] for m in vr.matching_items(items,r)],
                         ["episode:100","episode:101"])
        self.assertEqual(vr.pool(items,r)["matching"],2)
        movies=vr.SmartRules(media_type="movies",genres=["Sci-Fi"],year_max=2000)
        self.assertEqual([m["id"] for m in vr.matching_items(items,movies)],["movie:200"])
        self.assertEqual(vr.duration_minutes(items[0],vr.SmartRules()),22)

    def test_stable_shuffle_across_reloads(self):
        with main.db() as c:
            items=[dict(x) for x in c.execute("SELECT * FROM media")]
        rule=vr.SmartRules(media_type="episodes",order="shuffle",seed=500)
        first=[x["id"] for x in vr.matching_items(items,rule)]
        second=[x["id"] for x in vr.matching_items(list(reversed(items)),rule)]
        self.assertEqual(first,second)

    def test_smart_crud_and_deterministic_guide(self):
        rule=vr.SmartRules(media_type="episodes",shows=["The Office"],
                           order="chronological")
        result=cm.create_virtual(cm.VirtualUpdate(name="Office TV",
                                  mode="smart",rules=rule),Request())
        channel_id=result["id"]
        saved=next(x for x in cm.virtual_definitions() if x["id"]==channel_id)
        self.assertEqual(saved["mode"],"smart")
        self.assertEqual(saved["rules"]["shows"],["The Office"])
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        first=cm.virtual_guide(start,start+timedelta(hours=6))
        second=cm.virtual_guide(start,start+timedelta(hours=6))
        self.assertEqual(first,second)
        self.assertTrue(all(p["plex"]["rating_key"] in ("100","101") for p in first[0]["programs"]))
        self.assertGreater(len(first[0]["programs"]),5)
        cm.update_virtual(channel_id,cm.VirtualUpdate(name="Movies",
                mode="smart",rules=vr.SmartRules(media_type="movies")),Request())
        self.assertEqual(cm.virtual_definitions()[0]["rules"]["media_type"],"movies")
        cm.delete_virtual(channel_id,Request())
        self.assertEqual(cm.virtual_definitions(),[])

    def test_existing_manual_playlist_remains_supported(self):
        body=cm.VirtualUpdate(name="Manual",items=[cm.VirtualItem(rating_key=200,duration_minutes=90)])
        cm.create_virtual(body,Request())
        row=cm.virtual_definitions()[0]
        self.assertEqual(row["mode"],"manual")
        start=datetime(2026,11,15,13,tzinfo=timezone.utc)
        schedule=cm.virtual_guide(start,start+timedelta(hours=2))
        self.assertTrue(all(p["plex"]["rating_key"]=="200" for p in schedule[0]["programs"]))

    def test_invalid_filters_rejected(self):
        for rule in [
            vr.SmartRules(media_type="movies",shows=["The Office"]),
            vr.SmartRules(year_min=2020,year_max=1990),
        ]:
            with self.assertRaises(HTTPException):
                vr.validate_rules(rule)

    def test_metadata_schema_upgrade_from_old_database(self):
        # Reinitialize old media table independently of current test DB.
        with main.db() as c:
            c.execute("DROP TABLE media")
            c.execute("""CREATE TABLE media(id TEXT PRIMARY KEY,kind TEXT,title TEXT,
                      year INT,normalized TEXT,show_title TEXT,season INT,episode INT,guids TEXT)""")
        main.initialize()
        with main.db() as c:
            columns={x["name"] for x in c.execute("PRAGMA table_info(media)")}
        self.assertIn("genres",columns)
        self.assertIn("duration_ms",columns)

if __name__=="__main__":
    unittest.main()
