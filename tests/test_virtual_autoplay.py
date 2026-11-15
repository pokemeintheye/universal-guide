"""Next virtual program endpoint follows saved lineup schedule."""
import unittest
import tempfile
from pathlib import Path
from datetime import datetime,timezone
from app import main,channel_management as cm

class Request:
    headers={}

class VirtualNextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_db,old_store=main.DB,cm.STORE
        main.DB=Path(self.tmp.name)/"guide.db"
        cm.STORE=Path(self.tmp.name)/"settings.db"
        self.addCleanup(setattr,main,"DB",old_db)
        self.addCleanup(setattr,cm,"STORE",old_store)
        main.initialize()
        with main.db() as c:
            c.executemany("""INSERT INTO media(id,kind,title,year,normalized,duration_ms)
                VALUES(?,?,?,?,?,?)""",[
                ("movie:501","movie","Movie One",2020,"movie one",660000),
                ("movie:502","movie","Movie Two",2021,"movie two",660000)])
        self.key=cm.create_virtual(cm.VirtualUpdate(name="Movie Channel",
            mode="manual",items=[
               cm.VirtualItem(rating_key=501,duration_minutes=11),
               cm.VirtualItem(rating_key=502,duration_minutes=11)]),Request())["id"]

    def test_next_program_is_exactly_following_guide_slot(self):
        start=datetime(2026,11,15,17,tzinfo=timezone.utc)
        schedule=cm.virtual_guide(start,start.replace(hour=20))[0]["programs"]
        current=schedule[0]
        next_program=cm.next_virtual_program(self.key,current["stop"])["program"]
        self.assertEqual(next_program["start"],current["stop"])
        self.assertNotEqual(next_program["plex"]["rating_key"],current["plex"]["rating_key"])
        second=cm.next_virtual_program(self.key,next_program["stop"])["program"]
        self.assertEqual(second["start"],next_program["stop"])

    def test_no_next_for_disabled_channel(self):
        cm.update_virtual(self.key,cm.VirtualUpdate(name="Off",enabled=False,
            items=[cm.VirtualItem(rating_key=501,duration_minutes=11)]),Request())
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as failed:
            cm.next_virtual_program(self.key,"2026-11-15T17:00:00+00:00")
        self.assertEqual(failed.exception.status_code,404)

if __name__=="__main__": unittest.main()
