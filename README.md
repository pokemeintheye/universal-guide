# Universal TV Guide

Unified TV guide prototype for Plex, local OTA listings and Philo schedules, now including experimental Plex HLS playback.

**Status:** Initial Plex metadata indexing and optional XMLTV guide import. No native Apple TV app, or verified app deep links yet. This is an experimental prototype.

## Install from Unraid Docker > Add Container

1. Wait for the **Publish Docker image** GitHub Action to finish successfully.
2. Ensure the new package under your GitHub account is **public**, otherwise Unraid needs GHCR authentication.
3. Set **Repository** to `ghcr.io/pokemeintheye/universal-guide:latest`.
4. Set **Network** to Bridge, mapping **host port 8787** to **container port 8787** (TCP).
5. Set Path mapping: `/mnt/user/appdata/universal-guide` (host) to `/data` (container), read/write.
6. Add environment variables:
   - `PLEX_URL`: `http://YOUR-UNRAID-IP:32400` (if Plex uses host networking).
   - `PLEX_TOKEN`: your Plex API token (secret; **never commit it**).
   - `REFRESH_MINUTES`: `30`
   - `GUIDE_XMLTV`: `/data/guide.xml`
   - `TABLO_URL`: `http://YOUR-TABLO-IP:8885` (legacy network Tablo)
   - `TABLO_REFRESH_HOURS`: `6`
7. Ensure `/mnt/user/appdata/universal-guide` exists and is writable by container UID **10001**.
8. Launch, then browse `http://YOUR-UNRAID-IP:8787`.

Example setup of data directory (Unraid terminal):
```bash
mkdir -p /mnt/user/appdata/universal-guide
chown 10001:10001 /mnt/user/appdata/universal-guide
```

The app reads Plex API metadata without mounting media directories. When playback is requested, it proxies a Plex HLS transcode playlist and stream segments to the browser, keeping the Plex token on the Unraid backend. It saves Plex titles and episode metadata in SQLite.

## Optional Docker Compose

Copy `.env.example` to `.env` locally and set the Plex URL and token. Run `docker compose up -d` from a checkout. `.env` is gitignored. The compose file uses the published GHCR image, not a local build.

## Optional live guide data

Place an XMLTV-format guide at `/mnt/user/appdata/universal-guide/guide.xml`. Click **Reload guide.xml** or allow the background watcher to import it. Tablo can be read directly from the local legacy API; Philo XMLTV feeds must be provided independently.

## Updating

The GitHub Actions workflow rebuilds GHCR `latest` whenever code is committed to `main`. Once published, pull a new image through the Unraid Docker tab.

## Security

- Restrict port 8787 to your trusted LAN. This prototype has no authentication.
- Plex tokens provide broad access to Plex despite the app using only metadata endpoints; keep the token only in Unraid settings, not GitHub.
- Avoid exposing the service directly to the internet.
- No media directory mounts are required.

## Planned

- Add selected OTA and Philo channel groups and schedules.
- Improve program matching using external metadata identifiers.
- Test Apple TV app handoff and plan a native tvOS client.

## Legacy Tablo OTA guide

Set `TABLO_URL` as an Unraid environment variable to the local address of your network Tablo, e.g. `http://192.168.1.50:8885`. Click **Refresh Tablo** in the web interface (or allow the background scan approximately every six hours). The integration makes read-only GET requests to `/guide/airings` and the returned individual program records. It uses broadcast start times, durations, channels, and episode metadata. Existing Plex and XMLTV metadata stays independent.

To avoid overloading Tablo the import uses no more than six concurrent requests, caps the guide at 4,000 program references, and reports truncation. The importer may require changes for different Tablo firmware. There is no video/recording access. Do not expose this application to the public internet.

## Philo-like network listings (free, metadata only)

The backend fetches EPGTalk's compressed US national XMLTV feed automatically every 24 hours (or click **Refresh Philo EPG**). Matching is based on the published Philo Essential channel names and conservative aliases. Only clearly matched channels are imported; channels missing from the feed won't appear. This feed is not provided by Philo, and the listings do not prove a particular channel is included in your individual plan. No Philo credentials, video playback, or recordings are accessed. The download uses a fixed trusted HTTPS URL with size limits.

