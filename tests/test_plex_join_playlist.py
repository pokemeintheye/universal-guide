import unittest

from app.playback import _trim_join_playlist


def playlist(segment_count):
    return ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n"
            + "".join(f"#EXTINF:1.000,\n/api/player/session/seg_{i}.ts\n"
                      for i in range(segment_count))
            + "#EXT-X-ENDLIST\n").encode("utf-8")


class PlexJoinTrimTests(unittest.TestCase):
    def test_join_trims_placeholder_slots_and_updates_sequence(self):
        data,info=_trim_join_playlist(
            {"mode":"join","requested_offset_seconds":6635.6,
             "item_duration_seconds":8281},playlist(8282))
        self.assertIsNotNone(info)
        self.assertGreater(info["skipped_segments"],6600)
        self.assertLess(info["skipped_segments"],6640)
        self.assertIn(b"#EXT-X-MEDIA-SEQUENCE:6633",data)
        self.assertNotIn(b"/seg_0.ts",data)
        self.assertIn(b"/seg_6633.ts",data)
        self.assertIn(b"/seg_8281.ts",data)
        self.assertTrue(data.endswith(b"#EXT-X-ENDLIST\n"))

    def test_beginning_remains_untouched(self):
        raw=playlist(100)
        result,info=_trim_join_playlist(
            {"mode":"beginning","requested_offset_seconds":0,
             "item_duration_seconds":100},raw)
        self.assertEqual(result,raw)
        self.assertIsNone(info)

    def test_join_already_windowed_playlist_stays_untouched(self):
        raw=playlist(120)
        result,info=_trim_join_playlist(
            {"mode":"join","requested_offset_seconds":6635.6,
             "item_duration_seconds":8281},raw)
        self.assertEqual(result,raw)
        self.assertIsNone(info)

    def test_encrypted_playlist_is_not_rewritten(self):
        raw=playlist(100).replace(b"#EXT-X-VERSION:3\n",
            b'#EXT-X-VERSION:3\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n')
        result,info=_trim_join_playlist(
            {"mode":"join","requested_offset_seconds":50,
             "item_duration_seconds":100},raw)
        self.assertEqual(result,raw)
        self.assertIsNone(info)


if __name__=="__main__":
    unittest.main()
