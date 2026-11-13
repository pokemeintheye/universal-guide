import SwiftUI
import AVKit

// tvOS AVPlayer uses only the server's HLS playlist. For Tablo this is
// /api/tablo/live/<sid>/index.m3u8 -- the continuously recorded FFmpeg
// on-disk DVR, NEVER a direct Tablo tuner URL.
struct TVTabloSession: Identifiable {
    let id: String
    let title: String
    let serverURL: URL
    let playlistURL: URL
}

private struct TVTabloResponse: Decodable {
    let session_id: String
    let playlist_url: String
}

private struct TVTabloStatus: Decodable {
    let manifest_ready: Bool?
    let disk_dvr_playlist: TVTabloDiskStatus?
    let transcoder_running: Bool?
}
private struct TVTabloDiskStatus: Decodable {
    let segment_count: Int?
}

enum TVTabloService {
    static func start(serverURL: String, channel: GuideChannel, title: String) async throws -> TVMediaSession {
        let base = try PlexPlaybackService.baseURL(serverURL)
        guard channel.id.hasPrefix("tablo:") else {
            throw PlexPlaybackError.server("This channel is not a Tablo source.")
        }
        let rawID = String(channel.id.dropFirst("tablo:".count))
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        parts.path = "/api/tablo/live/start"
        parts.queryItems = [URLQueryItem(name: "channel_id", value: rawID)]
        guard let url = parts.url else { throw PlexPlaybackError.invalidServer }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 35
        let (data,response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw PlexPlaybackError.server("Unable to start the Tablo DVR stream.")
        }
        let result = try JSONDecoder().decode(TVTabloResponse.self, from: data)
        guard result.playlist_url.hasPrefix("/api/tablo/live/"),
              result.playlist_url.hasSuffix("/index.m3u8"),
              let playlist = URL(string: result.playlist_url, relativeTo: base)?.absoluteURL else {
            throw PlexPlaybackError.invalidSession
        }
        let session = TVTabloSession(id: result.session_id, title: title,
                                    serverURL: base, playlistURL: playlist)
        do {
            for _ in 0..<45 {
                var health = URLRequest(url: URL(string: "/api/tablo/live/\(session.id)/status", relativeTo: base)!.absoluteURL)
                health.timeoutInterval = 7
                let (bytes,reply) = try await URLSession.shared.data(for: health)
                if let code = reply as? HTTPURLResponse, code.statusCode == 200 {
                    let status = try JSONDecoder().decode(TVTabloStatus.self, from: bytes)
                    if status.transcoder_running == false {
                        throw PlexPlaybackError.server("Tablo DVR encoder stopped unexpectedly.")
                    }
                    if status.manifest_ready == true &&
                       (status.disk_dvr_playlist?.segment_count ?? 0) >= 4 {
                        return .tablo(session)
                    }
                }
                try await Task.sleep(for: .seconds(1))
            }
            throw PlexPlaybackError.server("Tablo did not create enough disk-backed DVR segments.")
        } catch {
            await stop(session)
            throw error
        }
    }
    static func stop(_ session: TVTabloSession) async {
        var req = URLRequest(url: URL(string: "/api/tablo/live/\(session.id)", relativeTo: session.serverURL)!.absoluteURL)
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        _ = try? await URLSession.shared.data(for: req)
    }
}

enum TVMediaSession: Identifiable {
    case plex(PlexPlaybackSession)
    case tablo(TVTabloSession)
    var id: String {
        switch self { case .plex(let x): return x.id; case .tablo(let x): return x.id }
    }
    var title: String {
        switch self { case .plex(let x): return x.title; case .tablo(let x): return x.title }
    }
    var playlist: URL {
        switch self { case .plex(let x): return x.playlistURL; case .tablo(let x): return x.playlistURL }
    }
    var isTablo: Bool { if case .tablo = self { return true }; return false }
    func stop() async {
        switch self {
        case .plex(let p): await PlexPlaybackService.stop(p)
        case .tablo(let t): await TVTabloService.stop(t)
        }
    }
}


struct TVPlaybackMetadata {
    let channelName: String?
    let title: String
    let subtitle: String?
    let season: Int?
    let episode: Int?
    let year: Int?
    let isMovie: Bool

    var detailLine: String {
        var parts: [String] = []
        if isMovie {
            if let year, year > 0 { parts.append(String(year)) }
        } else {
            if let season, season > 0 { parts.append("S\(season)") }
            if let episode, episode > 0 { parts.append("E\(episode)") }
        }
        if let subtitle, !subtitle.isEmpty, subtitle != title { parts.append(subtitle) }
        return parts.joined(separator: " · ")
    }
}