Use **Manage channels** to mark channels as favorites or hide them. Settings persist in SQLite in /data. **Favorites** filters the guide. Programs imported from Tablo and manual XMLTV remain independent. Future improvements include official lineup selection for add-on tiers and channel matching overrides.

## Matching accuracy and duplicate schedules
Free guide feeds may contain multiple station IDs for one network. The Philo importer selects the source with the greatest upcoming program coverage per canonical network name, and stores it under a stable ID. Existing channel favorites/hidden settings are migrated when practical. A source variant may differ from Philo's actual East/West feed; this is not an official Philo lineup.

Plex badges are **conservative**: a program marked as a movie can match only Plex movies; series episodes can match only Plex episodes with season/episode IDs or matching episode subtitles. Unclassified entries will not be matched to movies. This reduces false positives (e.g. a Batman TV episode incorrectly matching a Batman film) but may temporarily miss some real movies where the free feed lacks program categories.

## Unraid icon and WebUI
The repository includes `templates/universal-guide.xml` and `assets/icon.png`. Existing containers do **not** automatically pick up new template metadata when the image updates. Open your existing container's **Edit** page, switch to **Advanced View**, set:

- **Icon URL:** `https://raw.githubusercontent.com/pokemeintheye/universal-guide/main/assets/icon.png`
- **WebUI:** `http://[IP]:[PORT:8787]/`

Then Apply. Keep your existing environment variables, mappings, and credentials. Alternatively, install from the provided XML template for a fresh deployment.

## Guide navigation and program information
The browser guide provides **Earlier** and **Later** navigation (two-hour steps), **Now**, a live-time indicator, separate **Local** and **Philo** filters, and clickable program tiles. Clicking a program shows its scheduled time, description, episode information if supplied, and Plex availability. No app is launched or media is accessed.

Legacy Tablo channels show the network (e.g. **CBS**) above the broadcast channel number (e.g. **2.1**). The app reads these names directly from Tablo's `/guide/channels` metadata during a **Refresh Tablo**; after upgrading, run that refresh once to populate the network labels. If the station has no network field, the guide falls back to its callsign or number.

These features are experimental and depend on guide data completeness. More precise artwork/episode metadata would require an additional metadata provider.

## Plex playback handoff diagnostic (first playback milestone)
When a guide program has a confirmed Plex match, its program-details popup includes **Open this title in Plex**. The backend resolves the Plex server's machine identifier through its private API and produces a token-free Plex Web URL for that exact library item. You must already be signed in to Plex in the target browser.

**This is a navigation diagnostic, not finished playback.** It opens Plex's item details, not immediate play, and does not yet support joining a broadcast in progress. In particular, this does not establish whether the Apple TV Plex app can accept the same handoff. The future native tvOS client or a direct Plex playback component must be tested separately before considering the project viable.

## TV-episode matching diagnostics
The free national EPG feed may contain XMLTV `episode-num` values (`xmltv_ns`, `onscreen`, or `common`). The importer now stores exact season and episode numbers when present. Existing databases receive new nullable columns automatically. Run **Refresh Philo EPG** (and optionally **Reload guide.xml**) after upgrading to populate them. Plex matching remains strict: exact season+episode, or matching episode subtitle; a TV series title alone does not establish ownership of the airing episode.

For an unmatched example, use `http://YOUR-UNRAID-IP:8787/api/match-diagnostics?q=The%20Office` on your local network. It returns the available guide episode identifiers and whether that particular listing matched Plex; it does not return credentials.

## Fast guide browsing and future program search
Plex metadata matching now uses in-memory title/episode hash indexes instead of searching the full Plex catalog for every airing, dramatically reducing guide query work. **Earlier/Later** move two hours; **+1 day** and **Jump to date** support further browsing. The **Find upcoming** box searches scheduled program titles (e.g. The Office), with **Go to airing** jumping directly to the matching time. This is guide search, not a guarantee that a third-party provider has that title available for playback. Results depend on the available feed horizon, generally around seven days.

