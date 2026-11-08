# Universal TV Guide

Metadata-only TV guide prototype for Plex, local OTA listings and Philo channel schedules.

**Status:** Initial Plex metadata indexing and optional XMLTV guide import. No automatic Philo/Tablo schedule acquisition, native Apple TV app, or verified app deep links yet. This is an experimental prototype.

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
7. Ensure `/mnt/user/appdata/universal-guide` exists and is writable by container UID **10001**.
8. Launch, then browse `http://YOUR-UNRAID-IP:8787`.

Example setup of data directory (Unraid terminal):
```bash
mkdir -p /mnt/user/appdata/universal-guide
chown 10001:10001 /mnt/user/appdata/universal-guide
```

The app reads Plex API metadata, never mounts media directories, and does not stream videos. It saves Plex titles and episode metadata in SQLite.

## Optional Docker Compose

Copy `.env.example` to `.env` locally and set the Plex URL and token. Run `docker compose up -d` from a checkout. `.env` is gitignored. The compose file uses the published GHCR image, not a local build.

## Optional live guide data

Place an XMLTV-format guide at `/mnt/user/appdata/universal-guide/guide.xml`. Click **Reload guide.xml** or allow the background watcher to import it. Schedule collection from Philo and Tablo is **not implemented**; XMLTV feeds must be provided independently.

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
