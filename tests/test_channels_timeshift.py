"""Channels DVR disk timeshift contract and safety checks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import channels_dvr as cd
from app import live_timeshift as ts


class FakeProcess:
    def __init__(self):
        self.ended = False
        self.terminated = False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.terminated = True
        self.ended = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.ended = True


class TimeshiftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.originals = (ts.STORE, ts.PREPARED, ts.SWEEPER_STARTED)
        ts.STORE = self.path / "channels-live-cache"
        ts.PREPARED = False
        # Don't launch a real reaper in a unit test.
        ts.SWEEPER_STARTED = True
        ts.ACTIVE.clear()

    def tearDown(self):
        for sid in list(ts.ACTIVE):
            ts._stop(sid)
        ts.STORE, ts.PREPARED, ts.SWEEPER_STARTED = self.originals

    def test_timeshift_capability_matches_generic_playback_options(self):
        with patch.object(cd, "_playlist", return_value=(
            "http://192.168.2.4:8089",
            '#EXTM3U\n#EXTINF:-1 tvg-chno="6104",HGTV\n'
            '/devices/ANY/channels/6104/hls/master.m3u8\n')):
            result = cd.live_guide_options()["options"]
            stream = cd.live_stream("tve-6104")
        hgtv = next(o for o in result if o["guide_label"] == "HGTV")
        self.assertEqual(hgtv["action"], "play-hls")
        self.assertTrue(hgtv["timeshift"])
        self.assertTrue(stream["timeshift"])

    def test_invalid_channel_never_launches_encoder(self):
        with patch.object(ts.subprocess, "Popen") as popen:
            with self.assertRaises(HTTPException) as context:
                ts.start("tve-9999")
        self.assertEqual(context.exception.status_code, 404)
        popen.assert_not_called()

    def test_start_status_media_cleanup_and_allowlisted_input(self):
        process = FakeProcess()
        with patch.object(ts, "live_stream", return_value={
            "stream_url": "http://192.168.2.4:8089/devices/ANY/channels/6104/hls/master.m3u8"
        }), patch.object(ts.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             patch.object(ts.subprocess, "Popen", return_value=process) as launch:
            result = ts.start("tve-6104")
        sid = result["session_id"]
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{20,80}$")
        self.assertTrue(result["playlist_url"].endswith("/" + sid + "/index.m3u8"))
        args = launch.call_args.args[0]
        self.assertIn("-hls_list_size", args)
        self.assertEqual(args[args.index("-hls_list_size") + 1], str(ts.SEGMENT_COUNT))
        self.assertIn("-c:v", args)
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertIn("192.168.2.4:8089", args[args.index("-i") + 1])

        directory = ts.STORE / sid
        (directory / "index.m3u8").write_text(
            "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:2\n"
            "#EXTINF:4.0,\nseg_00000002.ts\n#EXTINF:4.0,\nseg_00000003.ts\n")
        (directory / "seg_00000002.ts").write_bytes(b"x" * 188)
        health = ts.status(sid)
        self.assertTrue(health["transcoder_running"])
        self.assertEqual(health["disk_dvr_playlist"]["segment_count"], 2)
        playlist_response = ts.media(sid, "index.m3u8")
        self.assertEqual(playlist_response.media_type, "application/vnd.apple.mpegurl")
        segment_response = ts.media(sid, "seg_00000002.ts")
        self.assertEqual(segment_response.media_type, "video/mp2t")
        with self.assertRaises(HTTPException):
            ts.media(sid, "../ffmpeg.log")
        self.assertEqual(ts.close(sid), {"stopped": True})
        self.assertTrue(process.terminated)
        self.assertFalse(directory.exists())

    def test_session_limit_is_enforced(self):
        ts.STORE.mkdir()
        for index in range(ts.MAX_SESSIONS):
            ts.ACTIVE["session-abcdefghijklmnop" + str(index)] = {
                "process": FakeProcess(), "directory": ts.STORE / ("session-" + str(index)),
                "last": ts.time.monotonic(),
            }
        with patch.object(ts, "live_stream", return_value={"stream_url": "http://192.168.2.4:8089/stream"}), \
             patch.object(ts.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             patch.object(ts.subprocess, "Popen") as popen:
            with self.assertRaises(HTTPException) as ctx:
                ts.start("tve-6104")
        self.assertEqual(ctx.exception.status_code, 409)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
