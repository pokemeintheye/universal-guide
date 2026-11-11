import unittest
from urllib.parse import urlparse

from app.playback import _map_reference, _rewrite_playlist


class NativeHLSResourceTests(unittest.TestCase):
    def setUp(self):
        self.parent = "http://plex.example:32400/video/:/transcode/universal/start.m3u8"
        self.session = {"id": "abc12345678901234567", "resources": {}}

    def test_variant_and_segment_keep_extensions(self):
        master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000\nvariant.m3u8\n"
        rewritten = _rewrite_playlist(self.session, self.parent, master).decode()
        variant = rewritten.strip().splitlines()[-1]
        self.assertTrue(variant.endswith(".m3u8"), rewritten)
        self.assertTrue(variant.startswith("/api/player/abc12345678901234567/"))
        self.assertEqual(len(self.session["resources"]), 1)

        media = b"#EXTM3U\n#EXTINF:4.0,\nsegment00001.ts\n"
        rewritten_media = _rewrite_playlist(self.session, self.parent, media).decode()
        segment = rewritten_media.strip().splitlines()[-1]
        self.assertTrue(segment.endswith(".ts"), rewritten_media)
        self.assertEqual(len(self.session["resources"]), 2)

    def test_mapped_resource_is_reused(self):
        first = _map_reference(self.session, self.parent, "variant.m3u8")
        second = _map_reference(self.session, self.parent, "variant.m3u8")
        self.assertEqual(first, second)
        self.assertEqual(len(self.session["resources"]), 1)

    def test_token_is_not_exposed(self):
        url = _map_reference(self.session, self.parent,
                             "variant.m3u8?X-Plex-Token=secret&session=abc")
        self.assertNotIn("secret", url)
        self.assertEqual(len(self.session["resources"]), 1)
        self.assertNotIn("X-Plex-Token", next(iter(self.session["resources"].values())))


if __name__ == "__main__":
    unittest.main()
