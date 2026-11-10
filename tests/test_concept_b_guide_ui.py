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

    def test_movie_artwork_uses_cached_tmdb_before_plex_fallback(self):
        self.assertIn('const isMovie=program.entry.program_kind==="movie"',HTML)
        self.assertIn('params.set("kind","movie")',HTML)
        self.assertIn('params.set("year",String(program.entry.year))',HTML)
        self.assertIn('result.source==="TMDB"?"TMDB":"TVmaze"',HTML)
        self.assertIn('if(!result.image_url){plexFallback();return;}',HTML)
        self.assertIn('TMDB_API_KEY',HTML)

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

    def test_philo_fallback_label_and_bottom_panel_alignment(self):
        self.assertIn('<strong>Schedule unavailable</strong><span>▶ Watch on Philo</span>',HTML)
        self.assertNotIn(' · Test Link',HTML)
        self.assertIn('tags.insertBefore(tag,$("guideartcredit"))',HTML)
        self.assertIn('flex:0 0 clamp(108px,12vw,175px)',HTML)
        self.assertIn('justify-content:flex-start;align-content:center;gap:7px',HTML)
        self.assertIn('flex:0 0 auto;margin:0;align-self:center',HTML)

    def test_missing_philo_schedule_is_a_remote_navigable_tile(self):
        self.assertIn('class="epg-program epg-no-schedule"',HTML)
        self.assertIn('data-philo-url=',HTML)
        self.assertIn('".epg-program[data-program],.epg-program[data-philo-url]"',HTML)
        self.assertIn('button.onclick=()=>{',HTML)
        self.assertIn('window.location.href=url',HTML)
        self.assertIn('.guide-main .epg-tracks .epg-no-schedule:focus-visible',HTML)

    def test_remote_can_reach_time_navigation(self):
        self.assertIn('const timeControls=["earlier","now","later"]',HTML)
        self.assertIn('timeControls[next].focus({preventScroll:true})',HTML)
        self.assertIn('if(direction==="ArrowDown"){remoteGuideFocus();return true}',HTML)
        self.assertIn('if(direction==="ArrowUp"&&railIndex===0)',HTML)
        self.assertIn('if(event.key==="ArrowUp")$("now").focus({preventScroll:true})',HTML)

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

    def test_overlapping_airings_are_clipped_before_rendering(self):
        self.assertIn("const airings=ch.programs.map",HTML)
        self.assertIn("const a=Math.max(airings[n].a,previousEnd)",HTML)
        self.assertIn("const b=Math.min(airings[n].b",HTML)
        self.assertIn("if(b<=a)continue;",HTML)
        self.assertNotIn("width:calc('+width+'% - 3px)",HTML)

    def test_remote_sidebar_and_back_to_now(self):
        self.assertNotIn('id="guidenow"',HTML)
        self.assertIn("function returnGuideToNow()",HTML)
        self.assertIn('$("now").onclick=returnGuideToNow;',HTML)
        self.assertIn('const rail=["opensearch","openfilters","opensettings"]',HTML)
        self.assertIn('if($("guide").scrollLeft>1)$("guide").scrollLeft=0;',HTML)
        self.assertIn('if(event.key==="ArrowUp")$("now").focus',HTML)

    def test_details_are_inline_and_philo_watches_channel_only(self):
        self.assertNotIn('id="guidedetails"',HTML)
        self.assertIn('const channelUrl=verifiedPhiloPlaybackUrl(program)',HTML)
        self.assertIn('function verifiedPhiloPlaybackUrl(program)',HTML)
        self.assertIn('$("philoepisode").hidden=true',HTML)
        self.assertIn('$("philoshow").hidden=true',HTML)

    def test_settings_does_not_replace_the_guide(self):
        self.assertIn('id="channelsettings"',HTML)
        self.assertIn('id="channelsettingsback"',HTML)
        self.assertIn('id="channelsettingslist"',HTML)
        self.assertIn('target.innerHTML=output',HTML)
        self.assertIn('if(drawer&&(direction==="ArrowUp"||direction==="ArrowDown"))',HTML)
        self.assertNotIn('$("guide").innerHTML=\'<div style="padding:14px">\'+output',HTML)

    def test_tvmaze_credit_does_not_steal_synopsis_line(self):
        self.assertIn('tags.querySelectorAll(":scope > span").forEach(badge=>badge.remove())',HTML)
        self.assertNotIn('tags.replaceChildren()',HTML)
        self.assertIn('class="guide-source-tags">',HTML)
        self.assertIn('guide-detail-content .guide-image-credit',HTML)
        self.assertIn('guide-detail-content #guidepreviewdesc{flex-shrink:0}',HTML)

    def test_bundled_sports_and_news_posters_fallback(self):
        root=Path(__file__).resolve().parents[1]/"app"/"static"
        self.assertTrue((root/"artwork-sports.svg").is_file())
        self.assertTrue((root/"artwork-news.svg").is_file())
        self.assertIn('function guideGenericArtwork(program)',HTML)
        self.assertIn('return "/artwork/news.svg"',HTML)
        self.assertIn('return "/artwork/sports.svg"',HTML)
        self.assertIn('if(!text||e.no_guide_data)return null',HTML)
        self.assertIn('artworkImage.src=genericPoster',HTML)
        self.assertIn('const genericPoster=guideGenericArtwork(program)',HTML)
        self.assertIn('artworkImage.src=local.src',HTML)
        self.assertIn('artworkImage.src=img.src',HTML)

    def test_preview_buttons_have_reserved_height_inside_panel(self):
        self.assertIn('height:clamp(240px,30dvh,310px)',HTML)
        self.assertIn('flex:0 0 clamp(240px,30dvh,310px)',HTML)
        self.assertIn('box-sizing:border-box;',HTML)
        self.assertIn('flex:1 1 0;',HTML)
        self.assertIn('flex:0 0 66px;',HTML)
        self.assertIn('min-height:66px;',HTML)
        self.assertIn('height:48px;',HTML)
        self.assertIn('overflow:visible;',HTML)

    def test_preview_description_and_button_focus_do_not_overlap(self):
        self.assertIn('-webkit-line-clamp:2',HTML)
        self.assertIn('max-height:2.8em',HTML)
        self.assertIn('min-height:66px',HTML)
        self.assertIn('padding:6px 5px',HTML)
        self.assertIn('outline-offset:2px',HTML)

    def test_preview_text_and_provider_choice_navigation(self):
        self.assertIn('.guide-detail-content #guidepreviewdesc{',HTML)
        self.assertIn('max-height:3.9em',HTML)
        self.assertIn('white-space:normal',HTML)
        self.assertIn('margin-top:auto',HTML)
        self.assertIn('const plexChoice=$("guidewatchplex"),liveChoice=$("guidewatchsecondary")',HTML)
        self.assertIn('current===plexChoice||current===liveChoice',HTML)
        self.assertIn('direction==="ArrowRight"?liveChoice:plexChoice',HTML)
        self.assertIn('target.focus({preventScroll:true})',HTML)

    def test_fixed_preview_artwork_and_horizontal_scroll(self):
        self.assertIn('height:clamp(190px,27dvh,275px)',HTML)
        self.assertIn('object-fit:contain!important',HTML)
        self.assertIn('function guideScrollToProgram(button)',HTML)
        self.assertIn('guideScrollToProgram(target)',HTML)
        self.assertIn('pane.scrollLeft=Math.max(0,pane.scrollLeft',HTML)

    def test_single_action_plays_on_ok_without_bottom_buttons(self):
        self.assertIn('function activateGuideProgram(program)',HTML)
        self.assertIn('const chooseSource=Boolean(e.plex?.rating_key)',HTML)
        self.assertIn('playPlex.hidden=!chooseSource;',HTML)
        self.assertIn('secondary.hidden=!chooseSource;',HTML)
        self.assertIn('if(hasTablo){launchTabloLive();return}',HTML)
        self.assertIn('if(channelUrl){persistGuideState();window.location.href=channelUrl;return}',HTML)
        self.assertIn('else launchPlex("beginning");',HTML)
        self.assertIn('if(split||isLive)openGuidePlexChoices();',HTML)
        self.assertIn('activateGuideProgram(shownPrograms[index]);',HTML)

    def test_focus_updates_program_strip_without_opening_modal(self):
        self.assertIn('el.onclick=event=>{',HTML)
        self.assertIn('showGuidePreview(shownPrograms[index]);',HTML)
        self.assertIn('activateGuideProgram(shownPrograms[index]);',HTML)
        self.assertIn('el.addEventListener("focus",()=>showGuidePreview',HTML)
        self.assertIn('classList.toggle("return-selected"',HTML)
        self.assertIn('if(shownPrograms.length)showGuidePreview(',HTML)

if __name__=="__main__":
    unittest.main()
