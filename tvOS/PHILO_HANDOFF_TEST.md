# Apple TV Philo handoff test

This is an **experiment**, not verified tvOS playback support. The app presents Philo options only for channel, series, and exact episode links supplied by the existing Universal Guide backend `/api/guide`. It does not invent Philo IDs or attempt playback through the guide server.

## Testing on Apple TV

1. Ensure the Philo Apple TV app is installed and signed in.
2. In Universal Guide, select a currently airing **Comedy Central** or **Catchy Comedy** program.
3. Choose **Open Channel in Philo**. If the app opens, note whether it shows the correct channel, whether you must press **Watch Live/Play**, and whether you can return to Universal Guide using the app switcher.
4. If Universal Guide remains on-screen and reports a failed handoff, tvOS didn't resolve the official HTTPS channel link to the installed Philo app. This test deliberately uses `UIApplication.open(... universalLinksOnly: true)`; no browser fallback occurs.
5. Optionally test a confirmed exact-episode and series link. Episode and series links are separate choices, and are offered only when the backend has a known destination.

**Do not infer success from compilation.** iPhone-tested Philo URLs are not proof of Apple TV Universal Link association. Philo itself supports TV app deep linking, but the same behavior may not be exposed to third-party tvOS apps for each URL.

The Unraid backend and Plex playback remain unchanged. A new Docker image is not required.
