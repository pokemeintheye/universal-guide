# Apple TV TestFlight signing — Windows + GitHub Actions

This guide assumes Apple Developer membership is active and the tvOS App Store Connect app already exists with **bundle ID** `co.thejonesfamily.universalguide`.

The workflow [tvos-testflight.yml](../.github/workflows/tvos-testflight.yml) is **manual only** (GitHub Actions → Publish Apple TV TestFlight build → Run workflow). Do **not** trigger it until all required credentials are stored as GitHub Actions secrets and the tvOS app is ready for App Store validation.

## Prerequisites

- Windows PC with OpenSSL installed (e.g., Git for Windows or a trusted OpenSSL package); use a PowerShell terminal with `openssl` on PATH.
- Apple Developer account with certificates/profiles access.
- App Store Connect API team-key access enabled (App Store Connect → Users and Access → Integrations → App Store Connect API).
- Your Apple Developer Team ID (Developer Account → Membership details).
- Access to your GitHub repository settings.

## 1. Create Apple Distribution certificate from Windows

Create a **private directory outside the repository** for certificate files. From there, run:

```powershell
openssl req -new -newkey rsa:2048 -nodes -keyout AppleDistribution.key -out AppleDistribution.csr -subj "/CN=Universal Guide Distribution"
```

The `.key` file is **private**; keep it securely and never upload it to GitHub.

At [Apple Certificates](https://developer.apple.com/account/resources/certificates/list), choose **+ → Apple Distribution**, upload the `.csr`, then download the resulting `.cer`. Place the downloaded `.cer` in that private directory. Run:

```powershell
openssl x509 -inform DER -in distribution.cer -out distribution.pem
openssl pkcs12 -export -inkey AppleDistribution.key -in distribution.pem -out AppleDistribution.p12
```

Replace `distribution.cer` with the actual downloaded filename. When prompted, set a strong P12 export password and keep it in a password manager. Do **not** commit the `.key`, `.cer`, or `.p12` files.

## 2. Create tvOS App Store Connect provisioning profile

At [Apple Profiles](https://developer.apple.com/account/resources/profiles/list), choose **+ → Distribution → tvOS App Store Connect** (not Ad Hoc or tvOS Development). Select your App ID `co.thejonesfamily.universalguide` and the **Apple Distribution** certificate you just created. Name it `Universal Guide tvOS App Store`, generate it, and download the `.mobileprovision` file.

## 3. Create an App Store Connect API team key

Go to [App Store Connect](https://appstoreconnect.apple.com/) → **Users and Access → Integrations → App Store Connect API → Team Keys**. If this is the first API key, the Account Holder may need to **Request Access** and wait for approval. Create an API key with sufficient app upload permissions, typically **App Manager**. Record the **Key ID** and **Issuer ID**, and download the `AuthKey_....p8` file. Apple only allows downloading a private key once.

**Do not use an individual API key with this workflow**; the workflow expects a team key and its Issuer ID. No Apple credentials or signing files should be sent in chat.

## 4. Store credentials as GitHub Actions secrets

Go to [GitHub → Settings → Secrets and variables → Actions](https://github.com/pokemeintheye/universal-guide/settings/secrets/actions), and add these seven **repository secrets**:

| GitHub Actions secret | Value |
|---|---|
| `APPLE_TEAM_ID` | Your Apple Developer Team ID |
| `APPLE_DISTRIBUTION_P12_BASE64` | Base64 encoding of the `.p12` certificate |
| `APPLE_DISTRIBUTION_P12_PASSWORD` | The password used to export the P12 |
| `APPLE_TV_PROVISIONING_PROFILE_BASE64` | Base64 encoding of the tvOS `.mobileprovision` file |
| `APP_STORE_CONNECT_KEY_ID` | Key ID from App Store Connect Team Keys |
| `APP_STORE_CONNECT_ISSUER_ID` | Issuer ID from App Store Connect Team Keys |
| `APP_STORE_CONNECT_PRIVATE_KEY_BASE64` | Base64 encoding of the `AuthKey_....p8` file |

To generate a Base64 string on Windows in PowerShell, replace the filename with the path of each file:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\\path\\to\\AppleDistribution.p12")) | Set-Clipboard
```

Paste the clipboard into the corresponding GitHub Actions secret and save. Repeat for the `.mobileprovision` and `.p8` files. Avoid printing these values to a terminal or storing them in the repository. Base64 **is not encryption**; GitHub Secrets provides the protected storage.

## 5. Build and upload

First, make sure the [unsigned simulator build](https://github.com/pokemeintheye/universal-guide/actions/workflows/tvos-build.yml) succeeds. When the tvOS app also meets App Store submission requirements (including app icon and launch assets), go to [Publish Apple TV TestFlight build](https://github.com/pokemeintheye/universal-guide/actions/workflows/tvos-testflight.yml) → **Run workflow**.

The job imports the distribution certificate and profile into a temporary macOS keychain, generates the Xcode project, archives and exports a signed `.ipa`, then uploads it to App Store Connect using the API key. GitHub does not require you to own a Mac.

After Apple processes the build, open App Store Connect → Universal Guide → TestFlight, provide the requested beta-test information, enable internal testing, and install TestFlight on Apple TV.

**Important:** This workflow is prepared for use, **not yet a confirmed successful upload**. The native tvOS app is still an early preview without complete Plex/Tablo/Philo playback. Apple may require further metadata, icons, or compliance answers before distributing the build.
