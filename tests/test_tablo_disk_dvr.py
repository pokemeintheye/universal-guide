"""Disk-backed Tablo DVR cache safeguards."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from app import tablo_live


class DiskDvrTests(unittest.TestCase):
    def test_bounded_rolling_window(self):
        self.assertEqual(tablo_live.DVR_SEGMENT_SECONDS,4)
        self.assertGreaterEqual(tablo_live.DVR_MINUTES,2)
        self.assertLessEqual(tablo_live.DVR_MINUTES,30)
        self.assertEqual(tablo_live.DVR_SEGMENT_COUNT,
                         tablo_live.DVR_MINUTES*60//tablo_live.DVR_SEGMENT_SECONDS)

    def test_cache_clears_abandoned_sessions_only(self):
        with TemporaryDirectory() as root:
            base=Path(root)
            abandoned=base/("a"*27)
            abandoned.mkdir()
            (abandoned/"seg_00001.ts").write_bytes(b"x"*188)
            unrelated=base/"unrelated_files"
            unrelated.mkdir()
            (unrelated/"keep.txt").write_text("keep")
            with patch.object(tablo_live,"STORE",base), \
                 patch.object(tablo_live,"CACHE_PREPARED",False):
                tablo_live._prepare_cache()
                self.assertFalse(abandoned.exists())
                self.assertTrue((unrelated/"keep.txt").exists())
                self.assertTrue(tablo_live.CACHE_PREPARED)
                tablo_live._prepare_cache()

    def test_disk_dvr_window_reports_actual_recorded_history(self):
        with TemporaryDirectory() as root:
            manifest=Path(root)/"index.m3u8"
            manifest.write_text("#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:41\n"
                "#EXTINF:4.000,\nseg_00041.ts\n"
                "#EXTINF:3.750,\nseg_00042.ts\n")
            info=tablo_live._disk_dvr_playlist_window(manifest)
            self.assertEqual(info["status"],"ok")
            self.assertEqual(info["window_seconds"],7.8)
            self.assertEqual(info["first_sequence"],41)
            self.assertEqual(info["last_sequence"],42)
            self.assertNotIn("seg_00042.ts",str(info))

    def test_cache_is_on_persistent_appdata_by_default(self):
        # Compose's /tmp is only 16 MiB and is intentionally not a DVR volume.
        self.assertNotEqual(str(tablo_live.STORE),"/tmp/universal-guide-live")


if __name__=="__main__":
    unittest.main()
