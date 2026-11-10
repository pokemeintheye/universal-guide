# Universal Guide for Apple TV — native tvOS preview

This is the first native SwiftUI tvOS client for the existing Universal Guide Unraid Docker backend. It is **not** yet the final Apple TV release.

## What works

- Connect to the existing private LAN backend from Settings using `http://YOUR-UNRAID-IP:8787`
- Retrieve `GET /api/guide?hours=8` and display channel rows and program time slots
- Navigate with Apple TV remote focus; select a program to see its details and Plex match
- Filter favorites and manually refresh the guide
- No credentials are required by the current trusted-network backend

## What is not implemented yet

- Playing Plex/Tablo media in the native app
- Verified Philo app handoff on Apple TV
- TV show/movie posters inside the native guide
- TestFlight signing and distribution (requires active Apple Developer membership and registered bundle ID)
- Channel ordering, timeshift, and parity with the web guide

## Build

The repo includes a macOS GitHub Actions build workflow that installs [XcodeGen](https://github.com/yonaskolb/XcodeGen), generates an Xcode project from `project.yml`, and builds an **unsigned tvOS Simulator app**. This validates source compatibility but **cannot be installed on a physical Apple TV**. After Apple enrollment is active, configure an App ID, certificates, signing, App Store Connect, and a release workflow.

To build locally on a Mac:

```sh
cd tvOS
xcodegen generate
xcodebuild -project UniversalGuideTV.xcodeproj -scheme UniversalGuideTV -sdk appletvsimulator -destination 'generic/platform=tvOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

## Signing

`com.universalguide.tv` is a **placeholder** bundle identifier; choose an unused identifier and register it in the Apple Developer portal. Never commit Apple API keys, private signing keys, provisioning profiles, Plex tokens, or credentials to GitHub. Keep the app connected to your trusted LAN only.
