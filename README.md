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
   - `PLEX_ALLOWED_LABEL`: optional exact Plex sharing label; leave empty to index all Plex media (e.g. `Family Approved`).
   - `PLEX_LABEL_MODE`: `allow` (default) or `exclude`.
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


## Experimental Tablo native-buffer playback (seek proof of concept)

The local Tablo player now offers **Test playback 2 min ago** after the live
channel has accumulated at least ~135 seconds of native video history.
This is explicitly a **test**, not the finished timeshift player.

A new `POST /api/tablo/live/{session_id}/preview?seconds_behind_live=120`
endpoint reads the original Tablo HLS playlist, selects approximately 75
seconds of previously recorded **Tablo-owned** segments, and launches a
separate temporary FFmpeg conversion for browser compatibility. It does
**not** re-tune the Tablo or make a rolling DVR recording on Unraid.
The original live playback session continues separately. On the iPhone,
use **Return to Live** to reattach the original live stream.

The preview is a finite test clip and will stop at its end. It is designed
to establish that **consecutive older segments can be decoded and played**.
It is *not* continuous rewind/live seeking, and you should not expect it to
stay synchronized with the live edge. The short converted test clip is
temporarily written in the container's existing HLS scratch directory,
and is deleted on close/expiry.

To validate: watch a Tablo channel for at least 3 minutes; press **Test
playback 2 min ago**; confirm that video and audio start and play for
30–60 seconds continuously; use **Return to Live**. If it fails, report the
on-screen message and `/api/tablo/live/{preview_id}/status?probe=true`
without exposing any Tablo-origin tokenized URLs.

This is a best-effort experimental branch of the existing player.
Normal **Watch Live on Tablo** and the existing recording/guide integrations
remain unchanged.


## Historical preview troubleshooting: video absent or return to live fails

The initial iPhone test produced audio but no historical video and failed to
return to live. The experimental player now creates a fresh browser video
element when swapping HLS sources, rather than reusing Safari's media decoder.
It also removes the live HLS auto-resume-on-buffer callback, which prevented
reliable manual pause. If the original live session cannot be reattached, a
**Restart Live Tablo** button starts a fresh tuner session.

To investigate video-only failures without revealing tokens, press **Historical
playback diagnostics** while the test clip session is open and share the JSON.
It reports FFmpeg exit status, segment output, and a safe ffprobe summary of the
latest transcoded segment. It does not by itself prove successful image
decoding on iOS. The previous historical test is not considered working until
video, audio, and return-to-live all pass on the actual iPhone.


## Experimental Tablo clip controls after successful live-history test

The user's iPhone confirmed approximately a minute of historical Tablo video
with working audio, followed by successful return to live. The historical test
clip now exposes **Pause/Resume**, **10-second Back**, and **30-second Forward**
buttons; these use the browser video element's seekable range and never seek
outside the currently loaded test clip. **Return to Live** remains separate.

**Important scope:** This is the first UI control milestone, not a finished
continuous DVR player. The current historical test is still a finite ~75-second
clip. These buttons cannot seek beyond that clip and may depend on Safari
reporting a usable video.seekable range. Live pause for longer than the
existing FFmpeg live HLS window is not yet implemented. To support seamless
hour-long timeshift next, we must serve a continuous indexed playback path
from Tablo's native segments and handle relaying/transcoding at arbitrary
positions without creating a separate Unraid recording buffer.

## Native Tablo buffer: selectable historical playback starting points

Following a successful iPhone test of the 75-second historical clip, its
Pause/Resume, 10-second rewind, 30-second forward and Return-to-Live controls,
the player now offers starting positions 2, 5, 10, 15 or 20 minutes behind
live. Each choice reuses the same Tablo live tuner and creates a *new bounded
75-second test clip* by transcoding native historical Tablo segments on demand.
An existing preview is closed before requesting a new one.

This **does not yet provide continuous multi-minute DVR playback** or a
full timeline: it verifies starting/decoding from different positions in
the Tablo buffer, retaining working live playback as fallback. Insufficient
history is reported by the backend (HTTP 409); wait until enough history
has accumulated on the Tablo. Longer DVR playback without duplicate Unraid
recording storage requires a dynamic playlist/transcoding session that can
advance beyond the bounded clip, plus long-pause handling and seeking
across clip boundaries.

