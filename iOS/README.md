# Universal Guide — iPhone Backbone test app

Native iOS companion for the existing Universal Guide Docker backend and tvOS app.

## Backbone controller behavior (iPhone test build)

- **Y**: temporary Play/Pause shortcut, even if the playback controls are hidden. Later replace with the remote's media button after identifying its input.
- **A / OK**: while watching, reveal the web player's control overlay with Play/Pause focused; a subsequent A activates the focused button.
- **D-pad Up/Down**: reveal the overlay if hidden, or navigate among its controls when visible.
- **D-pad Left/Right**: skip back 20 seconds or forward 30 seconds while the overlay is hidden; when visible, navigate controls (or adjust the focused slider).
- **X**: in the guide, expand/minimize the program preview. On expansion, focus the first visible playback action. In Plex playback, X opens Chapters (or Audio if chapters are unavailable).
- **LB**: cycle focus through the persistent left rail (Search, Filters, Settings); Up/Down then select its entries, A opens them.
- **RB**: return focus to the guide; **B** goes back/closes.
- **Go Live**: reachable on the Tablo overlay (formerly Y, reassigned to temporary Play/Pause).

These commands work through the native GameController-to-WKWebView bridge. The original Unraid web player still owns the buffered/disk-backed HLS stream and DVR timeline; this patch changes only iPhone controller behavior.

## Current playback architecture

**The TestFlight app now launches the actual Unraid web guide in a full-screen WKWebView.** This intentionally replaces the divergent native Swift Plex/Tablo player. The web app continues to own the live file-backed Tablo stream, DVR seek window, pause/resume, Go Live, Plex chapters, subtitles, audio, and matching. The Backbone maps buttons to those **existing** web controls. The web page's fixed left rail provides Search, Filters and Settings; LB selects/cycles that rail, RB returns to the guide. A uses the focused control and B goes back.

This requires the latest web guide to be running on Unraid. The iPhone app must reach the server over your LAN or trusted private network. Philo handoff via Universal Links still needs validation, and WKWebView/controller playback needs a physical iPhone test. The old native Swift playback views remain in the codebase temporarily as non-active code, not as the app's player.

## Historical first-pass native prototype

## What it does

- Landscape, dark timeline grid modeled on the existing web EPG (sticky station column, time header, current-time marker, green Plex-owned programs).
- Backbone / Game Controller D-pad to move between channel rows and programs; A to open details, B to go back, X to show playback options, Y to jump to a current airing, shoulder buttons to move a page. An on-screen input/focus diagnostic is available.
- Touch remains available for initial URL entry and troubleshooting.
- Uses existing `/api/guide`, Plex `/api/player/start`, and Tablo `/api/tablo/live/start?channel_id=...` routes. Tablo playback uses the existing HLS session and native AVPlayer; native player pause/resume and a reconnect-to-live button are available for initial testing. Advanced Tablo DVR rewind/fast-forward using the backend's timeshift API is a follow-up, not yet implemented.
- Philo uses only curated HTTPS URLs provided by the backend and attempts normal iOS Universal Link launch to Philo. It cannot guarantee launch, autoplay or tvOS equivalence.
- The app does **not** store or need Plex/Philo/Tablo passwords.

## Build

GitHub Action **Build iPhone Backbone preview** runs an unsigned iPhone Simulator compilation for each PR. Passing proves compilation, not on-device controller or playback functionality.

To publish to TestFlight, create a **separate iOS app** in App Store Connect with bundle ID `co.thejonesfamily.universalguide.iphone`. Register that identifier in the Apple Developer portal and create an **iOS App Store** provisioning profile. The existing tvOS provisioning profile is not valid for iOS. Reuse the Apple distribution certificate only if it is valid for the iOS profile. The iOS TestFlight upload workflow can be added once the profile is configured.

## Notes

- The iPhone and Unraid container must be able to reach each other (LAN or approved private network). Enter e.g. `http://192.168.1.10:8787` under Settings. Do not expose the local API to the public internet.
- Playback of a legacy Tablo stream is served by the existing transcoding endpoint, not direct use of the Tablo device. Closing the player calls DELETE to free the session.
- The Tablo backend's 120-second idle cleanup could close an unused paused stream; consider this during longer controller tests.
- On iPhone, a Backbone D-pad is **not** identical to the Apple TV focus engine. Final tvOS focus and Philo handoff need physical Apple TV verification.