struct TVMediaPlayerScreen: View {
    let session: TVMediaSession
    let metadata: TVPlaybackMetadata
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()
    @State private var controlsVisible = false
    @State private var selectedControl = "pause"
    @State private var elapsed: Double = 0
    @State private var minimum: Double = 0
    @State private var maximum: Double = 1
    @State private var seekProgress: Double = 0
    // Plex preview cursor is independent of the actual AVPlayer seek position.
    @State private var scrubPosition: Double?
    @State private var scrubDirection = 0
    @State private var scrubSpeed = 0
    @State private var scrubNeedsConfirm = false
    @State private var scrubLastTick: Date?
    @State private var scrubThumbnail: UIImage?
    @State private var scrubThumbnailSeconds: Int?
    @State private var scrubThumbnailTask: Task<Void, Never>?
    @State private var scrubThumbnailCache: [Int: UIImage] = [:]
    @State private var scrubThumbnailLoading = false
    @State private var scrubLastThumbnailAttempt = Date.distantPast
    @State private var scrubThumbnailRequestID = 0
    @State private var isPaused = false
    @State private var videoReady = false
    @State private var streamStartedAt = Date()
    @State private var recoveredOnce = false
    @State private var checkedPlayback = false
    @State private var streamError: String?
    @State private var explicitlyClosing = false
    @State private var replacementPlex: PlexPlaybackSession?
    @State private var retiringPlex: PlexPlaybackSession?
    @State private var plexMetadata: PlexPlaybackMetadata?
    // First playable AVPlayer timestamp for the current Plex transcode.
    // Its timestamp may be zero OR an absolute HLS media timestamp.
    @State private var plexPlaybackOrigin: Double?
    @State private var pendingTrackSeek: Double?
    @State private var applyingTrackSeek = false
    @State private var playbackTimeObserver: Any?
    @State private var pauseBeganAt: Date?
    @State private var pausePosition: Double?
    @State private var restoringLongPause = false
    @State private var audioID: Int?
    @State private var subtitleID: Int?
    @State private var trackMenu: String?
    @State private var menuIndex = 0
    @State private var changingTrack = false
    @State private var optionsError: String?
    @State private var hideControlsTask: Task<Void, Never>?
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)
    private var isCompact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private func isSelected(_ key: String) -> Bool {
        controlsVisible && selectedControl == key
    }
    private var playbackTitle: String {
        metadata.title.isEmpty ? session.title : metadata.title
    }
    private var playerDetailLine: String {
        if session.isTablo || !metadata.isMovie { return metadata.detailLine }
        let year = plexMetadata?.year ?? metadata.year ?? 0
        let existing = metadata.detailLine
        guard year > 0 else { return existing }
        let yearText = String(year)
        if existing.split(separator: "·").map({ $0.trimmingCharacters(in: .whitespaces) }).contains(yearText) {
            return existing
        }
        return ([yearText, existing].filter { !$0.isEmpty }).joined(separator: " · ")
    }
    private var sourceName: String {
        session.isTablo ? (metadata.channelName ?? "Live TV") : "PLEX"
    }
    private var currentSession: TVMediaSession {
        if let replacementPlex { return .plex(replacementPlex) }
        return session
    }
    private var plexSession: PlexPlaybackSession? {
        if case .plex(let plex) = currentSession { return plex }
        return nil
    }
    private var plexScrubDuration: Double {
        max(1, plexMetadata?.duration_seconds ?? 1)
    }
    private var playbackElapsedSeconds: Double {
        if session.isTablo { return max(0, elapsed - minimum) }
        if let plex = plexSession { return currentPlexPosition(plex) }
        return max(0, elapsed)
    }
    private var playbackRemainingSeconds: Double {
        if session.isTablo { return max(0, maximum - elapsed) }
        return max(0, plexScrubDuration - playbackElapsedSeconds)
    }
    private var shownSeekProgress: Double {
        if !session.isTablo, let scrubPosition {
            return min(1, max(0, scrubPosition / plexScrubDuration))
        }
        if !session.isTablo, let plex = plexSession, plexMetadata != nil {
            return min(1, max(0, currentPlexPosition(plex) / plexScrubDuration))
        }
        return seekProgress
    }
    private var remoteButtons: [String] {
        session.isTablo ? ["back", "pause", "forward", "live"] :
                          ["back", "pause", "forward", "chapters", "captions", "audio"]
    }
    private var menuCount: Int {
        switch trackMenu {
        case "chapters": return plexMetadata?.chapters.count ?? 0
        case "audio": return plexMetadata?.audio.count ?? 0
        case "captions": return (plexMetadata?.subtitles.count ?? 0) + 1
        default: return 0
        }
    }
    private var menuTitle: String {
        switch trackMenu {
        case "chapters": return "Chapters"
        case "audio": return "Audio"
        default: return "Captions"
        }
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            UniversalVideoSurface(player: player) { ready in
                videoReady = ready
                if ready, let previous = retiringPlex {
                    // The new stream has displayed a video frame: only now
                    // can we safely retire the previously working stream.
                    retiringPlex = nil
                    Task { await PlexPlaybackService.stop(previous) }
                }
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)
            #if os(iOS)
            // Tap the native video surface to reveal the same playback controls
            // used on Apple TV. Keep the video layer noninteractive and let the
            // controls above it handle their own taps once visible.
            .overlay {
                if !controlsVisible && trackMenu == nil {
                    Color.clear
                        .contentShape(Rectangle())
                        .onTapGesture { reveal() }
                }
            }
            #endif

            if controlsVisible {
                VStack(spacing: 0) {
                    HStack(alignment: .top, spacing: 16) {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(sourceName.uppercased())
                                .font(.system(size: isCompact ? 13 : 20, weight: .bold))
                                .foregroundStyle(purple)
                            Text(playbackTitle)
                                .font(.system(size: isCompact ? 21 : 32, weight: .bold))
                                .lineLimit(2)
                            if !playerDetailLine.isEmpty {
                                Text(playerDetailLine)
                                    .font(.system(size: isCompact ? 13 : 19))
                                    .foregroundStyle(.white.opacity(0.75))
                                    .lineLimit(2)
                            }
                        }
                        Spacer()
                        Button { exitPlayback() } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: isCompact ? 20 : 28))
                                .foregroundStyle(.white)
                                .padding(8)
                                .overlay(RoundedRectangle(cornerRadius: 8)
                                    .stroke(isSelected("close") ? purple : .clear,
                                            lineWidth: 2))
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, isCompact ? 20 : 74)
                    .padding(.top, isCompact ? 14 : 42)
                    Spacer(minLength: 30)

                    // The seek bar is ABOVE the three transport controls.
                    VStack(spacing: isCompact ? 10 : 17) {
                        HStack {
                            Text(time(playbackElapsedSeconds))
                            Spacer()
                            Text(session.isTablo && maximum-elapsed < 8 ? "LIVE" :
                                 "-" + time(playbackRemainingSeconds))
                        }
                        .font(.system(size: isCompact ? 13 : 18, weight: .medium))
                        .foregroundStyle(.white)

                        Button {
                            if session.isTablo { seekTo(elapsed + 30) }
                            else { handlePlexScrubOK() }
                        } label: {
                            GeometryReader { geometry in
                                let usable = max(0, geometry.size.width-24)
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Color.white.opacity(0.36))
                                        .frame(height: 6)
                                    Capsule().fill(purple.opacity(0.6))
                                        .frame(width: geometry.size.width*shownSeekProgress,
                                               height: 6)
                                    Circle().fill(.white)
                                        .frame(width: 24, height: 24)
                                        .overlay(Circle().stroke(
                                            isSelected("seek") ? purple : .clear,
                                            lineWidth: 4))
                                        .offset(x: usable*shownSeekProgress)
                                }
                                .frame(height: geometry.size.height)
                            }
                            .frame(height: 32)
                        }
                        .buttonStyle(.plain)
                        // Render the thumbnail ABOVE the slider's 32-point hit target.
                        // Putting the preview inside that tiny button caused tvOS to
                        // clip everything except the bottom edge of the card.
                        .overlay(alignment: .topLeading) {
                            if !session.isTablo, let preview = scrubPosition {
                                GeometryReader { geometry in
                                    VStack(spacing: 5) {
                                        if let thumbnail = scrubThumbnail {
                                            Image(uiImage: thumbnail)
                                                .resizable()
                                                .aspectRatio(contentMode: .fill)
                                                .frame(width: isCompact ? 145 : 240,
                                                       height: isCompact ? 82 : 135)
                                                .clipped()
                                        } else {
                                            RoundedRectangle(cornerRadius: 7)
                                                .fill(Color.black.opacity(0.75))
                                                .frame(width: isCompact ? 145 : 240,
                                                       height: isCompact ? 82 : 135)
                                                .overlay(Text("Preview").font(.caption))
                                        }
                                        Text(time(preview) + (scrubSpeed > 0 ?
                                            "   \(scrubDirection < 0 ? "◀" : "▶") \(scrubSpeed)×" : ""))
                                            .font(.system(size: isCompact ? 12 : 18, weight: .bold))
                                            .foregroundStyle(.white)
                                    }
                                    .padding(6)
                                    .background(Color.black.opacity(0.91),
                                                in: RoundedRectangle(cornerRadius: 10))
                                    .position(x: min(max(isCompact ? 85 : 135,
                                                         geometry.size.width * shownSeekProgress),
                                                     geometry.size.width - (isCompact ? 85 : 135)),
                                              y: -(isCompact ? 48 : 80))
                                }
                                .allowsHitTesting(false)
                            }
                        }
                        .zIndex(20)

                        ZStack(alignment: .trailing) {
                            // Center the three transport buttons on screen,
                            // regardless of whether Go Live is present.
                            HStack(spacing: isCompact ? 18 : 58) {
                                transport("rewind20", key: "back", size: isCompact ? 48 : 66) {
                                    skip(-20)
                                }
                                transport(isPaused ? "play.fill" : "pause.fill",
                                          key: "pause", size: isCompact ? 60 : 78) {
                                    playPause()
                                }
                                transport("goforward.30", key: "forward",
                                          size: isCompact ? 48 : 66) {
                                    skip(30)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            if session.isTablo {
                                Button {
                                    goLive()
                                } label: {
                                    Label("Go Live", systemImage: "bolt.fill")
                                        .font(.system(size: isCompact ? 11 : 18, weight: .semibold))
                                        .foregroundStyle(.white)
                                        .padding(isCompact ? 7 : 10)
                                        .overlay(RoundedRectangle(cornerRadius: 9)
                                            .stroke(isSelected("live") ? purple : .clear,
                                                    lineWidth: 2))
                                }
                                .buttonStyle(.plain)
                            } else {
                                HStack(spacing: isCompact ? 6 : 12) {
                                    plexOptionsButton("chapters", icon: "book.closed",
                                                      enabled: !(plexMetadata?.chapters.isEmpty ?? true))
                                    plexOptionsButton("captions", icon: "captions.bubble",
                                                      enabled: plexMetadata != nil)
                                    plexOptionsButton("audio", icon: "globe",
                                                      enabled: !(plexMetadata?.audio.isEmpty ?? true))
                                }
                            }
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .padding(.horizontal, isCompact ? 22 : 80)
                    .padding(.bottom, isCompact ? 16 : 36)
                }
                .background(LinearGradient(
                    colors: [.black.opacity(0.62), .clear, .black.opacity(0.82)],
                    startPoint: .top, endPoint: .bottom))
                .transition(.opacity)
            }
            if trackMenu != nil {
                plexOptionsOverlay
            }
            if changingTrack {
                Color.black.opacity(0.55).ignoresSafeArea()
                ProgressView("Updating Plex playback…")
                    .font(.title3)
            }
            #if os(tvOS)
            UniversalMediaRemoteReceiver(
                onMove: remoteDirection,
                onSelect: { if controlsVisible || trackMenu != nil { activateCurrent() } else { reveal() } },
                onBack: remoteExit,
                onPlayPause: playPause
            )
            .ignoresSafeArea()
            #endif
        }
        .tint(purple)
        .preferredColorScheme(.dark)
        .onAppear {
            streamStartedAt = Date()
            if let plex = plexSession {
                Task {
                    if let metadata = try? await PlexPlaybackService.metadata(plex) {
                        plexMetadata = metadata
                        audioID = metadata.audio.first(where: { $0.selected })?.id
                        subtitleID = metadata.subtitles.first(where: { $0.selected })?.id ?? 0
                    }
                }
            }
            plexPlaybackOrigin = plexSession?.offsetSeconds == 0 ? 0 : nil
            pendingTrackSeek = plexSession?.initialSeekSeconds
            applyingTrackSeek = false
            let item = AVPlayerItem(url: currentSession.playlist)
            player.replaceCurrentItem(with: item)
            // The initial seek must finish before playback starts, or
            // AVPlayer may display the tail of the stream first.
            if pendingTrackSeek == nil { player.play() }
            isPaused = false
            playbackTimeObserver = player.addPeriodicTimeObserver(
                forInterval: CMTime(seconds: 0.25, preferredTimescale: 600),
                queue: .main
            ) { _ in
                updateProgress()
                monitorPlexStartup()
            }
        }
        .onDisappear {
            scrubThumbnailTask?.cancel()
            hideControlsTask?.cancel()
            if let observer = playbackTimeObserver {
                player.removeTimeObserver(observer)
                playbackTimeObserver = nil
            }
            player.pause()
            player.replaceCurrentItem(with: nil)
            // SwiftUI can tear down a playback view during a presentation
            // transition. Don't DELETE a Plex session until an explicit exit.
            // Preserve existing Tablo cleanup.
            if session.isTablo { Task { await session.stop() } }
        }
        .onReceive(Timer.publish(every: 0.1, on: .main, in: .common).autoconnect()) { _ in
            tickPlexScrub()
            // A new AVPlayerItem may become ready while playback is paused
            // for a track switch, so periodic playback callbacks won't fire.
            if pendingTrackSeek != nil { updateProgress() }
        }
        // The AVPlayer periodic-time observer drives the live clock.
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            if let direction = event.object as? String { remoteDirection(direction) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            if controlsVisible || trackMenu != nil { activateCurrent() } else { reveal() }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadBack)) { _ in
            #if os(iOS)
            remoteExit()
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadPlayPause)) { _ in
            #if os(iOS)
            playPause()
            #endif
        }
        .alert("Plex options", isPresented: Binding(
            get: { optionsError != nil }, set: { if !$0 { optionsError = nil } }
        )) {
            Button("OK") { optionsError = nil }
        } message: {
            Text(optionsError ?? "")
        }
        .alert("Plex playback couldn't start",
               isPresented: Binding(get: { streamError != nil },
                                    set: { if !$0 { streamError = nil } })) {
            Button("Retry") { restartVideo() }
            Button("Return to Guide") { exitPlayback() }
        } message: {
            Text(streamError ?? "The player did not receive a video frame.")
        }
    }

    private func plexOptionsButton(_ key: String, icon: String, enabled: Bool) -> some View {
        Button {
            openPlexOptions(key)
        } label: {
            Image(systemName: icon)
                .font(.system(size: isCompact ? 18 : 27, weight: .medium))
                .foregroundStyle(enabled ? .white : .gray)
                .frame(width: isCompact ? 34 : 46, height: isCompact ? 40 : 52)
                .overlay(RoundedRectangle(cornerRadius: 9)
                    .stroke(isSelected(key) ? purple : .clear, lineWidth: 2))
        }
        .buttonStyle(.plain)
        .disabled(!enabled || changingTrack)
        .accessibilityLabel(key == "captions" ? "Captions" : key.capitalized)
    }

    private var plexOptionsOverlay: some View {
        ZStack {
            Color.black.opacity(0.8).ignoresSafeArea()
            VStack(alignment: .leading, spacing: isCompact ? 8 : 16) {
                HStack {
                    Text(menuTitle).font(.system(size: isCompact ? 24 : 35, weight: .bold))
                    Spacer()
                    Button { trackMenu = nil } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: isCompact ? 24 : 32))
                    }
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: isCompact ? 5 : 10) {
                            ForEach(0..<menuCount, id: \.self) { index in
                                Button { selectPlexOption(index) } label: {
                                    HStack(spacing: isCompact ? 12 : 22) {
                                        if trackMenu == "chapters",
                                           plexMetadata?.preview_available == true,
                                           let plex = plexSession,
                                           let chapters = plexMetadata?.chapters,
                                           chapters.indices.contains(index),
                                           let url = URL(
                                              string: "/api/player/\(plex.id)/preview?at=\(Int(chapters[index].start_seconds))",
                                              relativeTo: plex.serverURL
                                           )?.absoluteURL {
                                            AsyncImage(url: url) { image in
                                                image.resizable()
                                                    .aspectRatio(contentMode: .fill)
                                            } placeholder: {
                                                Color.white.opacity(0.10)
                                            }
                                            .frame(width: isCompact ? 96 : 160,
                                                   height: isCompact ? 54 : 90)
                                            .clipShape(RoundedRectangle(cornerRadius: 7))
                                        }
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(optionLabel(index))
                                                .font(.system(size: isCompact ? 15 : 23, weight: .semibold))
                                            if let detail = optionDetail(index) {
                                                Text(detail)
                                                    .font(.system(size: isCompact ? 11 : 16))
                                                    .foregroundStyle(.white.opacity(0.7))
                                            }
                                        }
                                        Spacer()
                                        if optionIsSelected(index) {
                                            Image(systemName: "checkmark").foregroundStyle(purple)
                                        }
                                    }
                                    .foregroundStyle(.white)
                                    .padding(isCompact ? 9 : 16)
                                    .background(menuIndex == index ? purple.opacity(0.28) :
                                                Color.white.opacity(0.08),
                                                in: RoundedRectangle(cornerRadius: 10))
                                    .overlay(RoundedRectangle(cornerRadius: 10)
                                        .stroke(menuIndex == index ? purple : .clear, lineWidth: 2))
                                }
                                .buttonStyle(.plain)
                                .id(index)
                            }
                        }
                    }
                    .onChange(of: menuIndex) { _, next in
                        withAnimation { proxy.scrollTo(next, anchor: .center) }
                    }
                }
            }
            .padding(isCompact ? 18 : 34)
            .frame(maxWidth: isCompact ? 560 : 900, maxHeight: isCompact ? 300 : 700)
            .background(Color(red: 0.09, green: 0.08, blue: 0.16),
                        in: RoundedRectangle(cornerRadius: 20))
        }
    }

    private func optionLabel(_ index: Int) -> String {
        switch trackMenu {
        case "chapters":
            guard let items = plexMetadata?.chapters, items.indices.contains(index) else { return "" }
            return items[index].title
        case "audio":
            guard let items = plexMetadata?.audio, items.indices.contains(index) else { return "" }
            return items[index].label
        default:
            if index == 0 { return "Off" }
            guard let items = plexMetadata?.subtitles, items.indices.contains(index-1) else { return "" }
            return items[index-1].label
        }
    }
    private func optionDetail(_ index: Int) -> String? {
        if trackMenu == "chapters", let chapters = plexMetadata?.chapters,
           chapters.indices.contains(index) {
            return time(chapters[index].start_seconds)
        }
        return nil
    }
    private func optionIsSelected(_ index: Int) -> Bool {
        if trackMenu == "audio", let audio = plexMetadata?.audio, audio.indices.contains(index) {
            return audio[index].id == audioID
        }
        if trackMenu == "captions" {
            if index == 0 { return subtitleID == nil || subtitleID == 0 }
            if let subtitles = plexMetadata?.subtitles, subtitles.indices.contains(index-1) {
                return subtitles[index-1].id == subtitleID
            }
        }
        return false
    }

    private func openPlexOptions(_ kind: String) {
        guard plexMetadata != nil, !changingTrack else { return }
        trackMenu = kind
        menuIndex = 0
        if kind == "audio", let audio = plexMetadata?.audio,
           let index = audio.firstIndex(where: { $0.id == audioID }) { menuIndex = index }
        if kind == "captions", let sub = plexMetadata?.subtitles,
           let index = sub.firstIndex(where: { $0.id == subtitleID }) { menuIndex = index+1 }
        hideControlsTask?.cancel()
    }

    // Plex's transcode offset and AVPlayer's HLS media timestamp use
    // different coordinate systems. Capture the AVPlayer time when the
    // new session FIRST becomes playable, then add only the subsequent
    // change in AVPlayer time to the requested absolute movie offset.
    // This works whether an HLS manifest begins at 0 or at 3600 seconds.
    private func currentPlexPosition(_ plex: PlexPlaybackSession) -> Double {
        // Read the observable player clock so the timeline and the resume
        // position reflect the same actual playback tick.
        let current = elapsed
        // Preserve negative deltas when the user seeks backward within the
        // existing stream; clamp the final absolute movie timestamp instead.
        let relative = current.isFinite ?
            (plexPlaybackOrigin.map { current - $0 } ?? 0) : 0
        let length = plexMetadata?.duration_seconds ?? Double.greatestFiniteMagnitude
        return min(max(0, length - 1), plex.offsetSeconds + relative)
    }

    private func selectPlexOption(_ index: Int) {
        guard !changingTrack, let plex = plexSession else { return }
        if trackMenu == "chapters" {
            guard let chapters = plexMetadata?.chapters, chapters.indices.contains(index) else { return }
            trackMenu = nil
            let target = chapters[index].start_seconds
            let origin = player.currentItem?.seekableTimeRanges.first.map {
                CMTimeGetSeconds($0.timeRangeValue.start)
            } ?? 0
            let local = target - plex.offsetSeconds + (origin.isFinite ? origin : 0)
            if target >= plex.offsetSeconds && local >= minimum && local <= maximum {
                seekTo(local)
            } else {
                Task { await replacePlexStream(at: target, audio: audioID,
                                               captions: subtitleID, change: nil) }
            }
            return
        }
        if trackMenu == "audio" {
            guard let tracks = plexMetadata?.audio, tracks.indices.contains(index) else { return }
            let chosen = tracks[index].id
            trackMenu = nil
            if chosen != audioID {
                Task { await replacePlexStream(at: currentPlexPosition(plex),
                                               audio: chosen, captions: subtitleID,
                                               change: "audio") }
            }
        } else if trackMenu == "captions" {
            let chosen: Int
            if index == 0 { chosen = 0 }
            else {
                guard let tracks = plexMetadata?.subtitles,
                      tracks.indices.contains(index-1) else { return }
                chosen = tracks[index-1].id
            }
            trackMenu = nil
            if chosen != (subtitleID ?? 0) {
                Task { await replacePlexStream(at: currentPlexPosition(plex),
                                               audio: audioID, captions: chosen,
                                               change: "captions") }
            }
        }
    }

    private func replacePlexStream(at position: Double, audio: Int?,
                                   captions: Int?, change: String?) async {
        guard let plex = plexSession, !changingTrack else { return }
        changingTrack = true
        // Creating and preflighting a new transcode can take several seconds.
        // Hold the current frame so it cannot advance past the exact
        // position used for the replacement stream.
        let wasPlaying = player.rate > 0
        player.pause()
        var replaced = false
        defer {
            changingTrack = false
            scheduleHideControls()
            if !replaced && wasPlaying { player.play() }
        }
        do {
            if change == "audio" {
                try await PlexPlaybackService.selectTracks(plex, audioID: audio)
            } else if change == "captions" {
                try await PlexPlaybackService.selectTracks(plex, subtitleID: captions)
            }
            // Avoid resume-offset HLS timeline ambiguity for track switches:
            // load the full-movie stream then seek locally to the saved time.
            let trackChange = change == "audio" || change == "captions" ||
                              change == "long-pause"
            let next = try await PlexPlaybackService.start(
                serverURL: plex.serverURL.absoluteString, ratingKey: plex.ratingKey,
                mode: trackChange ? "beginning" : "resume",
                airingStart: "", airingEnd: "",
                offsetSeconds: trackChange ? nil : max(0, position),
                audioStreamID: audio, subtitleStreamID: captions)
            // The new HLS playlist has passed preflight. Only now replace
            // the current AVPlayer item and retire the previous session.
            // Keep the previous transcode alive until the first new video
            // frame is actually displayed, not merely until a manifest loads.
            retiringPlex = plex
            videoReady = false
            plexPlaybackOrigin = trackChange ? 0 : nil
            pendingTrackSeek = trackChange ? max(0, position) : nil
            applyingTrackSeek = false
            replacementPlex = next
            replaced = true
            player.replaceCurrentItem(with: AVPlayerItem(url: next.playlistURL))
            if !trackChange { player.play() }
            audioID = audio
            subtitleID = captions
            // Show the preserved movie timestamp while the new stream loads.
            // The periodic observer will update it after the local seek.
            elapsed = trackChange ? max(0, position) : 0
            minimum = 0
            maximum = max(1, (plexMetadata?.duration_seconds ?? 1)-next.offsetSeconds)
            checkedPlayback = false
            recoveredOnce = false
            streamStartedAt = Date()
        } catch {
            optionsError = error.localizedDescription
        }
    }

    private func transport(_ symbol: String, key: String, size: CGFloat,
                           action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Group {
                if symbol == "rewind20" {
                    ZStack {
                        Image(systemName: "arrow.counterclockwise")
                            .font(.system(size: size*0.50, weight: .medium))
                        Text("20")
                            .font(.system(size: size*0.20, weight: .bold))
                    }
                } else {
                    Image(systemName: symbol)
                        .font(.system(size: size*0.52))
                }
            }
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .overlay(Circle().stroke(isSelected(key) ? purple : .clear,
                                     lineWidth: 3))
        }
        .buttonStyle(.plain)
    }

    private func remoteDirection(_ direction: String) {
        if changingTrack { return }
        if trackMenu != nil {
            switch direction {
            case "up", "left": menuIndex = max(0, menuIndex-1)
            case "down", "right": menuIndex = min(max(0,menuCount-1), menuIndex+1)
            default: break
            }
            return
        }
        if !controlsVisible {
            switch direction {
            case "left": skip(-20)
            case "right": skip(30)
            default: reveal()
            }
            return
        }
        scheduleHideControls()
        if selectedControl == "seek" {
            if !session.isTablo {
                switch direction {
                case "left": changePlexScrubDirection(-1); return
                case "right": changePlexScrubDirection(1); return
                case "down":
                    cancelPlexScrub()
                    selectedControl = "pause"
                    return
                case "up":
                    cancelPlexScrub()
                    selectedControl = "close"
                    return
                default: return
                }
            }
            switch direction {
            case "left": skip(-20); return
            case "right": skip(30); return
            case "down": selectedControl = "pause"; return
            case "up": selectedControl = "close"; return
            default: return
            }
        }
        if direction == "up" {
            selectedControl = "seek"
            return
        }
        if direction == "down" {
            if selectedControl == "close" { selectedControl = "seek" }
            else if ["live", "chapters", "captions", "audio"].contains(selectedControl) {
                selectedControl = "pause"
            }
            return
        }
        if direction == "left" || direction == "right" {
            let row = remoteButtons
            guard let current = row.firstIndex(of: selectedControl) else {
                selectedControl = "pause"
                return
            }
            let delta = direction == "left" ? -1 : 1
            selectedControl = row[max(0, min(row.count-1,current+delta))]
        }
    }

    private func activateCurrent() {
        if trackMenu != nil { selectPlexOption(menuIndex); return }
        if changingTrack { return }
        scheduleHideControls()
        switch selectedControl {
        case "back": skip(-20)
        case "pause": playPause()
        case "forward": skip(30)
        case "seek":
            if session.isTablo { seekTo(elapsed) } else { handlePlexScrubOK() }
        case "live": goLive()
        case "chapters", "captions", "audio": openPlexOptions(selectedControl)
        case "close": exitPlayback()
        default: reveal()
        }
    }
    private func remoteExit() {
        if trackMenu != nil { trackMenu = nil; scheduleHideControls(); return }
        if changingTrack { return }
        if controlsVisible {
            cancelPlexScrub()
            hideControls()
        } else { exitPlayback() }
    }
    private func changePlexScrubDirection(_ requested: Int) {
        guard !session.isTablo, let plex = plexSession, !changingTrack else { return }
        if scrubPosition == nil {
            scrubPosition = currentPlexPosition(plex)
            scrubNeedsConfirm = false
            requestPlexThumbnail()
        }
        if scrubDirection == requested {
            scrubSpeed = min(4, scrubSpeed + 1)
        } else if scrubSpeed > 0 && scrubDirection != 0 {
            // Reverse-direction taps decelerate through 4,3,2,1,stop.
            // Another tap after stopping begins the other direction at 1×.
            scrubSpeed -= 1
            if scrubSpeed == 0 { scrubDirection = 0 }
        } else {
            scrubDirection = requested
            scrubSpeed = 1
        }
        scrubNeedsConfirm = false
        scrubLastTick = Date()
        hideControlsTask?.cancel()
    }

    private func tickPlexScrub() {
        guard scrubSpeed > 0, scrubDirection != 0, let position = scrubPosition else {
            scrubLastTick = nil
            return
        }
        let now = Date()
        let delta = min(0.25, max(0, now.timeIntervalSince(scrubLastTick ?? now)))
        scrubLastTick = now
        // Scale to title duration: 4× traverses the entire title in ~15 s,
        // 3× in ~30 s, 2× in ~60 s and 1× in ~120 s.
        // Only the preview cursor moves; the video keeps playing.
        let secondsPerSecond = plexScrubDuration / (15.0 * pow(2.0, Double(4-scrubSpeed)))
        let next = min(max(0, position + Double(scrubDirection) * secondsPerSecond * delta),
                       max(0, plexScrubDuration - 0.5))
        scrubPosition = next
        requestPlexThumbnail()
        if next <= 0 || next >= plexScrubDuration - 0.5 {
            scrubSpeed = 0
            scrubDirection = 0
            scrubNeedsConfirm = true
        }
    }

    private func handlePlexScrubOK() {
        guard let position = scrubPosition else {
            // First OK on the seek bar arms the non-destructive preview.
            if let plex = plexSession {
                scrubPosition = currentPlexPosition(plex)
                scrubNeedsConfirm = false
                requestPlexThumbnail()
            }
            return
        }
        if scrubSpeed > 0 {
            scrubSpeed = 0
            scrubDirection = 0
            scrubNeedsConfirm = true
            scrubLastTick = nil
            return
        }
        // Second OK commits the chosen position.
        cancelPlexScrub()
        guard let plex = plexSession else { return }
        let start = player.currentItem?.seekableTimeRanges.first.map {
            CMTimeGetSeconds($0.timeRangeValue.start)
        } ?? 0
        let relative = position - plex.offsetSeconds + (start.isFinite ? start : 0)
        if position >= plex.offsetSeconds && relative >= minimum && relative <= maximum {
            seekTo(relative)
        } else {
            Task {
                await replacePlexStream(at: position, audio: audioID,
                                        captions: subtitleID, change: nil)
            }
        }
    }

    private func cancelPlexScrub() {
        scrubSpeed = 0
        scrubDirection = 0
        scrubPosition = nil
        scrubLastTick = nil
        scrubNeedsConfirm = false
        scrubThumbnailTask?.cancel()
        scrubThumbnailRequestID += 1
        scrubThumbnailLoading = false
        scrubThumbnail = nil
        scrubThumbnailSeconds = nil
    }

    private func requestPlexThumbnail() {
        guard let plex = plexSession, let position = scrubPosition,
              plexMetadata?.preview_available == true else { return }
        // Limit image requests to one every ~350ms and allow an in-flight
        // request to finish instead of cancelling it every animation tick.
        let now = Date()
        guard !scrubThumbnailLoading,
              now.timeIntervalSince(scrubLastThumbnailAttempt) >= 0.35 else { return }
        let second = max(0, Int(position / 10) * 10)
        guard second != scrubThumbnailSeconds else { return }
        scrubThumbnailSeconds = second
        scrubLastThumbnailAttempt = now
        if let cached = scrubThumbnailCache[second] {
            scrubThumbnail = cached
            return
        }
        scrubThumbnailLoading = true
        let requestID = scrubThumbnailRequestID
        scrubThumbnailTask = Task {
            defer { scrubThumbnailLoading = false }
            guard let url = URL(string: "/api/player/\(plex.id)/preview?at=\(second)",
                                relativeTo: plex.serverURL)?.absoluteURL else { return }
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 5
                let (bytes, response) = try await URLSession.shared.data(for: request)
                guard !Task.isCancelled, requestID == scrubThumbnailRequestID,
                      (response as? HTTPURLResponse)?.statusCode == 200,
                      let image = UIImage(data: bytes) else { return }
                if scrubThumbnailCache.count >= 80 { scrubThumbnailCache.removeAll() }
                scrubThumbnailCache[second] = image
                // Ignore completed requests for distant, stale slider positions.
                if let current = scrubPosition, abs(current-Double(second)) <=
                    max(40, plexScrubDuration / 30) {
                    scrubThumbnail = image
                }
            } catch {
                // Not all Plex movies have generated preview indexes.
            }
        }
    }

    private func exitPlayback() {
        cancelPlexScrub()
        guard !explicitlyClosing else { return }
        explicitlyClosing = true
        hideControlsTask?.cancel()
        player.pause()
        if !session.isTablo {
            let active = replacementPlex
            let previous = retiringPlex
            let original = session
            Task {
                if let active { await PlexPlaybackService.stop(active) }
                if let previous, previous.id != active?.id,
                   previous.id != original.id {
                    await PlexPlaybackService.stop(previous)
                }
                await original.stop()
            }
        }
        dismiss()
    }
    private func goLive() {
        guard session.isTablo else { return }
        seekTo(maximum - 2)
        player.play()
        isPaused = false
    }
    private func reveal() {
        controlsVisible = true
        selectedControl = "pause"
        scheduleHideControls()
    }
    private func hideControls() {
        hideControlsTask?.cancel()
        controlsVisible = false
        selectedControl = "pause"
    }
    private func scheduleHideControls() {
        hideControlsTask?.cancel()
        hideControlsTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            hideControls()
        }
    }
    private func playPause() {
        if controlsVisible { scheduleHideControls() }
        guard !changingTrack, !restoringLongPause else { return }
        if player.rate == 0 {
            let pausedTooLong = pauseBeganAt.map {
                Date().timeIntervalSince($0) >= 90
            } ?? false
            let resumeAt = pausePosition
            pauseBeganAt = nil
            pausePosition = nil
            if !session.isTablo, pausedTooLong, let position = resumeAt {
                restoringLongPause = true
                Task {
                    await replacePlexStream(at: position, audio: audioID,
                                            captions: subtitleID, change: "long-pause")
                    restoringLongPause = false
                }
            } else {
                player.play()
                isPaused = false
            }
        } else {
            if let plex = plexSession {
                pausePosition = currentPlexPosition(plex)
            }
            pauseBeganAt = Date()
            player.pause()
            isPaused = true
        }
    }
    private func time(_ seconds: Double) -> String {
        guard seconds.isFinite else { return "0:00" }
        let value = Int(max(0,seconds))
        if value >= 3600 {
            return String(format: "%d:%02d:%02d", value/3600, (value%3600)/60, value%60)
        }
        return String(format: "%d:%02d", value/60, value%60)
    }
    private func updateProgress() {
        guard let item = player.currentItem else { return }
        if let target = pendingTrackSeek, item.status == .readyToPlay,
           !applyingTrackSeek {
            applyingTrackSeek = true
            pendingTrackSeek = nil
            player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                        toleranceBefore: .zero, toleranceAfter: .zero) { finished in
                DispatchQueue.main.async {
                    applyingTrackSeek = false
                    if finished {
                        updateProgress()
                        player.play()
                    } else {
                        optionsError = "Plex could not seek to the saved playback position."
                    }
                }
            }
        }
        let current = CMTimeGetSeconds(player.currentTime())
        if current.isFinite {
            elapsed = current
            if !session.isTablo, plexPlaybackOrigin == nil, videoReady,
               item.status == .readyToPlay {
                plexPlaybackOrigin = current
            }
        }
        let ranges = item.seekableTimeRanges
        if let first = ranges.first, let last = ranges.last {
            let low = CMTimeGetSeconds(first.timeRangeValue.start)
            let end = CMTimeGetSeconds(CMTimeRangeGetEnd(last.timeRangeValue))
            if low.isFinite && end.isFinite && end > low {
                minimum = low
                maximum = end
            }
        } else {
            let duration = CMTimeGetSeconds(item.duration)
            if duration.isFinite && duration > 0 {
                minimum = 0; maximum = duration
            }
        }
        if maximum > minimum {
            seekProgress = min(1,max(0,(elapsed-minimum)/(maximum-minimum)))
        }
        isPaused = player.rate == 0
    }
    private func skip(_ seconds: Double) {
        guard maximum > minimum else { return }
        seekTo(elapsed + seconds)
    }
    private func seekTo(_ seconds: Double) {
        let safeEnd = max(minimum, maximum-1.5)
        guard safeEnd > minimum else { return }
        let target = max(minimum,min(safeEnd,seconds))
        player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }
    private func restartVideo() {
        streamError = nil
        checkedPlayback = false
        recoveredOnce = true
        videoReady = false
        plexPlaybackOrigin = nil
        streamStartedAt = Date()
        player.replaceCurrentItem(with: AVPlayerItem(url: currentSession.playlist))
        player.play()
    }
    private func monitorPlexStartup() {
        guard !session.isTablo, !videoReady, !checkedPlayback else { return }
        let age = Date().timeIntervalSince(streamStartedAt)
        if age >= 10 && !recoveredOnce {
            recoveredOnce = true
            // Retry an HLS master that may have been opened before Plex
            // finished generating its first playable media segments.
            plexPlaybackOrigin = nil
            player.replaceCurrentItem(with: AVPlayerItem(url: currentSession.playlist))
            player.play()
            streamStartedAt = Date()
            return
        }
        if age >= 20 {
            checkedPlayback = true
            if let previous = retiringPlex, let failed = replacementPlex {
                // A new transcoder that never displays a frame must not
                // destroy a known-good Plex playback session.
                retiringPlex = nil
                replacementPlex = previous
                pendingTrackSeek = nil
                applyingTrackSeek = false
                plexPlaybackOrigin = previous.offsetSeconds == 0 ? 0 : nil
                player.replaceCurrentItem(with: AVPlayerItem(url: previous.playlistURL))
                player.play()
                videoReady = false
                streamStartedAt = Date()
                recoveredOnce = true
                Task { await PlexPlaybackService.stop(failed) }
                optionsError = "The selected Plex track could not produce playable video. Restored the previous stream."
                return
            }
            Task {
                var message = player.currentItem?.error?.localizedDescription ??
                    "No video frame was received from the Plex HLS stream."
                if case .plex(let plexSession) = currentSession {
                    if let url = URL(string: "/api/player/\(plexSession.id)/diagnostics",
                                     relativeTo: plexSession.serverURL)?.absoluteURL,
                       let (data, _) = try? await URLSession.shared.data(from: url),
                       let info = try? JSONSerialization.jsonObject(with: data)
                                  as? [String: Any] {
                        let lastHTTP = info["last_http"].map { String(describing: $0) } ?? "unknown"
                        let playlists = info["playlists"].map { String(describing: $0) } ?? "0"
                        let segments = info["segments"].map { String(describing: $0) } ?? "0"
                        let error = info["last_error"].map { String(describing: $0) } ?? "none"
                        message += "\nServer: HTTP \(lastHTTP), playlists \(playlists), segments \(segments), error \(error)."
                    }
                }
                if !videoReady { streamError = message }
            }
        }
    }
}