## Experimental direct Plex playback (browser first)

When you select a green **In Plex** program, the program details offer:

- **Start from Beginning**: request a Plex HLS stream starting at time zero.
- **Join in Progress**: available only during the program's airing. Map the elapsed fraction of the broadcast timeslot to the runtime returned by Plex, approximately removing commercial-break time. This is an estimate, not exact scene synchronization.
- **Open this title in Plex**: fallback to Plex Web if the experimental player does not work.

The browser player receives only session-specific, opaque local URLs. The Unraid app talks to Plex with its API token and relays playlists, video and audio segments. The browser does not receive the Plex token or a direct Plex URL. This increases Unraid server network traffic and requires the Plex server's transcoder to be working. The test may require a Plex Pass depending on codec and transcoding capabilities.

Browsers with native HLS can use it directly. Other browsers (including Chrome) load **hls.js** from a pinned jsDelivr CDN version; allow the browser to access the CDN for this initial test. The player is **not** a native Apple TV app and is **not yet verified with real Plex streams**. If playback fails, please report the exact error from the player or Docker logs.

**Security:** This experimental container has no user authentication. It must remain accessible only on a trusted LAN, not exposed to the public internet. Session URLs allow playback until they expire. No Plex files need to be mounted.

Tablo and Philo playback are **not implemented** by this milestone. Only select Plex-matched programs to test.

## Reopening the same Plex title (player lifecycle fix)
The Plex player now explicitly requests termination of its server-side transcoding session when closed, rather than deleting only the guide-side session. The browser also destroys the HLS instance and replaces its video element with a fresh one before another playback, preventing stale MediaSource state from being reused. A 25-second startup message helps diagnose a silent stall. Test the sequence **episode A → close → episode A again → close → episode B → episode A**. If the same title still stalls, note the browser error or Unraid Docker logs. This fix has not yet been validated against a live Plex server.

## Repeat-playback diagnostics (November 2026)
When a movie or episode plays once but immediately reopening the same title fails with browser error 4, the player now reports both the hls.js error category/details (when available) and safe Plex request counters: number of playlist and segment requests, transcoding-decision status, and most recent HTTP status. These diagnostics contain no Plex token, upstream URL or file path. Playback start requests now carry an explicit `X-Plex-Session-Identifier` equal to the unique per-playback transcoder session and register the session using the universal transcode decision endpoint before the first playlist request. These are compatibility experiments; repeat-playback must be validated with the real Plex server.

After updating, run **episode A → close → episode A**. If it fails, copy the **full player error**, particularly the Plex requests, decision HTTP, last HTTP and hls.js details. The browser's generic error 4 alone is insufficient to identify whether the media could not be fetched or decoded.

## Experimental live Tablo tuner playback

The guide now shows **Watch Live on Tablo** for an airing that is currently live on an indexed Tablo station. It makes a local `POST /guide/channels/{id}/watch` request against the configured `TABLO_URL`. On a successful JSON response containing `playlist_url`, the Unraid Docker container starts its bundled FFmpeg transcoder to convert over-the-air video (which may contain MPEG-2 video/AC3 audio) to browser-compatible H.264/AAC HLS. The video appears in the existing player, separate from Plex playback. This uses Tablo's *live tuner*, not its scheduled recording or a Plex copy.

**Warning:** The Tablo `/watch` endpoint is not officially documented for third parties and may require authenticated and HMAC-signed requests. Existing read-only guide access does **not** prove that watch access is permitted. If the request returns HTTP 401 or 403, the UI displays that diagnostic; the application does not attempt to defeat authentication. The new watch feature has not yet been tested on the user's physical Tablo device. No login credentials need to be supplied at this stage.

**Requirements and cost:** The container includes FFmpeg; live video conversion uses Unraid CPU resources (up to two simultaneous sessions) and consumes a Tablo tuner while playing. A channel may take 15–30 seconds to buffer. Only channels already indexed from the local Tablo device can be tuned. All actions are intended for a trusted LAN with no public internet exposure. The stream is stopped when the Close player button is used. A lost/abandoned stream is also stopped on the next live endpoint request once idle for 85 seconds.

