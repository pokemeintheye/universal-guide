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

    def test_dvr_uses_existing_tested_playback_paths(self):
        self.assertIn('document.getElementById("tablolivepause").click()',HTML)
        self.assertIn('document.getElementById("tablocontinuous").click()',HTML)
        self.assertIn('document.getElementById("tabloliveback").click()',HTML)
        self.assertIn('seekHistoricalClip(-30)',HTML)
        self.assertIn('seekHistoricalClip(30)',HTML)

    def test_live_seekbar_disabled_while_not_in_dvr(self):
        self.assertIn('if(!historical||!video.seekable||!video.seekable.length)',HTML)
        self.assertIn('slider.disabled=true;',HTML)

    def test_old_diagnostics_preserved_as_advanced_tools(self):
        self.assertIn('id="tabloquickdiagnostics"',HTML)
        self.assertIn('id="tablopauseadjust"',HTML)
        self.assertIn('id="tablosegments"',HTML)
        self.assertIn('id="tablodiagnosticspanel"',HTML)


if __name__=="__main__":
    unittest.main()