## Longer Tablo timeshift test (November 2026)

After successful iPhone testing of 75-second native-buffer playback, pause/resume,
10-second rewind, 30-second forward, and return-to-live, the experimental
historical player now offers **75 seconds** or **up to 5 minutes** of playback.
Choose the start offset (2, 5, 10, 15 or 20 minutes behind live) and the
preview duration. The five-minute option works only when that amount of
native Tablo history is available after the chosen start point; starting
two minutes behind live cannot produce five minutes of historical content.

The browser also pings the original live tuner every 30 seconds while the
player is open. This refreshes the original tuner and linked historical
preview leases so ordinary pausing does not prematurely clean up the session.
iOS background suspension can still interrupt this heartbeat, so long
background pauses are not yet guaranteed.

**This remains a finite proof-of-concept clip, not unlimited DVR playback.**
Video is still temporarily transcoded on Unraid for iPhone compatibility;
Tablo itself remains the historical media store. A dynamic continuously
growing timeshift playlist is needed before the 5-minute limit can be
removed. The regular Watch Live path remains unchanged.

## Continuous Tablo timeshift (opt-in beta)

The player now offers a **Continuous timeshift (beta)** button next to the
existing known-good finite historical previews. Select an offset into
Tablo's available buffer (2–20 minutes) and start the continuous mode.
This mode uses a locally refreshed **text-only** HLS source playlist to
follow Tablo's growing stream. FFmpeg reads the selected Tablo transport
stream segments directly, transcodes at real-time speed, and publishes a
rolling playlist with approximately 90 transcoded HLS segments retained
on Unraid. Old segments are deleted as new ones are produced, instead
of preserving an entire second DVR recording.

Pause/Resume and 10-second back / 30-second forward operate within
the *currently retained output seek window*, which is approximately
six minutes depending on segment boundaries. To jump to earlier points,
select another historical offset and start a new timeshift child.
**Return to Live** closes the timeshift child and reattaches to the
original tuner stream. The established finite 75-second and five-minute
preview options remain available as a fallback.

**Unverified until hardware test:** FFmpeg must reload the growing
file-based source playlist and native Safari must follow the resulting
rolling HLS manifest without stopping. Test at 5 minutes behind live
for more than 7 minutes, then Pause/Resume, seek, and Return to Live.
If it stops or fails, use Historical playback diagnostics to capture
transcoder status, source-follow state, and codec details. Long pauses
and iOS background suspension beyond the retained cache duration
will require additional work; this is not yet a finished unlimited
pause-live-TV implementation.

## Pause Live directly from Tablo live viewing (beta)

The original live player now shows **Pause Live (beta)**. Press it to freeze
the current picture while the Tablo keeps buffering. Press **Resume Live**
to continue:

- For a short pause (less than 12 seconds), continue the existing live HLS.
- For a longer pause, request a continuous timeshift child from approximately
  `pause duration + 12 seconds` behind live, allowing decode overlap.
  The browser then switches to the continuous player and exposes the usual
  Pause/Resume, 10-second backward and 30-second forward controls.
- **Return to Live** reattaches to the original live transcoder. If a timeshift
  request fails, the page attempts to restore live playback.

This is **an approximate resume position**, not yet sample-accurate pause-live
capture. Resuming may start slightly earlier than the scene where Pause was
pressed. The Tablo buffer must still hold the paused scene; when a long pause
exceeds the available history (or the 30-minute selection cap), resume cannot
be guaranteed. Browser background suspension can interrupt keepalives.

Test initially after letting the Tablo channel run for several minutes:
pause live for roughly **90 seconds**, then resume and verify both picture
and audio, 10-second rewind, 30-second forward, and Return to Live. Also test
a **5-second pause** separately. If anything fails, the existing manual
continuous timeshift and finite-clip playback remain available.

## Long Pause Live resume correction

A live-pause test found that a long pause appeared to jump to live despite
the UI labeling the resumed session historical. The FFmpeg HLS demuxer
defaults to `live_start_index=-3`, meaning it begins close to the end of
the input live playlist even when Universal Guide has selected a much older
native Tablo segment. Continuous timeshift now explicitly sets
`-live_start_index 0` before `-i continuous-input.m3u8` so FFmpeg
begins with the selected historical source segment. This change applies
only to continuous timeshift children, not standard live TV or finite
historical clips. The response also includes the selected source media
sequence for internal diagnostics.

