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
    @State private var isPaused = false
    @State private var videoReady = false
    @State private var streamStartedAt = Date()
    @State private var recoveredOnce = false
    @State private var checkedPlayback = false
    @State private var streamError: String?
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
    private var sourceName: String {
        session.isTablo ? (metadata.channelName ?? "Live TV") : "PLEX"
    }
    private var remoteButtons: [String] {
        session.isTablo ? ["back", "pause", "forward", "live"] :
                          ["back", "pause", "forward"]
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            UniversalVideoSurface(player: player) { ready in
                videoReady = ready
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)

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
                            if !metadata.detailLine.isEmpty {
                                Text(metadata.detailLine)
                                    .font(.system(size: isCompact ? 13 : 19))
                                    .foregroundStyle(.white.opacity(0.75))
                                    .lineLimit(2)
                            }
                        }
                        Spacer()
                        Button { dismiss() } label: {
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
                            Text(time(max(0, elapsed-minimum)))
                            Spacer()
                            Text(session.isTablo && maximum-elapsed < 8 ? "LIVE" :
                                 "-\(time(max(0,maximum-elapsed)))")
                        }
                        .font(.system(size: isCompact ? 13 : 18, weight: .medium))
                        .foregroundStyle(.white)

                        Button { seekTo(elapsed + 30) } label: {
                            GeometryReader { geometry in
                                let usable = max(0, geometry.size.width-24)
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Color.white.opacity(0.36))
                                        .frame(height: 6)
                                    Capsule().fill(purple.opacity(0.6))
                                        .frame(width: geometry.size.width*seekProgress,
                                               height: 6)
                                    Circle().fill(.white)
                                        .frame(width: 24, height: 24)
                                        .overlay(Circle().stroke(
                                            isSelected("seek") ? purple : .clear,
                                            lineWidth: 4))
                                        .offset(x: usable*seekProgress)
                                }
                                .frame(height: geometry.size.height)
                            }
                            .frame(height: 32)
                        }
                        .buttonStyle(.plain)

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
                            if session.isTablo {
                                Button {
                                    goLive()
                                } label: {
                                    Label("Go Live", systemImage: "bolt.fill")
                                        .font(.system(size: isCompact ? 12 : 18, weight: .semibold))
                                        .foregroundStyle(.white)
                                        .padding(10)
                                        .overlay(RoundedRectangle(cornerRadius: 9)
                                            .stroke(isSelected("live") ? purple : .clear,
                                                    lineWidth: 2))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .frame(maxWidth: .infinity)
                        Text(selectedControl == "seek" ?
                             "Left / Right to seek · Down for playback controls" :
                             "Up for timeline · Back to hide controls")
                            .font(.system(size: isCompact ? 10 : 13))
                            .foregroundStyle(.white.opacity(0.55))
                    }
                    .padding(.horizontal, isCompact ? 22 : 80)
                    .padding(.bottom, isCompact ? 16 : 36)
                }
                .background(LinearGradient(
                    colors: [.black.opacity(0.62), .clear, .black.opacity(0.82)],
                    startPoint: .top, endPoint: .bottom))
                .transition(.opacity)
            }
            #if os(tvOS)
            UniversalMediaRemoteReceiver(
                onMove: remoteDirection,
                onSelect: { if controlsVisible { activateCurrent() } else { reveal() } },
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
            let item = AVPlayerItem(url: session.playlist)
            player.replaceCurrentItem(with: item)
            player.play()
            isPaused = false
        }
        .onDisappear {
            hideControlsTask?.cancel()
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task { await session.stop() }
        }
        .onReceive(Timer.publish(every: 0.75, on: .main, in: .common).autoconnect()) { _ in
            updateProgress()
            monitorPlexStartup()
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            if let direction = event.object as? String { remoteDirection(direction) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            if controlsVisible { activateCurrent() } else { reveal() }
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
        .alert("Plex playback couldn't start",
               isPresented: Binding(get: { streamError != nil },
                                    set: { if !$0 { streamError = nil } })) {
            Button("Retry") { restartVideo() }
            Button("Return to Guide") { dismiss() }
        } message: {
            Text(streamError ?? "The player did not receive a video frame.")
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
            else if selectedControl == "live" { selectedControl = "pause" }
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
        scheduleHideControls()
        switch selectedControl {
        case "back": skip(-20)
        case "pause": playPause()
        case "forward": skip(30)
        case "seek": seekTo(elapsed)
        case "live": goLive()
        case "close": dismiss()
        default: reveal()
        }
    }
    private func remoteExit() {
        if controlsVisible { hideControls() } else { dismiss() }
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
        if player.rate == 0 {
            player.play(); isPaused = false
        } else {
            player.pause(); isPaused = true
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
        let current = CMTimeGetSeconds(player.currentTime())
        if current.isFinite { elapsed = current }
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
        streamStartedAt = Date()
        player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
        player.play()
    }
    private func monitorPlexStartup() {
        guard !session.isTablo, !videoReady, !checkedPlayback else { return }
        let age = Date().timeIntervalSince(streamStartedAt)
        if age >= 10 && !recoveredOnce {
            recoveredOnce = true
            // Retry an HLS master that may have been opened before Plex
            // finished generating its first playable media segments.
            player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
            player.play()
            streamStartedAt = Date()
            return
        }
        if age >= 20 {
            checkedPlayback = true
            Task {
                var message = player.currentItem?.error?.localizedDescription ??
                    "No video frame was received from the Plex HLS stream."
                if case .plex(let plexSession) = session {
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
