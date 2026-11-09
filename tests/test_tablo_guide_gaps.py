"""OTA channels must remain tuneable even when Tablo has no EPG data."""
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app import main


class TabloGuideGapTests(unittest.TestCase):
    def test_no_guide_creates_single_placeholder(self):
        start="2026-11-09T12:00:00+00:00"
        end="2026-11-09T20:00:00+00:00"
        found=main.fill_tablo_schedule_gaps(
            [],"/guide/channels/abc","20.1",start,end)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["title"],"No guide information")
        self.assertTrue(found[0]["no_guide_data"])
        self.assertEqual(found[0]["channel_id"],"/guide/channels/abc")
        self.assertEqual(found[0]["start"],start)
        self.assertEqual(found[0]["stop"],end)
        self.assertIsNone(found[0]["plex"])

    def test_real_listings_preserved_while_gaps_are_filled(self):
        a={"title":"Morning Show",
           "start":"2026-11-09T13:00:00+00:00",
           "stop":"2026-11-09T14:00:00+00:00"}
        b={"title":"Afternoon News",
           "start":"2026-11-09T15:00:00+00:00",
           "stop":"2026-11-09T16:00:00+00:00"}
        out=main.fill_tablo_schedule_gaps([b,a],"/guide/channels/abc",
            "20.1","2026-11-09T12:00:00+00:00","2026-11-09T18:00:00+00:00")
        self.assertEqual([p["title"] for p in out],
                         ["No guide information","Morning Show",
                          "No guide information","Afternoon News","No guide information"])
        self.assertIs(out[1],a)
        self.assertIs(out[3],b)
        self.assertEqual(out[2]["start"],a["stop"])
        self.assertEqual(out[2]["stop"],b["start"])

    def test_guide_includes_lineup_channel_without_airings(self):
        with TemporaryDirectory() as directory, patch.object(main,"DB",Path(directory)/"guide.db"):
            main.initialize()
            with main.db() as c:
                c.execute("INSERT INTO tablo_channels(channel_id,number,network,callsign) VALUES (?,?,?,?)",
                          ("/guide/channels/abc123","20.1","Mystery Network","WXYZ"))
            now=datetime.now(timezone.utc)+timedelta(minutes=5)
            result=main.guide(hours=8,plex_only=False,favorites_only=False,
                              source="tablo",include_hidden=False,start=now.isoformat())
            self.assertEqual(len(result["channels"]),1)
            channel=result["channels"][0]
            self.assertEqual(channel["number"],"20.1")
            self.assertEqual(channel["network"],"Mystery Network")
            self.assertEqual(channel["id"],"tablo:/guide/channels/abc123")
            self.assertEqual(len(channel["programs"]),1)
            self.assertTrue(channel["programs"][0]["no_guide_data"])

            # Plex-only never fabricates a Plex match for an OTA gap.
            plex=main.guide(hours=8,plex_only=True,favorites_only=False,
                            source="tablo",include_hidden=False,start=now.isoformat())
            self.assertEqual(plex["channels"],[])

            # Hidden and favorite filters still apply to lineup-only channels.
            with main.db() as c:
                c.execute("INSERT INTO channel_prefs(source,channel_id,favorite,hidden) VALUES (?,?,?,?)",
                          ("tablo","/guide/channels/abc123",0,1))
            hidden=main.guide(hours=8,plex_only=False,favorites_only=False,
                              source="tablo",include_hidden=False,start=now.isoformat())
            self.assertEqual(hidden["channels"],[])


if __name__=="__main__":
    unittest.main()