Retest long Pause Live: tune and watch a channel for several minutes,
pause for 90 seconds during a recognizable program moment, resume, and
check that the scene is from the paused moment rather than live. The
resume point intentionally includes about 12 seconds of overlap, and
exact frame-accurate positioning is not yet implemented.

## Long Pause Live: capture native segment at pause time

A later iPhone test found that changing FFmpeg to start at index zero made a
long-pause resume appear to begin at the initial tuning point rather than the
scene where Pause was pressed. The long-pause handoff now saves a native Tablo
media sequence immediately on Pause Live via `POST /api/tablo/live/{sid}/pause-point`.
That stores a segment roughly 18 seconds behind the Tablo source live edge.
Resume uses `POST /api/tablo/live/{sid}/timeshift?resume_paused=true`,
which pins the newly created continuous session to the saved segment.
The five-minute manual historical playback and original live stream paths
remain unchanged. The small overlap is for tolerance, not exact
frame-level synchronization with the displayed iPhone frame.

If Tablo has already dropped the saved native segment, the backend returns
409 rather than silently selecting the oldest remaining segment. A missing
pause marker is also reported instead of guessing how many seconds to seek.
For the next iPhone test, start live playback for several minutes, pause on
a recognizable scene for 90 seconds, and check the scene at resume.
The message displays the captured source segment number for diagnosis.

## Accessible iPhone Pause Live diagnostics

The player previously centered a large video in an unscrollable fullscreen
overlay, leaving the diagnostic output and controls inaccessible on smaller
iPhone screens. The overlay now scrolls vertically and the video height is
bounded. A visible **Copy Tablo diagnostics** button in the top player bar
collects the pause marker, current browser seekable position, and continuous
source-session status. Results appear in a selectable text field with a
**Copy diagnostics** action and fallback for Safari when the clipboard API
is unavailable.

The user's latest long-pause test resumed roughly 90 seconds earlier than
the paused program scene. This result does not yet isolate whether the
stored native Tablo marker, FFmpeg's selected input segment, or the iPhone
browser's playback position is responsible. Do not label this problem fixed;
collect the new diagnostics before changing resume offsets again.

## Native Tablo segment identity check for inaccurate Pause Live resume

Observed a 90-second Pause Live test in which two **different source media
sequence numbers**, 1632 and 1647, both returned identical sampled first and
last video PTS (1502.017 to 1502.284 seconds). That is unexpected and leaves
open whether the playlist refers to repeated content, the Tablo server is
serving identical bytes, or PTS sampling is misleading.

The read-only Pause Live diagnostic now adds
`native_entry_urls_distinct` and `native_segment_samples_match`, plus a
20-character SHA-256 sample digest for each bounded 256-KiB native segment
probe. These fields do not reveal the Tablo stream URLs or full media.
Compare both hashes during a fresh short pause (no need to wait 90 seconds)
before altering resume offsets or rewriting FFmpeg. If the URLs differ but
their samples match, investigate Tablo's native playlist/segment addressing
before attempting to calibrate Pause Live against source sequences.

## Fix: native Tablo byte-range segments in historical playback

Confirmed from a live Tablo playlist: 2,658 HLS media entries but only
9 distinct segment URLs; each entry has an `#EXT-X-BYTERANGE` directive.
Historically, Universal Guide's continuous and bounded preview manifests
retained the URLs and durations but discarded the byte-range directives.
That made multiple source sequence numbers resolve to the **same full TS
file** and made Pause Live resume from the wrong scene.

Both `_continuous_source_manifest` and `_historic_clip_manifest` now
preserve per-entry byte ranges. When the native playlist uses an implicit
offset, it is resolved against the preceding range's end and emitted as
an explicit `length@offset`, so a trimmed manifest can begin in the
middle of a shared TS file. HLS version 4 is selected for ranged
playlists; non-ranged playlists keep the old version and behavior.
A malformed or unresolvable range fails explicitly.

