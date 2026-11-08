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
