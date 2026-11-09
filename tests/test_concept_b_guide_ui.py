"""Guide-first Concept B interface and Plex-first playback choice contract."""
import unittest
from pathlib import Path

HTML=(Path(__file__).resolve().parents[1]/"app"/"static"/"index.html").read_text(encoding="utf-8")

class ConceptBGuideUiTests(unittest.TestCase):
    def test_guide_is_the_home_screen(self):
        self.assertIn('class="container guide-app"',HTML)
        self.assertIn('id="guide"',''.join(HTML.split()))
        self.assertIn('id="guidedetail"',HTML)
        self.assertNotIn("<body><header>",HTML)
        self.assertNotIn('<h2>Search your Plex catalog</h2>',HTML)
        self.assertIn('id="settingsdrawer"',HTML)
        self.assertIn('id="searchdrawer"',HTML)
        self.assertIn('id="openfilters"',HTML)
        self.assertIn('id="guidefiltermenu"',HTML)
        self.assertIn('id="guidefiltermenu" class="guide-popover"',HTML)
        self.assertIn('id="guidecontrols"',HTML) # Retained for legacy bindings

    def test_filters_and_channel_controls_are_in_menus(self):
        self.assertIn('data-guide-filter="all"',HTML)
        self.assertIn('data-guide-filter="favorites"',HTML)
        self.assertIn('data-guide-filter="plex"',HTML)
        self.assertIn('data-guide-filter="tablo"',HTML)
        self.assertIn('data-guide-filter="philo"',HTML)
        self.assertIn('id="filtermanage"',HTML)
        self.assertIn('id="settingsmanage"',HTML)
        self.assertIn('function closeGuideDrawer()',HTML)
        self.assertIn('function updateGuideFilterMenu()',HTML)

    def test_plex_is_primary_with_start_mode_choice(self):
        self.assertIn('id="guidewatchplex" class="guide-action-primary"',HTML)
        self.assertIn('id="guidewatchsecondary"',HTML)
        self.assertIn("function openGuidePlexChoices()",HTML)
        self.assertIn('id="playjoin" type="button" hidden>▶ Watch From Now',HTML)
        self.assertIn('id="playbegin" type="button"',HTML)
        self.assertIn('document.getElementById("playjoin").onclick=()=>launchPlex("join")',HTML)
        self.assertIn('document.getElementById("playbegin").onclick=()=>launchPlex("beginning")',HTML)
        self.assertIn('$("playjoin").hidden=!(e.plex&&!isMulti',HTML)
        self.assertIn("const split=Array.isArray(program.plex.parts)",HTML)
        self.assertIn('verifiedPhiloPlaybackUrl(program)',HTML)
        self.assertIn('if(selectedProgram.channel.source==="tablo")launchTabloLive()',HTML)

    def test_selected_plex_program_artwork_is_token_isolated(self):
        self.assertIn('id="guideartimage"',HTML)
        self.assertIn('artworkImage.src="/api/guide/art/"',HTML)
        self.assertIn('artworkImage.onerror=()=>{artworkImage.hidden=true}',HTML)
        self.assertIn('artworkImage.onload=()=>{if(selectedProgram===program)',HTML)

    def test_focus_updates_program_strip_without_opening_modal(self):
        self.assertIn('el.onclick=()=>showGuidePreview(shownPrograms[index])',HTML)
        self.assertIn('el.addEventListener("focus",()=>showGuidePreview',HTML)
        self.assertIn('classList.toggle("return-selected"',HTML)
        self.assertIn('if(shownPrograms.length)showGuidePreview(',HTML)

if __name__=="__main__":
    unittest.main()
