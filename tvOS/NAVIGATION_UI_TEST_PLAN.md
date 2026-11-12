# Apple TV Navigation Test Specification

Status: **planned; not implemented or passing**. Scope: tvOS only, simulator offline fixture. Do not alter live Plex, Tablo, Philo or iPhone behavior.

## Prerequisite / release gates

1. The app must launch in tvOS Simulator with `--visual-preview --ui-test-mode`, render real guide content, and expose `guide.root` by accessibility. A 1920x1080 wallpaper PNG is **not** a valid preview.
2. Both simulator screenshot and navigation UI tests must pass on the working branch before proposing any TestFlight release. A static screenshot **cannot** prove remote navigation, smoothness, or video playback.
3. The working branch is `tvos-auto-visual-development` / draft PR #25. Do not merge or publish without approval.

## Implementation plan and precise accessibility identifiers

Implement these stable identifiers on existing SwiftUI components, and avoid changing navigation semantics just to expose accessibility state. Identifiers must be attached to elements that XCTest can actually discover (not exclusively to decorative, ignored containers).

| Identifier | View / ownership | Required semantics |
|---|---|---|
| `guide.root` | TVGuideScreen outer accessible content (not the background-only ZStack) | Existence means actual guide UI rendered |
| `guide.timeline` | timeline header/content | Exists with visible schedule labels |
| `guide.station.<channelIndex>` | station column view for each visible row | Exists for a displayed station |
| `guide.program.<channelIndex>.<programIndex>` | program Button inside timelineRow | Selectable/hittable when on-screen |
| `guide.selection` | dedicated **read-only** accessibility status Text near guide root | `accessibilityValue`: `channel=<int>;program=<int>;key=program-<channel>-<program>`; update whenever `selectedChannel`, `selectedProgram`, or `tvSelection` changes |
| `guide.selection.visible` | optional read-only accessibility status Text | `accessibilityValue` `true` only if selected channel is inside `visibleChannelRange` and the selected program is actually in the rendered window; for horizontal scrolling, prefer testing `isHittable` on the selected tile as well |
| `guide.rail.search` | search rail Button | Selectable via left at first visible program |
| `guide.rail.filter` | filter rail Button | Stable focus destination |
| `guide.rail.settings` | settings rail Button | Stable focus destination |
| `guide.plex.modal` | UniversalPlexChoiceScreen dialog root | Exists after selecting a matched program |
| `guide.plex.beginning` | Plex choice first Button | Correct source choice |
| `guide.plex.current` | Plex choice second Button when available | Optional on-air source choice |
| `guide.plex.philo` | Philo alternate choice, when present | Present for dual-source matching only |
| `guide.plex.tablo` | Tablo alternate choice, when present | Present for dual-source on-air only |
| `guide.plex.cancel` | Cancel choice Button | Returns cleanly to guide |
| `guide.error` | error alert / recovery message | Optional, useful for diagnosing unexpected launches |

The UIKit `UniversalTVGuideRemoteReceiver` is the **sole tvOS directional-input receiver**. It calls `moveTVFocus` and `activateFocused`, which update the custom `tvSelection` state. XCTest should therefore assert `guide.selection.accessibilityValue`, not the system focused element alone. For `guide.program.*`, use the exact visible array indices used in `timelineRow(channel,index:)`, not channel numbers printed in the UI.

Avoid a continually visible test/debug panel: expose the read-only state only when `--ui-test-mode` is present. Ensure the representation is accessible to XCUITest even when the text is visually hidden; don't use `.hidden()` or `.accessibilityHidden(true)` for status.

## Fixed fixture requirements

Extend `TVVisualPreview.channels()` (or a separate deterministic `TVUITestFixture`) with:
- At least 20 channels, each with 5+ sequential programs, enough to require vertical window movement.
- Alternating short/long scheduled programs, including a short 30-minute and long 120-minute tile on adjacent rows, to reproduce focus misalignment.
- Known Plex-matched on-air program that opens `UniversalPlexChoiceScreen` without starting external playback.
- Optional multiple-source program with Philo alternate and one with Tablo alternate; selecting sources should not launch third-party apps or video in UI tests.
- Static fixed clock in UI test mode so program boundaries, visible indices and expected selection remain deterministic. Keep real time for ordinary preview/live mode.

Expected selection examples must be driven from fixture-defined values rather than hardcoded assumptions about the current clock.

## Tests (initial XCTest target `UniversalGuideTVUITests`)

| Test method | Exact sequence | Pass criteria |
|---|---|---|
| `testGuideLaunchAndSelection` | Launch with UI test fixture. Wait for `guide.root`, `guide.timeline`, `guide.selection`. | Root and timeline exist, selected tile exists and is hittable, status reports valid channel/program indices. |
| `testDownFifteenRowsTracksVisibleSelection` | Record selection, send 15 `XCUIRemote.shared.press(.down)` presses. | Channel index advances exactly 15 (if fixture supports it), selected tile is hittable, window follows selected row, no out-of-bounds channel. |
| `testUpReturnsToStartingChannel` | Down 10, Up 10. | Starting channel and program/time alignment recovered; selected tile visible. |
| `testRightLeftKeepsCorrectProgram` | From known channel, Right 3, Left 3. | Program selection follows schedule as expected and returns to starting program; selected tile remains visible. |
| `testRapidVerticalMovement` | Send 15 Down then 15 Up button presses with minimal pauses. | No skipped/double channel increments; selection always valid; final row returns to start and tile is hittable. |
| `testPlexSelectAndBack` | Navigate to fixture Plex match, press `.select`, wait for `guide.plex.modal`, press `.menu`. | Plex choices appear; modal closes; guide root and prior selection are restored. |
| `testSidebarRecovery` | Move Left at first visible program to rail, then Right. | `guide.rail.search` reachable; Right returns to prior program, without app exiting. |
| `testHorizontalTimelineFollow` | Repeated Right presses across the initial screen-width boundary. | Correct program index after each input; selected tile always hittable and remains on-screen; timeline follows time. |

For tvOS, use `XCUIRemote.shared.press(.up/.down/.left/.right/.select/.menu)`. A standard XCUITest target must be added to `tvOS/project.yml` through XcodeGen and launched with `xcodebuild test -destination 'platform=tvOS Simulator,id=<SIMULATOR_UDID>'`. Save XCTest results (`.xcresult`), test logs, and failure screenshots as GitHub artifacts. Use a separate job or workflow `tvos-navigation-tests.yml` triggered by tvOS/Shared changes on the isolated working branch; do not block the current TestFlight workflow until simulator/UI test reliability is validated.

## Known blockers and next implementation order

- Run #43226680067 **compiled**, but screenshot job **failed** after five attempts: edge-detection score indicated **wallpaper, no guide UI**. tvOS simulator logs show a `scene-create` watchdog/provision warning while the app process exists. Investigate whether the root SwiftUI scene renders too slowly, whether the custom transparent UIKit remote receiver occludes the app, and whether the screenshot is capturing the correct TVOut display. The failure is not yet attributed conclusively to any one cause.
- First, get `guide.root` rendered and accessibility-discoverable in the simulator; failing test `testGuideLaunchAndSelection` should report precisely where launch stalls.
- Second, add identifiers and deterministic UI test state, then a single launch test.
- Third, add directional movement/scrolling tests.
- Fourth, add Plex modal and return-navigation tests.
- Preserve the original iPhone behavior and working playback services throughout.
