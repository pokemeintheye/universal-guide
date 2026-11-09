"""Static regressions for the standalone browser Tablo DVR interface.

Playback still needs iPhone/Unraid integration testing.
"""
import unittest
from pathlib import Path
from html.parser import HTMLParser

HTML=(Path(__file__).resolve().parents[1]/"app"/"static"/"index.html").read_text()


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=set()
    def handle_starttag(self, tag, attrs):
        props=dict(attrs)
        if "id" in props:self.ids.add(props["id"])


class TabloDvrControlsTests(unittest.TestCase):
    def test_expected_transport_controls_present(self):
        parser=Elements()
        parser.feed(HTML)
        for name in ("tablodvr","tablodvrpause","tablodvrback",
                     "tablodvrforward","tablodvrlive","tablodvrseek",
                     "tablodvrstate","tabloadvancedtoggle","tabloadvanced",
                     "tablolivepause","tablocontinuous","tabloliveback"):
            with self.subTest(name=name):
                self.assertIn(name,parser.ids)

    def test_normal_dvr_seeks_within_one_running_stream(self):
        self.assertIn("TABLO_DVR_EDGE_SECONDS=3",HTML)
        self.assertIn("function tabloDvrSeekTo(",HTML)
        self.assertIn("tabloDvrSeekTo(video.currentTime-20,video)",HTML)
        self.assertIn("tabloDvrSeekTo(video.currentTime+30,video)",HTML)
        self.assertIn('id="tablodvrback" class="tablo-transport tablo-transport-skip"',HTML)
        self.assertIn('aria-label="Rewind 20 seconds"',HTML)
        self.assertIn('id="tablodvrforward" class="tablo-transport tablo-transport-skip"',HTML)
        self.assertIn('aria-label="Forward 30 seconds"',HTML)
        self.assertIn("if(video.paused)video.play()",HTML)
        self.assertIn("else video.pause()",HTML)
        self.assertIn("tabloDvrSeekTo(window.hi)",HTML)

    def test_live_dvr_scrubber_tracks_seekable_buffer(self):
        self.assertIn("function tabloDvrWindow(video)",HTML)
        self.assertIn("const window=tabloDvrWindow(video)",HTML)
        self.assertIn("if(!window)",HTML)
        self.assertIn("slider.disabled=true",HTML)

    def test_live_hls_can_buffer_beyond_ninety_second_pause(self):
        self.assertIn("maxBufferLength:180",HTML)
        self.assertIn("maxMaxBufferLength:240",HTML)
        self.assertIn("maxBufferSize:128*1024*1024",HTML)
        self.assertIn("liveSyncMode:\"buffered\"",HTML)
        self.assertIn("liveMaxLatencyDurationCount:Infinity",HTML)
        self.assertIn("maxLiveSyncPlaybackRate:1",HTML)

    def test_diagnostics_separate_seekable_and_buffered_video(self):
        self.assertIn("buffered_ranges:tabloDvrBufferRanges(video)",HTML)
        self.assertIn("dvr_playback:tabloDvrPlaybackTelemetry",HTML)
        self.assertIn("hls_js_latency:",HTML)
        self.assertIn('["waiting","stalled","playing"]',HTML)

    def test_forward_and_jump_live_never_flicker_disabled(self):
        self.assertIn('forward.disabled=false;',HTML)
        self.assertIn('live.disabled=false;',HTML)
        self.assertNotIn('id="tablodvrforward" type="button" disabled',HTML)
        self.assertNotIn('id="tablodvrlive" type="button" disabled',HTML)
        self.assertIn('if(video.currentTime>=safeEnd-0.5)return;',HTML)
        self.assertIn('if(!video.paused&&video.currentTime>=safeEnd-0.5)return;',HTML)

    def test_original_historical_fallback_remains_available(self):
        self.assertIn('id="tablopauseadjust"',HTML)
        self.assertIn('id="tablocontinuous"',HTML)
        self.assertIn('id="tabloliveback"',HTML)
        self.assertIn('document.getElementById("tabloliveback").click()',HTML)

    def test_full_screen_overlay_keeps_plex_video_controls(self):
        self.assertIn('class="tablo-dvr" hidden',HTML)
        self.assertIn('id="tablodvrtitle"',HTML)
        self.assertIn('id="tablodvrnetwork"',HTML)
        self.assertIn('id="tablodvrmeta"',HTML)
        self.assertIn('id="tablodvrclose"',HTML)
        self.assertIn('class="tablo-dvr-bottom"',HTML)
        self.assertIn('class="tablo-go-live"',HTML)
        self.assertIn('panel.classList.add("tablo-mode")',HTML)
        self.assertIn('panel.classList.remove("tablo-mode")',HTML)
        self.assertIn('clean.controls=true;',HTML)

    def test_debug_controls_hidden_but_available(self):
        self.assertIn('get("debug")==="1"',HTML)
        self.assertIn('document.getElementById("tabloadvancedtoggle").hidden=!tabloDebugEnabled;',HTML)
        self.assertIn('id="tabloquickdiagnostics"',HTML)
        self.assertIn('id="tabloadvanced" hidden',HTML)
        self.assertIn('function showTabloOverlay()',HTML)
        self.assertIn('overlay.classList.add("controls-hidden")',HTML)

    def test_old_diagnostics_preserved_as_advanced_tools(self):
        self.assertIn('id="tabloquickdiagnostics"',HTML)
        self.assertIn('id="tablopauseadjust"',HTML)
        self.assertIn('id="tablosegments"',HTML)
        self.assertIn('id="tablodiagnosticspanel"',HTML)


if __name__=="__main__":
    unittest.main()