**Test procedure:** Update the container; select a *currently airing* local Tablo program; click **Watch Live on Tablo**. If it fails, provide the exact popup error. If it starts, verify picture, audio, and closing/reopening the same channel. Don't use an airing later in the guide to try to tune early.

**Note:** On some devices, the returned HLS is already browser-playable; this first proof-of-concept deliberately uses FFmpeg to support the MPEG-2/AC3 variant. No Apple TV native client or Philo tuning has been implemented.

## Tablo browser error 4 diagnostics
The live player now waits until FFmpeg has written a valid HLS manifest and at least one segment before handing playback to Chrome. The server retains failed transcoder sessions temporarily and offers an authenticated-LAN-only status route `/api/tablo/live/<session-id>/status` with process running/exited state, manifest readiness, segment count and a short, sanitized FFmpeg error. FFmpeg stderr is temporarily saved under `/tmp/universal-guide-live`, automatically deleted when the live session closes or expires. Do not publish Universal Guide to the Internet; the prototype still has no authentication.

**Next test:** After updating, select a currently-airing Tablo channel and click **Watch Live on Tablo**. If it fails, share the complete message at the bottom of the player or the popup. In particular `transcoder exited`, `manifest missing`, `segments 0` and the FFmpeg error should reveal whether the issue is the tuner stream, decoder or browser.

## Tablo decoder diagnostic (Chrome browser error 4)
The live player now prefers HLS.js / MediaSource on Chrome and Edge, using native HLS only where HLS.js is unavailable (e.g. Safari). If a live stream fails, the player requests `/api/tablo/live/<session-id>/status?probe=true` and reports the live transcode status, HLS playlist validity, segment-reference count and FFprobe-detected video/audio codecs from an existing locally generated HLS segment. It never sends Tablo stream URLs or tokens to the browser. Live playback is still being validated against a physical legacy Tablo.

## iPhone Philo Universal Link handoff (first verified test)
An iPhone with the Philo app installed and signed in successfully opened the **The Office** program page using `https://www.philo.com/player/show/U2hvdzo2MDg1NDg4OTk2NDg0MzkyODg`. Universal Guide now offers **Watch on Philo** for that exact Philo guide series as a conservative test, alongside Plex options when it matches. The link targets the show page, **not a particular episode or live Comedy Central stream**. The public `/go/comedycentral` URL remained in Safari and is not used. Other series must have their provider-specific link verified before a handoff button is enabled.

## iPhone-first Philo catalog prototype

The guide now contains a curated link resolver (`app/philo_links.py`) and an editable catalog (`app/philo_links.json`) for **confirmed Philo player links only**. Each Philo guide program receives separate verified `channel`, `show` and, where episode title/number matches, `episode` handoff options. Links point to the official philo.com site so iOS can open the installed app via Universal Links. This is provider handoff: no Philo DRM streams are proxied, and launching a Philo link doesn't automatically start playback.

Initially verified on the user's iPhone:
- **The Office**: series page
- **Everybody Loves Raymond — Pilot (S1E1)**: exact episode page (user tapped Play there)
- **Food Network**: live channel page (user must select/play inside Philo)

**Important:** Matching is intentionally conservative. Other programs never reuse the Office ID. The guide has external EPG schedules, not a verified Philo subscription lineup. Verified links need to be added to `philo_links.json`; do not derive an ID from a title or assume a random Philo page corresponds to the right show. Add another link only after verifying the destination on an iPhone; a channel page is not the same as live autoplay. The integration is not yet validated on tvOS.

### Add Universal Guide to the iPhone Home Screen

Connect your iPhone to the LAN where Unraid is reachable. Open the Universal Guide WebUI in Safari, tap **Share → Add to Home Screen**, enable **Open as Web App** if offered, and add it. The page now declares a standalone web-app manifest and includes narrower-screen layout adjustments. **Do not expose this prototype publicly**: it has no user authentication and can control local Plex and Tablo playback. Testing whether Philo links launch the iPhone app from standalone Home Screen mode is a next validation step.

### Verify after update

