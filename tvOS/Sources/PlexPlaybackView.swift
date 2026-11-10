import SwiftUI
import AVKit

/// Backend-created, token-free HLS session. Never place Plex credentials on Apple TV.
struct PlexPlaybackSession: Identifiable {
    let id: String
    let playlistURL: URL
    let serverURL: URL
    let title: String
}

enum PlexPlaybackError: LocalizedError {
    case invalidServer
    case invalidSession
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidServer: return "Check the Universal Guide server address in Settings."
        case .invalidSession: return "The guide server returned an invalid Plex playback session."
        case .server(let message): return message
        }
    }
}

private struct PlexStartResponse: Decodable {
    let title: String
    let playlist_url: String
}

private struct PlexServerError: Decodable {
    let detail: String?
}

enum PlexPlaybackService {
    static func baseURL(_ address: String) throws -> URL {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: trimmed),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else {
            throw PlexPlaybackError.invalidServer
        }
        return url
    }

    static func start(serverURL: String, ratingKey: Int, mode: String,
                      airingStart: String, airingEnd: String) async throws -> PlexPlaybackSession {
        let base = try baseURL(serverURL)
        guard let startURL = URL(string: "/api/player/start", relativeTo: base)?.absoluteURL else {
            throw PlexPlaybackError.invalidServer
        }
        var request = URLRequest(url: startURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var payload: [String: Any] = ["rating_key": ratingKey, "mode": mode]
        if mode == "join" {
            payload["airing_start"] = airingStart
            payload["airing_end"] = airingEnd
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw PlexPlaybackError.server("Unable to contact the playback server.")
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = try? JSONDecoder().decode(PlexServerError.self, from: data)
            throw PlexPlaybackError.server(detail?.detail ?? "Plex playback failed (HTTP \(http.statusCode)).")
        }
        let info = try JSONDecoder().decode(PlexStartResponse.self, from: data)
        // The backend must return its own session resource, not an upstream Plex URL.
        guard info.playlist_url.hasPrefix("/api/player/"),
              info.playlist_url.hasSuffix("/master.m3u8"),
              let playlist = URL(string: info.playlist_url, relativeTo: base)?.absoluteURL,
              let sessionID = info.playlist_url.split(separator: "/").dropFirst(2).first.map(String.init),
              !sessionID.isEmpty else {
            throw PlexPlaybackError.invalidSession
        }
        return PlexPlaybackSession(id: sessionID, playlistURL: playlist,
                                   serverURL: base, title: info.title)
    }

    static func stop(_ session: PlexPlaybackSession) async {
        guard let url = URL(string: "/api/player/\(session.id)", relativeTo: session.serverURL)?.absoluteURL else {
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 10
        _ = try? await URLSession.shared.data(for: request)
    }
}

struct PlexPlaybackScreen: View {
    let session: PlexPlaybackSession
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player)
                .ignoresSafeArea()
            Button {
                dismiss()
            } label: {
                Label("Back to Guide", systemImage: "xmark.circle.fill")
            }
            .padding(35)
        }
        .onAppear {
            player.replaceCurrentItem(with: AVPlayerItem(url: session.playlistURL))
            player.play()
        }
        .onDisappear {
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task { await PlexPlaybackService.stop(session) }
        }
    }
}