private final class UniversalMediaRemoteView: UIView {
    var onMove: ((String) -> Void)?
    var onSelect: (() -> Void)?
    var onBack: (() -> Void)?
    var onPlayPause: (() -> Void)?
    override var canBecomeFocused: Bool { true }
    #if os(tvOS)
    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window != nil {
            DispatchQueue.main.async { [weak self] in
                self?.setNeedsFocusUpdate()
                self?.updateFocusIfNeeded()
            }
        }
    }
    override func shouldUpdateFocus(in context: UIFocusUpdateContext) -> Bool {
        if context.previouslyFocusedView === self,
           context.nextFocusedView !== self { return false }
        return super.shouldUpdateFocus(in: context)
    }
    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var handled = false
        for press in presses {
            switch press.type {
            case .upArrow: onMove?("up"); handled = true
            case .downArrow: onMove?("down"); handled = true
            case .leftArrow: onMove?("left"); handled = true
            case .rightArrow: onMove?("right"); handled = true
            case .select: onSelect?(); handled = true
            case .menu: onBack?(); handled = true
            case .playPause: onPlayPause?(); handled = true
            default: break
            }
        }
        if !handled { super.pressesBegan(presses, with: event) }
    }
    #endif
}

private struct UniversalMediaRemoteReceiver: UIViewRepresentable {
    let onMove: (String) -> Void
    let onSelect: () -> Void
    let onBack: () -> Void
    let onPlayPause: () -> Void
    func makeUIView(context: Context) -> UniversalMediaRemoteView {
        let view = UniversalMediaRemoteView()
        view.backgroundColor = .clear
        return view
    }
    func updateUIView(_ view: UniversalMediaRemoteView, context: Context) {
        view.onMove = onMove
        view.onSelect = onSelect
        view.onBack = onBack
        view.onPlayPause = onPlayPause
    }
}

private final class UniversalVideoLayerView: UIView {
    override static var layerClass: AnyClass { AVPlayerLayer.self }
    var videoLayer: AVPlayerLayer { layer as! AVPlayerLayer }
}

/// tvOS and iOS share exactly this video-only surface; native AVKit overlays
/// are intentionally never created, preventing unexpected opaque white sheets.
private struct UniversalVideoSurface: UIViewRepresentable {
    let player: AVPlayer
    let onReady: (Bool) -> Void
    final class Coordinator {
        var observation: NSKeyValueObservation?
    }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> UniversalVideoLayerView {
        let view = UniversalVideoLayerView()
        view.isUserInteractionEnabled = false
        view.backgroundColor = .black
        view.videoLayer.videoGravity = .resizeAspect
        view.videoLayer.player = player
        context.coordinator.observation = view.videoLayer.observe(
            \.isReadyForDisplay, options: [.initial, .new]) { layer, _ in
                DispatchQueue.main.async { onReady(layer.isReadyForDisplay) }
            }
        return view
    }
    func updateUIView(_ view: UniversalVideoLayerView, context: Context) {
        if view.videoLayer.player !== player {
            view.videoLayer.player = player
        }
    }
}
