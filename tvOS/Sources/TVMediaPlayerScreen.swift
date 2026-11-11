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

struct TVMediaPlayerScreen: View {
    let session: TVMediaSession
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()
    @State private var controlsVisible = false
    @State private var selectedControl = "pause"
    @State private var elapsed: Double = 0
    @State private var minimum: Double = 0
    @State private var maximum: Double = 1
    @State private var isPaused = false
    @State private var seekProgress: Double = 0
    @State private var isScrubbing = false
    @State private var hideControlsTask: Task<Void, Never>?
    @FocusState private var focused: String?
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)
    private var isCompact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private func isSelected(_ key: String) -> Bool {
        controlsVisible && (isCompact ? selectedControl == key : focused == key)
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            // AVKit VideoPlayer can summon its own opaque system transport
            // panel (the white sheet observed on tvOS). AVPlayerLayer renders
            // video only; our controls are the sole overlay.
            UniversalVideoSurface(player: player)
                .ignoresSafeArea()
                .allowsHitTesting(false)
            if !controlsVisible {
                // Without a focusable surface, the TV remote's OK button
                // never arrives. This invisible, genuinely focusable button
                // opens transport controls on the first Select press.
                Button { reveal() } label: {
                    Color.clear
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                // The full-screen transparent Button was receiving tvOS's
                // giant opaque *focused button* effect, obscuring video.
                // Keep the remote's OK activation but suppress the effect.
                .modifier(UniversalDisableFocusEffect())
                .focused($focused, equals: "reveal")
            }
            if controlsVisible {
                VStack {
                    HStack {
                        Text(session.isTablo ? "TABLO DVR" : "PLEX")
                            .foregroundStyle(purple).font(.caption.bold())
                        Text(session.title).font(.system(size: 26, weight: .bold))
                        Spacer()
                        Button { dismiss() } label: {
                            Image(systemName: "xmark").font(.title3)
                        }
                        .focused($focused, equals: "close")
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 68)
                    .padding(.top, 46)
                    Spacer()
                    HStack(spacing: 55) {
                        playerButton("rewind20", key: "back", size: 68) { skip(-20) }
                        playerButton(isPaused ? "play.fill" : "pause.fill", key: "pause", size: 90) { playPause() }
                        playerButton("goforward.30", key: "forward", size: 68) { skip(30) }
                    }
                    Spacer()
                    VStack(spacing: 10) {
                        HStack {
                            Text(time(elapsed - minimum))
                            Spacer()
                            Text(session.isTablo && maximum-elapsed < 8 ?
                                 "LIVE" : "-\(time(max(0,maximum-elapsed)))")
                        }
                        .font(.system(size: 17, weight: .semibold))
                        if maximum > minimum {
                            // tvOS has no SwiftUI Slider: a native focusable
                            // timeline responds to Left/Right 20/30-second seeks.
                            Button {
                                seekTo(elapsed + 30)
                            } label: {
                                GeometryReader { geometry in
                                    ZStack(alignment: .leading) {
                                        Capsule().fill(.white.opacity(0.25))
                                            .frame(height: 7)
                                        Capsule().fill(purple)
                                            .frame(width: max(0,geometry.size.width*seekProgress),
                                                   height: 7)
                                        Circle().fill(.white)
                                            .frame(width: 22,height: 22)
                                            .overlay(Circle().stroke(
                                                isSelected("seek") ? purple : .clear,
                                                lineWidth: 4))
                                            .offset(x: max(0,(geometry.size.width-22)*seekProgress))
                                    }
                                    .frame(height: geometry.size.height)
                                }
                                .frame(height: 28)
                                .padding(.horizontal, 9)
                            }
                            .buttonStyle(.plain)
                            .focused($focused, equals: "seek")
                        }
                        HStack {
                            if session.isTablo {
                                Button {
                                    seekTo(maximum - 2)
                                    player.play()
                                    isPaused = false
                                } label: {
                                    Label("Go Live", systemImage: "bolt.fill")
                                        .font(.headline.bold())
                                }
                                .focused($focused, equals: "live")
                            }
                            Spacer()
                            Text("OK to select · Back to close")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, 68)
                    .padding(.bottom, 45)
                }
                .background(LinearGradient(colors: [.black.opacity(0.58), .clear, .black.opacity(0.8)],
                                           startPoint: .top, endPoint: .bottom))
                .transition(.opacity)
            }
        }
        .tint(purple)
        .preferredColorScheme(.dark)
        .onChange(of: focused) { _, target in
            #if os(tvOS)
            if let target {
                selectedControl = target
                if controlsVisible { scheduleHideControls() }
            }
            #endif
        }
        .onAppear {
            player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
            player.play()
            isPaused = false
            focused = "reveal"
        }
        .onDisappear {
            hideControlsTask?.cancel()
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task { await session.stop() }
        }
        .onReceive(Timer.publish(every: 0.75, on: .main, in: .common).autoconnect()) { _ in
            updateProgress()
        }
        .modifier(UniversalMediaInput(
            direction: { direction in self.remoteDirection(direction) },
            exit: { self.remoteExit() },
            playPause: { self.playPause() }
        ))
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            if let direction = event.object as? String { remoteDirection(direction) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            if !controlsVisible { reveal() }
            else { activateCurrent() }
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
        .onTapGesture { if !controlsVisible { reveal() } }
    }

    private func remoteDirection(_ direction: String) {
        if controlsVisible { scheduleHideControls() }
        if !controlsVisible {
            if direction == "left" { skip(-20) }
            else if direction == "right" { skip(30) }
            else { reveal() }
            return
        }
        #if os(iOS)
        // iOS receives Backbone events, not a reliable native FocusState.
        // Explicitly select only real, currently visible controls.
        if selectedControl == "seek" && direction == "left" { skip(-20); return }
        if selectedControl == "seek" && direction == "right" { skip(30); return }
        let row = ["back", "pause", "forward"]
        let upper = ["close"]
        let lower = session.isTablo ? ["seek", "live"] : ["seek"]
        switch direction {
        case "left", "right":
            let candidate = selectedControl == "close" ? upper :
                lower.contains(selectedControl) ? lower : row
            let index = candidate.firstIndex(of: selectedControl) ?? 1
            let delta = direction == "left" ? -1 : 1
            selectedControl = candidate[max(0,min(candidate.count-1,index+delta))]
        case "up":
            selectedControl = selectedControl == "close" ? "pause" :
                lower.contains(selectedControl) ? "pause" : "close"
        case "down":
            selectedControl = selectedControl == "close" ? "pause" : "seek"
        default: break
        }
        // Keep tvOS and iOS renderers on the same named playback controls.
        focused = selectedControl
        #else
        // Let Apple's tvOS focus engine move between actual buttons.
        // Its focus changes are displayed by the same isSelected() helper.
        _ = direction
        #endif
    }
    private func activateCurrent() {
        scheduleHideControls()
        switch isCompact ? selectedControl : (focused ?? selectedControl) {
        case "back": skip(-20)
        case "pause": playPause()
        case "forward": skip(30)
        case "seek": skip(30)
        case "live": if session.isTablo { seekTo(maximum-2) }
        case "close": dismiss()
        default: reveal()
        }
    }
    private func remoteExit() {
        if controlsVisible {
            hideControls()
        } else { dismiss() }
    }
    private func hideControls() {
        hideControlsTask?.cancel()
        controlsVisible = false
        selectedControl = "pause"
        focused = "reveal"
    }
    private func scheduleHideControls() {
        hideControlsTask?.cancel()
        hideControlsTask = Task { @MainActor in
            try? await Task.sleep(for: .seconds(5))
            guard !Task.isCancelled else { return }
            hideControls()
        }
    }

    private func playerButton(_ symbol: String, key: String, size: CGFloat,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Group {
                if symbol == "rewind20" {
                    // SF Symbols doesn't provide a universal gobackward.20
                    // glyph. Draw a readable 20-second icon on both platforms.
                    ZStack {
                        Image(systemName: "arrow.counterclockwise")
                            .font(.system(size: size*0.57, weight: .medium))
                        Text("20")
                            .font(.system(size: size*0.23, weight: .bold))
                    }
                } else {
                    Image(systemName: symbol)
                        .font(.system(size: size*0.55, weight: .medium))
                }
            }
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            // No circles at rest: a single purple ring identifies whichever
            // control the remote has selected.
            .background(isSelected(key) ? purple.opacity(0.25) : .clear,
                        in: Circle())
            .overlay(Circle().stroke(isSelected(key) ? purple : .clear,
                                     lineWidth: 3))
        }
        .buttonStyle(.plain)
        .focused($focused, equals: key)
    }
    private func reveal() {
        controlsVisible = true
        selectedControl = "pause"
        focused = "pause"
        scheduleHideControls()
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
        // The Tablo source is a continuously appended disk HLS window. Only
        // allow seeking where AVPlayer actually reports data is available.
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
        if !isScrubbing && maximum > minimum {
            seekProgress = min(1,max(0,(elapsed-minimum)/(maximum-minimum)))
        }
        isPaused = player.rate == 0
    }
    private func skip(_ seconds: Double) {
        guard maximum > minimum else { return }
        seekTo(elapsed + seconds)
    }
    private func seekTo(_ seconds: Double) {
        let safeEnd = max(minimum,maximum-1.5)
        guard safeEnd > minimum else { return }
        let target = max(minimum,min(safeEnd,seconds))
        player.seek(to: CMTime(seconds: target, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }
}

private struct UniversalMediaInput: ViewModifier {
    let direction: (String) -> Void
    let exit: () -> Void
    let playPause: () -> Void

    @ViewBuilder func body(content: Content) -> some View {
        #if os(tvOS)
        content
            .onMoveCommand { directionValue in
                switch directionValue {
                case .left: direction("left")
                case .right: direction("right")
                case .up: direction("up")
                case .down: direction("down")
                default: break
                }
            }
            .onExitCommand(perform: exit)
            .onPlayPauseCommand(perform: playPause)
        #else
        content
        #endif
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
    func makeUIView(context: Context) -> UniversalVideoLayerView {
        let view = UniversalVideoLayerView()
        view.isUserInteractionEnabled = false
        view.backgroundColor = .black
        view.videoLayer.videoGravity = .resizeAspect
        view.videoLayer.player = player
        return view
    }
    func updateUIView(_ uiView: UniversalVideoLayerView, context: Context) {
        if uiView.videoLayer.player !== player { uiView.videoLayer.player = player }
    }
}

private struct UniversalDisableFocusEffect: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        #if os(tvOS)
        content.focusEffectDisabled()
        #else
        content
        #endif
    }
}
