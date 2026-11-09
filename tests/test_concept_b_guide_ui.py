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
        self.assertIn('local.src="/api/guide/art/"',HTML)
        self.assertIn('local.onerror=()=>{}',HTML)
        self.assertIn('const result=await req("/api/guide/artwork?"+params.toString())',HTML)
        self.assertIn('img.src=result.image_url',HTML)
        self.assertIn('if(!result.image_url){plexFallback();return;}',HTML)
        self.assertIn('id="guideartcredit"',HTML)
        self.assertIn('requestId===guideArtworkRequestId',HTML)

    def test_program_details_remain_in_viewport(self):
        self.assertIn('.guide-app{height:100dvh;overflow:hidden}',HTML)
        self.assertIn('.guide-main{flex:1 1 0;min-height:0;overflow:hidden}',HTML)
        self.assertIn('.guide-detail{flex:0 0 auto;max-height:33dvh;overflow:auto}',HTML)
        self.assertIn('if(action)action.focus({preventScroll:true});',HTML)

    def test_live_first_navigation_without_calendar(self):
        self.assertNotIn('id="jumpdate"',HTML)
        self.assertNotIn('id="nextday"',HTML)
        self.assertNotIn('$("jumpdate").onchange',HTML)
        self.assertNotIn('$("nextday").onclick',HTML)
        self.assertIn('id="now" type="button" class="guide-today"',HTML)

    def test_directional_navigation_never_jumps_diagonally_over_program_rows(self):
        self.assertIn('function guideProgramButtons(row)',HTML)
        self.assertIn('function guideChooseByTime(programs,time)',HTML)
        self.assertIn('current.closest(".epg-row")',HTML)
        self.assertIn('programs[here+1]||null',HTML)
        self.assertIn('programs[here-1]||null',HTML)
        self.assertIn('for(let next=rowIndex+delta;next>=0&&next<rows.length;next+=delta)',HTML)
        self.assertIn('target=guideChooseByTime(nextPrograms,guideNavigationTime)',HTML)
        self.assertIn('event.stopPropagation()',HTML)
        self.assertIn('guideNavigationTime=el.offsetLeft+el.offsetWidth/2',HTML)
        self.assertNotIn('const score=primary+secondary*2;',HTML)

    def test_keyboard_remote_arrow_enter_and_back(self):
        self.assertIn('function remoteFocusNext(direction)',HTML)
        self.assertIn('function remoteGuideFocus()',HTML)
        self.assertIn('function remoteBack()',HTML)
        self.assertIn('if(key==="Enter"||key===" ")',HTML)
        self.assertIn('"ArrowUp","ArrowDown","ArrowLeft","ArrowRight"',HTML)
        self.assertIn('"Backspace","Escape","BrowserBack"',HTML)
        self.assertIn('remoteTypingTarget(event.target)',HTML)
        self.assertIn('event.stopImmediatePropagation()',HTML)
        self.assertIn('},true);',HTML)
        self.assertIn('if(!$("plextrackpanel").hidden)',HTML)
        self.assertIn('closePlexPlayer();remoteGuideFocus()',HTML)

    def test_focus_updates_program_strip_without_opening_modal(self):
        self.assertIn('el.onclick=event=>{',HTML)
        self.assertIn('showGuidePreview(shownPrograms[index]);',HTML)
        self.assertIn('if(event.detail===0)',HTML)
        self.assertIn('el.addEventListener("focus",()=>showGuidePreview',HTML)
        self.assertIn('classList.toggle("return-selected"',HTML)
        self.assertIn('if(shownPrograms.length)showGuidePreview(',HTML)

if __name__=="__main__":
    unittest.main()
