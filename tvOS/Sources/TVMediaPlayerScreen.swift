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
    @FocusState private var focused: String?
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            // Avoid native VideoPlayer controls: the web interface has its
            // own remote-accessible overlay, matching the phone design.
            VideoPlayer(player: player)
                .ignoresSafeArea()
                .allowsHitTesting(false)
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
                        playerButton("gobackward.20", key: "back", size: 68) { skip(-20) }
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
                                                focused == "seek" ? purple : .clear,
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
                            Text("Select to use controls · Menu to close")
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
        .onAppear {
            player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
            player.play()
            isPaused = false
        }
        .onDisappear {
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task { await session.stop() }
        }
        .onReceive(Timer.publish(every: 0.75, on: .main, in: .common).autoconnect()) { _ in
            updateProgress()
        }
        .onPlayPauseCommand { playPause() }
        .onMoveCommand { direction in
            if !controlsVisible {
                switch direction {
                case .left: skip(-20)
                case .right: skip(30)
                default: reveal()
                }
            } else {
                if focused == "seek" {
                    if direction == .left { skip(-20); return }
                    if direction == .right { skip(30); return }
                }
                let order = ["back", "pause", "forward", "seek", "live", "close"]
                guard let index = order.firstIndex(of: focused ?? "pause") else {
                    focused = "pause"; return
                }
                let offset = (direction == .left || direction == .up) ? -1 : 1
                var next = max(0,min(order.count-1,index+offset))
                if !session.isTablo && order[next] == "live" { next = 5 }
                focused = order[next]
                selectedControl = order[next]
            }
        }
        .onExitCommand {
            if controlsVisible { controlsVisible = false }
            else { dismiss() }
        }
        .onTapGesture { if !controlsVisible { reveal() } }
    }

    private func playerButton(_ symbol: String, key: String, size: CGFloat,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: size*0.55, weight: .medium))
                .foregroundStyle(.white)
                .frame(width: size, height: size)
                .background(focused == key ? purple.opacity(0.45) : .clear, in: Circle())
                .overlay(Circle().stroke(focused == key ? purple : .white.opacity(0.55), lineWidth: 3))
        }
        .buttonStyle(.plain)
        .focused($focused, equals: key)
    }
    private func reveal() {
        controlsVisible = true
        selectedControl = "pause"
        focused = "pause"
    }
    private func playPause() {
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
