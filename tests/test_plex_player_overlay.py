"""Regression tests for the Tablo-style Plex player and codec fallback."""
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from app import playback

HTML=(Path(__file__).resolve().parents[1]/"app"/"static"/"index.html").read_text()

MOVIE_XML=b'''<MediaContainer>
<Video type="movie" title="Test Movie" year="2021" duration="7200000">
 <Media videoCodec="hevc" videoResolution="4k">
  <Part id="4242" indexes="sd">
   <Stream id="41" streamType="2" language="English" codec="aac" selected="1"/>
   <Stream id="42" streamType="2" language="French" codec="aac"/>
   <Stream id="51" streamType="3" language="English" codec="srt"/>
  </Part>
 </Media>
 <Chapter title="Opening" startTime="0"/>
 <Chapter title="Act Two" startTime="900000"/>
</Video>
</MediaContainer>'''

class PlexPlayerOverlayTests(unittest.TestCase):
    def test_overlay_controls_and_no_live_action(self):
        for marker in ('id="plexoverlay"', 'id="plexoverlaytitle"',
                       'id="plexoverlaymeta"', 'id="plexback"',
                       'id="plexpause"', 'id="plexforward"',
                       'id="plexseek"', 'id="plexthumbimage"',
                       'id="plexchaptersbutton"', 'id="plexaudiobutton"',
                       'id="plexsubtitlesbutton"'):
            with self.subTest(marker=marker):
                self.assertIn(marker,HTML)
        self.assertIn('panel.classList.add("plex-mode")',HTML)
        self.assertIn('panel.classList.remove("plex-mode")',HTML)
        self.assertIn('class="tablo-network">PLEX</div>',HTML)

    def test_audio_and_subtitles_reopen_transcode_at_position(self):
        self.assertIn('launchPlex("resume",{position,',HTML)
        self.assertIn("audio_stream_id:plexSelectedAudio",HTML)
        self.assertIn("subtitle_stream_id:plexSelectedSubtitle",HTML)
        self.assertIn('plexPlayerBaseOffset+',HTML)
        self.assertIn('plexTotalDuration=Number(info.duration_seconds)||0;',HTML)

    def test_graceful_scrub_preview_without_bif(self):
        self.assertIn('if(!plexTrackMetadata?.preview_available)return;',HTML)
        self.assertIn('img.onerror=()=>{img.hidden=true}',HTML)
        self.assertIn('document.getElementById("plexthumbtime").textContent=',HTML)

    def test_plex_movie_metadata_and_tracks(self):
        response=MagicMock()
        response.content=MOVIE_XML
        with patch.object(playback,"_config",return_value=("/tmp/db","http://plex", "secret")), \
             patch.object(playback.requests,"get",return_value=response):
            meta=playback._plex_item_metadata(142)
        self.assertEqual(meta["title"],"Test Movie")
        self.assertEqual(meta["year"],2021)
        self.assertEqual(meta["duration_seconds"],7200)
        self.assertEqual(meta["chapters"][1]["start_seconds"],900)
        self.assertEqual(meta["audio"][1]["language"],"French")
        self.assertEqual(meta["subtitles"][0]["id"],51)
        self.assertTrue(meta["preview_available"])

    def test_metadata_requires_active_session(self):
        with self.assertRaises(HTTPException):
            playback.playback_metadata("session_that_does_not_exist")

    def test_resuming_midtitle_trims_unavailable_lead_in(self):
        playlist=(b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n" +
                  b"".join(b"#EXTINF:1.000,\nseg.ts\n" for _ in range(120))+
                  b"#EXT-X-ENDLIST\n")
        out,meta=playback._trim_join_playlist(
            {"mode":"resume","requested_offset_seconds":48,
             "item_duration_seconds":120},playlist)
        self.assertIsNotNone(meta)
        self.assertGreater(meta["skipped_segments"],40)
        self.assertLess(len(out),len(playlist))


if __name__=="__main__":
    unittest.main()