Open a current *The Office* Philo listing: expect **Open Show in Philo** only, unless an independently verified channel match exists. Open a Food Network listing: expect **Open Live Channel in Philo**, which shows its channel page and may require selecting the current program to play. Open *Everybody Loves Raymond* pilot listing: expect an episode-specific button if the schedule contains a matching episode subtitle and no conflicting season/episode metadata. Other titles without verified IDs must not display Philo options.

## Philo public page discovery - expanded starter catalog (2026-11-08)

Philo officially publishes `https://www.philo.com/player/channel/<Philo-ID>` and `https://www.philo.com/player/show/<Philo-ID>` links in public search-indexed pages. Rather than guessing internal IDs or using the authenticated Philo video API, we collected and checked public page titles against the following verified-in-public identifiers:

- **Previously iPhone-tested:** Food Network; The Office series; Everybody Loves Raymond series, and Pilot episode.
- **Publicly listed; still needs iPhone testing:** AMC, HGTV, HISTORY, Discovery Channel, TLC, TV Land; The Kitchen and House Hunters series; The Kitchen S41E1 ("World on a Plate") episode.
- **Sources:** Public `www.philo.com/player/` pages listed in the `source` field of `app/philo_links.json`. A `public-listed` URL has an identifiable title from Philo's public page but **has not been validated on iPhone**.

A Philo guide listing automatically receives available links when its channel name or series title exactly matches the known catalog entry; exact episode buttons additionally require matching subtitle AND season AND episode. Unknown/ambiguous titles never inherit another title's ID. The curated catalog is loaded once per app process for speed; redeploy after editing.

The UI labels untested entries **Test Link** and explains that the user must confirm the result in the installed Philo app. Even tested links generally require pressing Play inside Philo; no unauthorized DRM media access or automatic live tuning is attempted.

**Next iterations:** Catalog growth is still limited by publicly discoverable ID metadata. There is no verified officially supported Philo search API that turns arbitrary EPG titles into Philo IDs. Public SEO page discovery can be expanded, but it must match exact titles, distinguish look-alike shows/movies, and undergo device testing; we do not fabricate IDs or call an unsupported private authenticated streaming API.

## Missing Philo schedules (e.g. HGTV)

