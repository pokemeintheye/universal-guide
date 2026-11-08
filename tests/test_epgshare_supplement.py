"""Verify broad missing-channel supplementation without corrupting primary EPG."""
import gzip
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

from app.epgshare_supplement import STATIONS,fetch_missing

class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def raise_for_status(self):pass
    def iter_content(self,chunk_size=131072):yield self.data

def parse_date(raw):
    return datetime.strptime(raw[:14],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()

def fixture():
    begin=(datetime.now(timezone.utc)+timedelta(minutes=10)).strftime("%Y%m%d%H%M%S")
    stop=(datetime.now(timezone.utc)+timedelta(minutes=50)).strftime("%Y%m%d%H%M%S")
    channels=[("Home.and.Garden.Television.HD.us2","HGTV show"),
              ("Magnolia.Network.HD.us2","Magnolia show"),
              ("Food.Network.HD.us2","Food show"),
              ("Home.and.Garden.Television.HD.(Pacific).us2","Pacific show")]
    elements=["<tv>"]
    for station,_ in channels:
        elements.append('<channel id="'+station+'"><display-name>'+station+'</display-name></channel>')
        elements.append('<programme channel="'+station+'" start="'+begin+'" stop="'+stop+
          '"><title>'+station+'</title><sub-title>Pilot</sub-title></programme>')
    elements.append("</tv>")
    return gzip.compress("".join(elements).encode())

class SupplementTests(unittest.TestCase):
    def test_station_ids_have_unique_and_safe_names(self):
        self.assertEqual(len(STATIONS),len(set(STATIONS.values())))
        self.assertGreaterEqual(len(STATIONS),65)
        for name,identifier in STATIONS.items():
            self.assertTrue(identifier.endswith(".us2"),name)
            self.assertNotIn("Pacific",identifier)
            self.assertNotIn("West",identifier)

    def test_only_missing_channels_and_eastern_feed(self):
        with patch("app.epgshare_supplement.requests.get",return_value=Response(fixture())) as request:
            entries,report=fetch_missing({"Food Network"},{"Food Network","HGTV","Magnolia Network"},
                                        parse_date,lambda node:"series",lambda node:(None,None))
        self.assertEqual(set(entries),{"HGTV","Magnolia Network"})
        self.assertEqual(report["added"],2)
        self.assertEqual(report["programs"],2)
        self.assertEqual(request.call_count,1)

    def test_no_network_call_when_primary_has_all(self):
        with patch("app.epgshare_supplement.requests.get") as request:
            entries,report=fetch_missing({"HGTV"},{"HGTV"},parse_date,lambda x:"unknown",lambda x:(None,None))
        self.assertEqual(entries,{})
        self.assertEqual(report["requested"],0)
        request.assert_not_called()

    def test_missing_channel_is_not_invented(self):
        with patch("app.epgshare_supplement.requests.get",return_value=Response(fixture())):
            entries,report=fetch_missing(set(),{"Nicktoons"},parse_date,lambda x:"unknown",lambda x:(None,None))
        self.assertFalse(entries)
        self.assertEqual(report["added"],0)
        self.assertTrue(report["error"])

if __name__=="__main__":
    unittest.main()
