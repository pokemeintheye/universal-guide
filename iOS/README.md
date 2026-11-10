# Universal Guide — iPhone Backbone test app

Native iOS companion for the existing Universal Guide Docker backend and tvOS app.

## What it does

- Landscape, dark timeline grid modeled on the existing web EPG (sticky station column, time header, current-time marker, green Plex-owned programs).
- Backbone / Game Controller D-pad to move between channel rows and programs; A to open details, B to go back, X to show playback options, Y to jump to a current airing, shoulder buttons to move a page. An on-screen input/focus diagnostic is available.
- Touch remains available for initial URL entry and troubleshooting.
- Uses existing `/api/guide`, Plex `/api/player/start`, and Tablo `/api/tablo/live/start?channel_id=...` routes. Tablo playback uses the existing HLS session and native AVPlayer; pause/resume, 15-second player seeks and a Return to Live option are available for testing, with limitations of the live DVR window.
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
