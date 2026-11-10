import SwiftUI
import AVKit

private struct PlexMetadata: Decodable {
    let chapters: [PlexChapter]
    let audio: [PlexTrack]
    let subtitles: [PlexTrack]
}
private struct PlexChapter: Decodable, Identifiable {
    let title: String
    let start_seconds: Double
    var id: Double { start_seconds }
}
private struct PlexTrack: Decodable, Identifiable {
    let id: Int
    let title: String
    let language: String
    let codec: String
    let selected: Bool
    var label: String {
        let name = title.isEmpty ? language : title
        return codec.isEmpty ? name : "\(name) · \(codec)"
    }
}
private struct DVRReply: Decodable {
    let session_id: String
    let playlist_url: String
}
private struct PauseReply: Decodable { let captured: Bool }

struct PhoneVideoScreen: View {
    let session: PlaybackSession
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()
    @State private var metadata: PlexMetadata?
    @State private var showTracks = false
    @State private var showChapters = false
    @State private var error: String?
    @State private var message: String?
    @State private var working = false
    @State private var pausedOnTablo = false
    @State private var childSession: String?
    @State private var behindLive = 0

    private var base: URL {
        session.stopURL.deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
    }
    var body: some View {
        ZStack(alignment: .top) {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player)
                .ignoresSafeArea()
            VStack(spacing: 6) {
                HStack(spacing: 9) {
                    Button { dismiss() } label: {
                        Label("Guide", systemImage: "chevron.left")
                    }
                    Text(session.title).font(.caption.bold()).lineLimit(1)
                    Spacer()
                    if session.kind == "plex" {
                        Button("Chapters") { showChapters = true }.disabled(metadata?.chapters.isEmpty != false)
                        Button("Audio / Subtitles") { showTracks = true }.disabled(metadata == nil)
                    } else {
                        Button(pausedOnTablo ? "Resume" : "Pause") { toggleTabloPause() }.disabled(working)
                        Button("−30s") { shiftTablo(-30) }.disabled(working)
                        Button("+30s") { shiftTablo(30) }.disabled(working || behindLive <= 0)
                        Button("Live") { returnToLive() }.disabled(working)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .padding(.horizontal, 8)
                if let error {
                    Text(error).foregroundStyle(.orange).font(.footnote)
                        .padding(8).background(.black.opacity(0.85), in: RoundedRectangle(cornerRadius: 7))
                } else if let message {
                    Text(message).font(.caption2).foregroundStyle(.white.opacity(0.9))
                        .padding(6).background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 7))
                }
            }
            .padding(.top, 4)
        }
        .sheet(isPresented: $showChapters) {
            NavigationStack {
                List(metadata?.chapters ?? []) { chapter in
                    Button {
                        seek(to: chapter.start_seconds)
                        showChapters = false
                    } label: {
                        HStack {
                            Text(chapter.title)
                            Spacer()
                            Text(timeText(chapter.start_seconds)).foregroundStyle(.secondary)
                        }
                    }
                }
                .navigationTitle("Plex Chapters")
                .toolbar { Button("Done") { showChapters = false } }
            }
        }
        .sheet(isPresented: $showTracks) {
            NavigationStack {
                List {
                    Section("Audio") {
                        ForEach(metadata?.audio ?? []) { track in
                            Button { changeTrack(audio: track.id, subtitle: nil) } label: {
                                Label(track.label, systemImage: track.selected ? "checkmark.circle.fill" : "circle")
                            }
                        }
                    }
                    Section("Subtitles") {
                        Button("Off") { changeTrack(audio: nil, subtitle: 0) }
                        ForEach(metadata?.subtitles ?? []) { track in
                            Button { changeTrack(audio: nil, subtitle: track.id) } label: {
                                Label(track.label, systemImage: track.selected ? "checkmark.circle.fill" : "circle")
                            }
                        }
                    }
                    Text("Audio and subtitle choices update Plex's active stream selection. Restarting playback may be required for a newly selected track to take effect.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .navigationTitle("Audio & Subtitles")
                .toolbar { Button("Done") { showTracks = false } }
            }
        }
        .onAppear {
            startPlayer(session.playlist)
            if session.kind == "plex" { Task { await loadPlexMetadata() } }
        }
        .onDisappear {
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task {
                if let childSession {
                    await PhoneAPI.delete(try! PhoneAPI.url(base, "/api/tablo/live/\(childSession)"))
                }
                await PhoneAPI.delete(session.stopURL)
            }
        }
    }

    private func timeText(_ value: Double) -> String {
        let seconds = max(0, Int(value))
        return String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
    }
    private func startPlayer(_ url: URL) {
        player.pause()
        player.replaceCurrentItem(with: AVPlayerItem(url: url))
        player.play()
    }
    private func seek(to time: Double) {
        player.seek(to: CMTime(seconds: time, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
    }
    private func plexURL(_ path: String) throws -> URL {
        try PhoneAPI.url(base, path)
    }
    private func loadPlexMetadata() async {
        do {
            metadata = try await PhoneAPI.get(PlexMetadata.self,
                         plexURL("/api/player/\(session.id)/metadata"))
        } catch {
            self.error = "Plex track details unavailable: \(error.localizedDescription)"
        }
    }
    private func changeTrack(audio: Int?, subtitle: Int?) {
        guard !working else { return }
        working = true; error = nil
        Task {
            defer { working = false }
            do {
                var request = URLRequest(url: try plexURL("/api/player/\(session.id)/tracks"))
                request.httpMethod = "PUT"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                var body: [String: Int] = [:]
                if let audio { body["audio_stream_id"] = audio }
                if let subtitle { body["subtitle_stream_id"] = subtitle }
                request.httpBody = try JSONSerialization.data(withJSONObject: body)
                let (_, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                    throw APIError.message("Plex could not select that track.")
                }
                message = "Track selected. Restart playback if the stream does not change."
                await loadPlexMetadata()
                showTracks = false
            } catch { self.error = error.localizedDescription }
        }
    }
    private func tabloURL(_ path: String, parameters: [URLQueryItem] = []) throws -> URL {
        var components = URLComponents(url: try PhoneAPI.url(base, path), resolvingAgainstBaseURL: false)!
        components.queryItems = parameters.isEmpty ? nil : parameters
        guard let url = components.url else { throw APIError.message("Invalid DVR URL") }
        return url
    }
    private func toggleTabloPause() {
        guard session.kind == "tablo" else { return }
        if pausedOnTablo {
            Task { await resumeTablo() }
        } else {
            working = true; error = nil
            Task {
                defer { working = false }
                do {
                    _ = try await PhoneAPI.post(PauseReply.self,
                        tabloURL("/api/tablo/live/\(session.id)/pause-point"))
                    player.pause()
                    pausedOnTablo = true
                    message = "Tablo pause position saved"
                } catch { self.error = error.localizedDescription }
            }
        }
    }
    private func resumeTablo() async {
        guard !working else { return }
        working = true; error = nil
        defer { working = false }
        do {
            await closeChild()
            let url = try tabloURL("/api/tablo/live/\(session.id)/timeshift",
                                   parameters: [URLQueryItem(name: "resume_paused", value: "true")])
            let result = try await PhoneAPI.post(DVRReply.self, url)
            guard result.playlist_url == "/api/tablo/live/\(result.session_id)/index.m3u8" else {
                throw APIError.message("Invalid resumed Tablo playlist.")
            }
            childSession = result.session_id
            behindLive = max(behindLive, 18)
            startPlayer(try PhoneAPI.url(base, result.playlist_url))
            pausedOnTablo = false
            message = "Resumed near saved pause point"
        } catch { self.error = error.localizedDescription }
    }
    private func shiftTablo(_ delta: Int) {
        guard session.kind == "tablo", !working else { return }
        working = true; error = nil
        Task {
            defer { working = false }
            do {
                // Source history is time-window limited, and the backend may
                // reject rewind before enough history exists.
                let requested = max(0, behindLive - delta)
                if requested == 0 { await goLive(); return }
                await closeChild()
                let url = try tabloURL("/api/tablo/live/\(session.id)/timeshift",
                     parameters: [URLQueryItem(name: "seconds_behind_live", value: String(requested))])
                let result = try await PhoneAPI.post(DVRReply.self, url)
                guard result.playlist_url == "/api/tablo/live/\(result.session_id)/index.m3u8" else {
                    throw APIError.message("Invalid Tablo timeshift playlist.")
                }
                childSession = result.session_id
                behindLive = requested
                startPlayer(try PhoneAPI.url(base, result.playlist_url))
                pausedOnTablo = false
                message = "Approximately \(requested)s behind live (beta)"
            } catch { self.error = error.localizedDescription }
        }
    }
    private func returnToLive() {
        guard !working else { return }
        working = true; error = nil
        Task { defer { working = false }; await goLive() }
    }
    private func goLive() async {
        await closeChild()
        behindLive = 0
        pausedOnTablo = false
        startPlayer(session.playlist)
        message = "Playing live"
    }
    private func closeChild() async {
        if let childSession {
            self.childSession = nil
            if let url = try? tabloURL("/api/tablo/live/\(childSession)") {
                await PhoneAPI.delete(url)
            }
        }
    }
}
