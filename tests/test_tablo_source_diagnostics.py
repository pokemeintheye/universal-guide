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

    def test_pause_marker_captures_segment_near_live_not_buffer_start(self):
        body="#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:700\n"+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(360))
        sequence,overlap=tablo_live._pause_source_sequence(body)
        self.assertEqual(sequence,1041)
        self.assertEqual(overlap,18.0)
        next_body=body+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(360,450))
        manifest,info=tablo_live._continuous_source_manifest(
            "http://192.168.1.9:8885",
            "http://192.168.1.9/stream/abc/index.m3u8",
            next_body,start_sequence=sequence)
        self.assertEqual(info["start_sequence"],1041)
        self.assertEqual(info["first_listed_sequence"],700)
        self.assertEqual(info["last_sequence"],1149)
        self.assertEqual(info["seconds_behind_source_live"],109.0)
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:1041",manifest)
        self.assertIn("seg_341.ts",manifest)
        self.assertNotIn("seg_0.ts",manifest)

    def test_pause_marker_expired_source_segment_is_not_clamped(self):
        body="#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1100\n"+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(200))
        with self.assertRaises(HTTPException) as exc:
            tablo_live._continuous_source_manifest(
                "http://192.168.1.9:8885",
                "http://192.168.1.9/stream/abc/index.m3u8",
                body,start_sequence=1041)
        self.assertEqual(exc.exception.status_code,409)

    def test_continuous_manifest_has_no_endlist_and_keeps_sequence(self):
        body="#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1000\n"+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(720))
        url="http://192.168.1.9/stream/abc/index.m3u8"
        manifest,meta=tablo_live._continuous_source_manifest(
            "http://192.168.1.9:8885",url,body,seconds_behind_live=600)
        self.assertTrue(manifest.startswith("#EXTM3U\n"))
        self.assertNotIn("#EXT-X-ENDLIST",manifest)
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:1120",manifest)
        self.assertEqual(meta["start_sequence"],1120)
        self.assertIn("seg_120.ts",manifest)
        # Next refresh preserves the same start rather than jumping to live.
        updated=body+"#EXTINF:1.000,\nseg_720.ts\n"
        next_manifest,next_meta=tablo_live._continuous_source_manifest(
            "http://192.168.1.9:8885",url,updated,start_sequence=1120)
        self.assertEqual(next_meta["start_sequence"],1120)
        self.assertIn("seg_720.ts",next_manifest)
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:1120",next_manifest)

    def test_continuous_rejects_insufficient_buffer(self):
        body="#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:1\n"+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(60))
        with self.assertRaises(HTTPException) as e:
            tablo_live._continuous_source_manifest(
                "http://192.168.1.9:8885",
                "http://192.168.1.9/stream/abc/index.m3u8",
                body,seconds_behind_live=120)
        self.assertEqual(e.exception.status_code,409)

    def test_historical_manifest_is_bounded_and_uses_native_segments(self):
        body="#EXTM3U\n"+ "".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(220))
        manifest,meta=tablo_live._historic_clip_manifest(
            "http://192.168.1.9:8885",
            "http://192.168.1.9/stream/abc/index.m3u8",body,120,75)
        self.assertTrue(manifest.startswith("#EXTM3U\n"))
        self.assertTrue(manifest.endswith("#EXT-X-ENDLIST\n"))
        self.assertEqual(meta["segment_count"],75)
        self.assertIn("/stream/abc/seg_100.ts",manifest)
        self.assertNotIn("/stream/abc/seg_0.ts",manifest)
        self.assertLess(meta["clip_duration_seconds"],100)

    def test_five_minute_history_preview_is_bounded(self):
        body="#EXTM3U\n"+"".join(
            "#EXTINF:1.000,\nseg_"+str(i)+".ts\n" for i in range(720))
        manifest,info=tablo_live._historic_clip_manifest(
            "http://192.168.1.9:8885",
            "http://192.168.1.9/stream/abc/index.m3u8",
            body,seconds_behind_live=600,clip_seconds=300)
        self.assertEqual(info["segment_count"],300)
        self.assertEqual(info["clip_duration_seconds"],300.0)
        self.assertTrue(manifest.endswith("#EXT-X-ENDLIST\n"))
        self.assertIn("seg_120.ts",manifest)
        self.assertNotIn("seg_420.ts",manifest)

    def test_oversized_history_preview_is_rejected(self):
        with self.assertRaises(HTTPException) as exc:
            tablo_live._historic_clip_manifest(
                "http://192.168.1.9:8885",
                "http://192.168.1.9/stream/abc/index.m3u8",
                "#EXTM3U",seconds_behind_live=600,clip_seconds=601)
        self.assertEqual(exc.exception.status_code,400)

    def test_historical_manifest_refuses_insufficient_history(self):
        body="#EXTM3U\n"+ "".join(
            "#EXTINF:1,\nseg_"+str(i)+".ts\n" for i in range(25))
        with self.assertRaises(HTTPException) as ctx:
            tablo_live._historic_clip_manifest(
                "http://192.168.1.9:8885",
                "http://192.168.1.9/stream/abc/index.m3u8",body,120)
        self.assertEqual(ctx.exception.status_code,409)

    def test_long_tablo_playlist_within_4mib_limit(self):
        class Response:
            status_code=200
            def __init__(self,body):self.body=body
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def iter_content(self,chunk_size=8192):
                for i in range(0,len(self.body),chunk_size):
                    yield self.body[i:i+chunk_size]
        # A growing native playlist commonly passes the old 256 KiB cap.
        payload=b"#EXTM3U\\n"+b"#EXTINF:1,\\nseg.ts\\n"*16000
        self.assertGreater(len(payload),262144)
        with patch.object(tablo_live.requests,"get",return_value=Response(payload)):
            self.assertEqual(tablo_live._read_source_playlist("http://tablo/stream/pl.m3u8"),
                             payload.decode())
        with patch.object(tablo_live.requests,"get",return_value=Response(b"#EXTM3U\\n"+b"x"*(4*1024*1024))):
            with self.assertRaises(HTTPException) as err:
                tablo_live._read_source_playlist("http://tablo/stream/pl.m3u8")
        self.assertEqual(err.exception.status_code,502)

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
