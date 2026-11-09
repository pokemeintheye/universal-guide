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
        self.assertIn('document.getElementById("playerbackdrop").classList.remove("plex-mode")',HTML)
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

    def test_remote_friendly_track_menu_and_chapter_images(self):
        self.assertIn('id="plextrackchoices"',HTML)
        self.assertIn('className="plex-option"',HTML)
        self.assertIn('button.addEventListener("click",()=>{',HTML)
        self.assertIn('button.dataset.value=String(id)',HTML)
        self.assertIn('event.key==="ArrowDown"',HTML)
        self.assertIn('"ArrowDown","ArrowUp","ArrowLeft","ArrowRight"',HTML)
        self.assertIn('event.key==="Escape"',HTML)
        self.assertIn('thumb.loading="lazy"',HTML)
        self.assertIn('Math.floor(item.start_seconds)',HTML)
        self.assertIn('@media (min-width:900px)',HTML)
        self.assertIn('width:clamp(340px,28vw,480px)',HTML)

    def test_plex_resumes_after_long_pause_and_recovers_expired_transcodes(self):
        self.assertIn('function handlePlexPauseResume(video,generation)',HTML)
        self.assertIn('Date.now()-pausedAt<90000',HTML)
        self.assertIn('refreshExpiredPlexPlayback(generation,"long_pause")',HTML)
        self.assertIn('refreshExpiredPlexPlayback(generation,"segment_404")',HTML)
        self.assertIn('Date.now()-plexRecoveryAttemptAt<120000',HTML)
        self.assertIn('plexLastPlaybackPosition=plexPlaybackPosition(video)',HTML)
        self.assertIn('launchPlex("resume",{position,audio_stream_id:audio,',HTML)

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

    def test_hevc_source_requests_h264_aac_1080p_output(self):
        import sqlite3
        from tempfile import TemporaryDirectory
        from urllib.parse import parse_qs,urlparse
        with TemporaryDirectory() as directory:
            path=Path(directory)/"plex.db"
            with sqlite3.connect(path) as con:
                con.execute("CREATE TABLE media(id TEXT,title TEXT,kind TEXT)")
                con.execute("INSERT INTO media VALUES(?,?,?)",
                            ("movie:142","Test Movie","movie"))
            with patch.object(playback,"_config",
                    return_value=(path,"http://plex","secret")), \
                 patch.object(playback,"_plex_duration",return_value=7200), \
                 patch.object(playback,"_plex_item_metadata",
                              return_value={"subtitles":[]}):
                details=playback.start_playback(
                    playback.PlaybackRequest(rating_key=142))
            sid=details["playlist_url"].split("/")[3]
            try:
                master=playback._sessions[sid]["resources"]["master.m3u8"]
                params=parse_qs(urlparse(master).query)
                self.assertEqual(params["videoCodec"],["h264"])
                self.assertEqual(params["audioCodec"],["aac"])
                self.assertEqual(params["videoResolution"],["1920x1080"])
                self.assertEqual(params["maxVideoBitrate"],["12000"])
                self.assertEqual(params["directPlay"],["0"])
            finally:
                playback._sessions.pop(sid,None)

    def test_chapter_metadata_requests_chapters_and_uses_tag(self):
        sample=MOVIE_XML.replace(b'<Chapter title="Opening" startTime="0"/>',
            b'<Chapter tag="Opening" startTimeOffset="0"/>')
        response=MagicMock(content=sample)
        with patch.object(playback,"_config",
                          return_value=("/tmp/db","http://plex","secret")), \
             patch.object(playback.requests,"get",return_value=response) as get:
            metadata=playback._plex_item_metadata(142)
        self.assertEqual(metadata["chapters"][0]["title"],"Opening")
        self.assertEqual(metadata["part_id"],4242)
        self.assertEqual(get.call_args.kwargs["params"]["includeChapters"],1)

    def test_subtitle_burn_requested_and_no_stream_ids_in_transcode_url(self):
        import sqlite3
        from tempfile import TemporaryDirectory
        from urllib.parse import parse_qs,urlparse
        with TemporaryDirectory() as directory:
            path=Path(directory)/"plex.db"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE media(id TEXT,title TEXT,kind TEXT)")
                connection.execute("INSERT INTO media VALUES(?,?,?)",
                                   ("movie:142","Test Movie","movie"))
            metadata={"audio":[{"id":41}],"subtitles":[{"id":51,"selected":True}]}
            with patch.object(playback,"_config",
                              return_value=(path,"http://plex","secret")), \
                 patch.object(playback,"_plex_duration",return_value=7200), \
                 patch.object(playback,"_plex_item_metadata",return_value=metadata):
                result=playback.start_playback(playback.PlaybackRequest(
                    rating_key=142,mode="resume",offset_seconds=30,
                    subtitle_stream_id=51))
            sid=result["playlist_url"].split("/")[3]
            try:
                params=parse_qs(urlparse(
                    playback._sessions[sid]["resources"]["master.m3u8"]).query)
                self.assertEqual(params["subtitles"],["burn"])
                self.assertNotIn("subtitleStreamID",params)
                self.assertNotIn("audioStreamID",params)
            finally:
                playback._sessions.pop(sid,None)

    def test_track_selection_validates_part_and_uses_plex_put(self):
        session_id="validOpaquePlexSession123456"
        metadata={"part_id":4242,
                  "audio":[{"id":41}],"subtitles":[{"id":51}]}
        with patch.dict(playback._sessions,{session_id:{
                "id":session_id,"rating_key":142,"last":__import__("time").monotonic()}},clear=False), \
             patch.object(playback,"_config",
                return_value=("/tmp/db","http://plex","secret")), \
             patch.object(playback,"_plex_item_metadata",return_value=metadata), \
             patch.object(playback.requests,"put",return_value=MagicMock()) as put:
            result=playback.select_playback_tracks(session_id,
                playback.PlexTrackSelection(subtitle_stream_id=51))
        self.assertTrue(result["ok"])
        self.assertEqual(put.call_args.kwargs["params"]["subtitleStreamID"],51)
        self.assertIn("/library/parts/4242",put.call_args.args[0])

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