After updating the Unraid image, first test Pause Live using the
**Original marker (0s)**, with a recognizable scene and a 90-second pause.
Then check 10s rewind, 30s forward, and Return to Live. Manual
continuous timeshift and bounded historical preview also use the fixed
range handling. Plex playback code was not changed.

## Plex matching: optional article and two-part episode order

Episode matching now accepts a single optional initial **The** on either side
of an episode title, without changing Plex movie matching or series identity.
Plex episode titles ending in **(1)** and **(2)** (optionally **(3)**) are
indexed under their shared base title. If consecutive guide listings on the
same channel have the same series and subtitle, the first two-part listing
automatically links to Plex Part 1 and the next links to Plex Part 2.
The matching remains confined to the same actual series and distinct Plex
episodes. When scheduling is incomplete, nonconsecutive, or ambiguous, both
parts remain selectable rather than arbitrarily choosing one. A Plex catalog
sync is not required to activate matching-rule changes with an existing
indexed catalog; refresh the guide after deploying the updated image.

## Optional Plex label filter for shared viewing

Set `PLEX_ALLOWED_LABEL` to a Plex sharing label (for example,
`Family Approved`) and set `PLEX_LABEL_MODE` to one of the following:

- `allow` (default): Include only movies and shows carrying the label.
- `exclude`: Include movies and shows that **do not** carry the label.

The chosen rule determines which movies and TV episodes are indexed,
searched, marked available and eligible for Universal Guide playback.
Matching is case-insensitive.
Apply labels to the **show** for TV episodes; this version does not
independently allow episodes from an otherwise unlabelled show.

Leaving `PLEX_ALLOWED_LABEL` blank disables filtering, regardless of mode.
When the label **or mode** changes, restarting the container clears the old Plex metadata catalog
before the new filtered sync. The filtered sync may take longer because
Plex sometimes omits Label elements from library listing responses;
in that case we request details for each movie/show.

After updating the image and setting the variable, run **Refresh Plex
catalog**. In allow mode, confirm an approved item appears and an unlabelled item
does not. In exclude mode, confirm a labeled item disappears and an
unlabelled item remains. Plex label edits take effect after another sync.

**Security:** This is not Plex user authorization. Universal Guide
currently has no per-user authentication and its backend uses a Plex
token with access to the configured library. Keep it on a trusted LAN,
do not expose it to untrusted users or the internet, and maintain
Plex shared-user permissions separately.


## Tune OTA channels without guide data

Tablo channels in the indexed /guide/channels lineup are displayed even when
their guide has no schedule entries for the current timeline. Uncovered periods
are represented by a muted, clickable **No guide information** block with
**Watch Live** in the details dialog. This also fills gaps between legitimate
program listings without inventing titles. The live tuner still uses the real
Tablo channel ID and its existing authorization/checks. Channels without
schedule data also appear in Manage Channels, respecting hidden/favorite
preferences. The Plex-only guide view does not include these placeholders.

After updating, click **Refresh Tablo** at least once to pick up any channel
missing from the stored Tablo lineup (e.g. OTA 20.1). A channel missing from
Tablo's own /guide/channels response cannot be tuned through this feature;
check Tablo's channel scan/lineup in that case.

## Tablo DVR transport (beta)

The Tablo player now presents a dedicated DVR bar instead of requiring
manual experiment buttons for everyday viewing. Pause/Resume delegates
to the existing pause-marker logic, 30-second Rewind from live starts
a new continuous-timeshift session from Tablo's native buffer, and
30-second Forward seeks within the rolling historical player cache.
While in DVR mode the timeline scrubs within the **currently seekable
browser window**; it does not claim to seek the entire Tablo-owned
history. A badge displays an **approximate** delay behind source live,
based on the historical source offset and browser position.

**Jump to Live** reconnects to the original continuously running live
tuner without opening another tuner. During a paused live stream it
reloads the current live HLS playlist, intentionally discarding the
paused position. Technical tools, the historical offset picker,
diagnostics, and the old controls remain available under
**Advanced playback tools**. Plex playback remains unchanged.

To test on iPhone: live pause/resume (short and 90-second pause), rewind
30s from live, pause/rewind/forward and scrub in DVR, then Jump to Live.
Observe the timeline growing as the HLS cache buffers. Native HLS and
hls.js may expose different seekable windows, and live-lag is an estimate.