The curated Philo channel-link catalog and the third-party EPGTalk program schedule are separate sources. A channel such as HGTV may have a publicly listed Philo deep link even when the external XMLTV import has no mapped or usable program rows in the selected guide window. The guide now retains those cataloged channels as visible **Schedule unavailable** rows with an **Open channel in Philo · Test Link** action (until verified on the user's iPhone). No program titles or airtimes are invented.

The fallback rows appear when **All programs** or **Philo** is selected and there are no matching listings. Like normal channels, they respect the hidden and favorites filters; they are excluded by **Available in Plex**. For troubleshooting, open `/api/philo/coverage` on the Universal Guide server to see the last EPG refresh, available future rows per catalog channel, hidden/favorite status, and the last live Philo error. If HGTV is missing schedules, inspect EPGTalk channel aliases and guide coverage separately rather than treating its deep link as a guide listing. The live channel page may still require tapping a program and Play in Philo.

## HGTV-specific backup EPG source

If the regular EPGTalk Philo import lacks HGTV, Universal Guide now tries a **HGTV-only** supplemental XMLTV feed at `https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz`. It matches US/Eastern HGTV labels explicitly, accepts only real titled future broadcasts (up to four days), and does not override schedules for Food Network, AMC, TLC or other channels. The backup feed is a community-operated source with no guaranteed uptime, future coverage or data licence; confirm the suitability of any third-party guide for your use. A failed download, missing channel, or empty schedule leaves the rest of the EPG intact.

After updating the image, click **Refresh Philo EPG**, then check the JSON from `/api/philo/coverage`. The new `hgtv_fallback` object reports whether the backup had a matching HGTV channel, which source ID was used, how many programs were imported, and any failure. The `HGTV` row's `future_epg_programs` should become greater than zero only if a real usable schedule was received. If zero, share `hgtv_fallback` for the next investigation.

## Broad Philo EPG gap filling (2026-11-08)

The HGTV-only EPG fallback has been generalized. During a Philo EPG refresh, the original EPGTalk XMLTV source remains authoritative for any channel it already covers. Universal Guide computes the channels **missing** from EPGTalk, then makes **one bounded request** to EPGShare's US2 XMLTV for any missing published Philo station with an explicitly mapped station ID. The initial known mapping covers **74 channel names** with exact, published US2 channel identifiers, selecting US/Eastern feeds and deliberately excluding Pacific variants. Source metadata and a per-channel set are available in `/api/philo/coverage` under `supplemental_epg` and `epg_sources`.

This resolves missing listings when supplemental data exists, but it **does not** assert that all mapped channels are included in the user's actual Philo subscription. The local configuration uses a published Philo lineup, not the user's authenticated Philo account, and some channels may have no EPG data from either source. Importantly, TV guide IDs are unrelated to Philo player deep-link IDs. Only the previously curated Philo app links in `app/philo_links.json` will appear as verified/tested handoff actions. We do not guess provider identifiers, expose credentials, or open the wrong channel. Missing program data remains clearly labeled.

On Unraid, update the container and run **Refresh Philo EPG**. Open `/api/philo/coverage` to inspect `supplemental_epg.added`, `supplemental_epg.programs`, `supplemental_epg.error` and `epg_sources.supplemental`. A successful Docker build validates local parser tests; actual live source coverage must be checked by running a refresh on the deployed server.

EPGShare publishes a current human-readable station listing at https://epgshare01.online/epgshare01/epg_ripper_US2.txt; channel IDs can change. This integration uses its public community feed, which has no guaranteed availability or coverage and may require checking data usage terms.

## Philo app handoff expansion (2026-11-08)

The curated `app/philo_links.json` catalog now contains **36 exact channel player URLs** (up from 7). The 29 added links were discovered as public `philo.com/player/channel/<ID>` pages whose titles identify the expected networks, rather than by decoding or guessing Philo IDs. All new entries use `verification: "public-listed"` and are displayed in the iPhone guide with **Test Link** until device-confirmed. Previously tested Food Network stays `iphone-tested`. Each link opens the Philo channel page; a second tap to start playing may be needed. A public listed channel is **not** proof that a user's subscription includes it.

Examples of new mapped channels: A&E, Animal Planet, BET/BET Her, BBC America, CMT, FETV, Game Show Network, the three Hallmark networks, HSN, Investigation Discovery, Lifetime, MeTV and MeTV Toons, MTV2, MTV Live, Nick Jr., Nickelodeon, Oprah Winfrey Network, Paramount Network, Science Channel, Sundance TV, Travel Channel, TV One, UPtv, VH1, and We TV. Source URLs for every new entry are retained in JSON.

The existing `/api/philo/coverage` diagnostics now include `philo_handoff_coverage`: `mapped`, `lineup_total`, and `not_mapped`. The missing list identifies the next batch of public player URLs to research. TV schedules still come from the two independent XMLTV sources; the Philo catalog holds only the independently confirmed *player-page* identifiers. Do not infer player-page IDs from XMLTV station IDs.

After installing the new Docker image, open a show on a newly covered channel such as **A&E** or **Hallmark Channel** and select **Open Live Channel in Philo · Test Link**. Confirm that the installed iPhone app opens the matching channel, even if playback still requires a tap.

## Philo player link coverage: 73 of 76 published Essential channels (Nov 8, 2026)

The curated `app/philo_links.json` catalog now contains **73 distinct Philo channel-player IDs**, each from a public, channel-titled `philo.com/player/channel/` page (not reconstructed from XMLTV IDs or decoded/guessed). The three remaining channels without a proven direct channel-player URL are **Catchy Comedy, Comedy Central, and MTV**. These stay deliberately unmapped; a marketing `/go/` URL is not a proven iPhone Universal Link. The guide's `/api/philo/coverage` endpoint provides this precise `philo_handoff_coverage.not_mapped` list.

The user directly confirmed **A&E**, **Hallmark Channel**, and **Nickelodeon** handoffs in the Philo iPhone app on 2026-11-08; their verification flags are now `iphone-tested`, joining Food Network. The other URLs are marked `public-listed` and displayed as **Test Link** until checked on the iPhone. A link opening a correct channel page does **not** imply automatic playback. The EPG import and Plex/Tablo playback are unchanged.

Further development should verify the newly added links on iPhone, resolve the remaining three IDs from actually titled Philo player pages or known authenticated UI navigation, and keep the app's native Apple TV compatibility testing as a separate project.

## Two or three short episodes in one broadcast slot (SpongeBob)

Some 30-minute TV guide slots contain multiple separately stored Plex cartoon episodes, e.g. `Help Wanted / Reef Blower`. The matcher now recognizes **two or three explicitly separated exact episode titles** (using spaced `/`, `;`, `&`, or `+`) from the **same** Plex series. A multi-part broadcast is considered matched **only if every part has one unique Plex episode**; unclear, partially matched or cross-series episodes stay unmatched. Existing single-episode number/title matching is unchanged.

The program detail modal displays a **Select the Plex segment to play** selector for matched combined broadcasts. Start from Beginning and the Plex Web handoff use the selected segment's specific Plex rating key. **Join in Progress is disabled for combined broadcasts** because the guide's half-hour clock cannot be mapped reliably onto a segment's individual timeline. Check sample data using `/api/match-diagnostics?q=SpongeBob` (returns `matched_parts` for combined records).

On Apple TV, the platform's app switcher (double-press the TV/Control Center remote button) can return from Philo to the Universal Guide app without going to the Home Screen, once a native Universal Guide app exists and the Philo handoff has been tested on tvOS. The current Universal Guide is an iPhone-first browser/PWA, **not a native tvOS app**, and automatic Back-button return from Philo is not implemented or guaranteed. A future native client should restore the last selected guide channel and scroll position when activated.

## Comedy Central and Catchy Comedy: priority player links (2026-11-08)

Direct `/player/channel/` identifiers for **Comedy Central** and **Catchy Comedy** have not been verified from Philo's public indexed channel pages. Do not derive these IDs from numeric sequences, reuse other channel IDs or substitute marketing `/go/` links. Both remain visible in `philo_handoff_coverage.not_mapped`. As a useful interim step, the guide offers **show-level** links for Comedy Central's **South Park** and Catchy Comedy's **Cheers**, **Taxi**, **Full House**, and **Hot in Cleveland**, only when the guide program title exactly matches. These open the relevant show page, **not** the live channel and may require pressing Play. They are marked `public-listed` until tested on iPhone.

To complete live channel linking, obtain a real URL by opening each channel's page in an authenticated Philo session and copying its player channel address (when the Philo UI/browser provides a shareable/copyable URL). Verify that URL on the user's iPhone before marking it `iphone-tested`. Avoid requesting the user's Philo credentials or token.

## Legacy Tablo native-buffer diagnostic (beta investigation)

The live Tablo player has a temporary **Inspect Tablo buffer (diagnostic)** button.
After starting a local live Tablo channel, tap it once, keep the player open
for 2–3 minutes, and tap it again. It calls
`GET /api/tablo/live/{session_id}/source-diagnostics` and shows a summary
of the **original Tablo HLS media playlist**, not Universal Guide's FFmpeg output.
The read-only probe downloads only bounded playlist text, **not video segments**.
It does not expose upstream playlist URLs or tokens.

Inspect `playlist.window_seconds`, `playlist.media_sequence`,
`playlist.segment_count`, and
`previous_observation.media_sequence_advanced`.
An increasing sequence with a small stable window suggests a sliding live
playlist; a growing window suggests accessible history, but neither proves
older segments are still retrievable. If the URL exposes only a short live
window, the next step is to investigate the Tablo's separate seek/timeshift
endpoints or variant playlists before implementing a second buffer.
A missing or inaccessible source playlist produces an error but does not
change live playback or recording behavior.

**Security:** The app still has no authentication. Keep its web port restricted
to the trusted local network; the diagnostic endpoint is not suitable for
internet exposure. Do not share raw upstream Tablo stream URLs or session tokens.

