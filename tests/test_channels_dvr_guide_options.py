"""Backend owns TVE guide aliases and stream-source choices."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app import channels_dvr as cd

M3U = """#EXTM3U
#EXTINF:-1 tvg-chno="6104" tvg-id="HGTVD",HGTV
/devices/ANY/channels/6104/hls/master.m3u8?format=hls&codec=copy
#EXTINF:-1 tvg-chno="6108" tvg-id="MAGNHD",Magnolia
/devices/ANY/channels/6108/hls/master.m3u8?format=hls&codec=copy
#EXTINF:-1 tvg-chno="6105" tvg-id="TLCHD",TLC
/devices/ANY/channels/6105/hls/master.m3u8?format=hls&codec=copy
"""

class BackendPlaybackOptionsTests(unittest.TestCase):
    def test_available_aliases_are_backend_owned(self):
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            payload = cd.guide_options()
        matches = {(x["guide_label"], x["channel_number"]) for x in payload["options"]}
        self.assertIn(("HGTV", "6104"), matches)
        self.assertIn(("Magnolia Network", "6108"), matches)
        self.assertIn(("DIY", "6108"), matches)
        self.assertIn(("TLC", "6105"), matches)
        self.assertNotIn(("AMC", "6086"), matches)
        self.assertTrue(all(x["provider"] == "channels-dvr" for x in payload["options"]))
        self.assertEqual(payload["fallback"], "philo")

    def test_generic_playback_contract(self):
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            listing = cd.live_guide_options()["options"]
            stream = cd.live_stream("tve-6105")
        tlc = next(item for item in listing if item["guide_label"] == "TLC")
        self.assertEqual(tlc["action"], "play-hls")
        self.assertEqual(tlc["playback_id"], "tve-6105")
        self.assertIn("/6105/hls/", stream["stream_url"])
        self.assertTrue(stream["timeshift"])

    def test_unavailable_channel_not_offered(self):
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", "#EXTM3U")):
            self.assertEqual(cd.guide_options()["options"], [])

    def test_unknown_number_is_not_resolved(self):
        with self.assertRaises(HTTPException) as context:
            cd._resolve("9999")
        self.assertEqual(context.exception.status_code, 404)

    def test_existing_hgtv_endpoint_still_resolves(self):
        with patch.object(cd, "_playlist", return_value=("http://192.168.2.4:8089", M3U)):
            result = cd.test_hgtv()
        self.assertEqual(result["channel_number"], "6104")
        self.assertTrue(result["stream_url"].startswith("http://192.168.2.4:8089/"))

if __name__ == "__main__":
    unittest.main()
