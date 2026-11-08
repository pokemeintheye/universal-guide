"""Unit tests for non-sensitive Tablo HLS origin diagnostics."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app import tablo_live


class TestTabloSourcePlaylist(unittest.TestCase):
    def test_media_window_and_sequence(self):
        source="""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:4
#EXT-X-MEDIA-SEQUENCE:500
#EXTINF:4.000,
seg_500.ts
#EXTINF:3.500,
seg_501.ts
#EXT-X-PROGRAM-DATE-TIME:2026-11-08T22:00:00Z
"""
        s=tablo_live._summarize_source_playlist(source)
        self.assertEqual(s["kind"],"media")
        self.assertEqual(s["media_sequence"],500)
        self.assertEqual(s["last_media_sequence"],501)
        self.assertEqual(s["window_seconds"],7.5)
        self.assertTrue(s["program_date_time_present"])
        self.assertFalse(s["endlist"])

    def test_master_playlist_not_a_seek_window(self):
        source="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nvariant.m3u8\n"
        s=tablo_live._summarize_source_playlist(source)
        self.assertEqual(s["kind"],"master")
        self.assertEqual(s["variant_count"],1)
        self.assertEqual(s["segment_count"],0)

    def test_bad_hls_rejected(self):
        with self.assertRaises(ValueError):
            tablo_live._summarize_source_playlist("<html>no playlist</html>")

    def test_segment_urls_restricted_to_tablo_origin(self):
        body="#EXTM3U\n#EXTINF:1,\nseg_1.ts\n#EXTINF:1,\nseg_2.ts\n"
        urls=tablo_live._media_segment_urls(
            "http://192.168.1.9:8885",
            "http://192.168.1.9/stream/session/index.m3u8",body)
        self.assertEqual(len(urls),2)
        self.assertTrue(urls[0].endswith("/stream/session/seg_1.ts"))
        with self.assertRaises(HTTPException):
            tablo_live._media_segment_urls(
                "http://192.168.1.9:8885",
                "http://192.168.1.9/stream/session/index.m3u8",
                "#EXTM3U\n#EXTINF:1,\nhttp://evil.example/stream/x.ts\n")

    def test_segment_samples_bounded_and_no_raw_url(self):
        import requests
        class Response:
            status_code=206
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def iter_content(self,chunk_size=8192):
                yield bytes([0x47])+bytes(187)+bytes([0x47])+bytes(200000)
        with patch.object(tablo_live.requests,"get",return_value=Response()):
            r=tablo_live._probe_source_segment("http://tablo/stream/seg.ts?token=SECRET")
        self.assertEqual(r["sample_bytes"],65536)
        self.assertTrue(r["mpeg_ts_sync"])
        self.assertNotIn("SECRET",str(r))

    def test_error_never_includes_signed_stream_url(self):
        import requests
        with patch.object(tablo_live.requests,"get",side_effect=requests.Timeout(
                "http://tablo/stream/pl.m3u8?token=SECRET")):
            with self.assertRaises(HTTPException) as e:
                tablo_live._read_source_playlist(
                    "http://tablo/stream/pl.m3u8?token=SECRET")
        self.assertNotIn("SECRET",str(e.exception.detail))
        self.assertIn("Timeout",str(e.exception.detail))


if __name__=="__main__":
    unittest.main()
