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
        // AVPlayer's error alone did not identify which endpoint failed:
        // the previous report showed zero recorded playlist requests. Fetch
        // the token-free HLS master explicitly before creating the player.
        // This also warms the Plex transcoder and gives a precise HTTP error
        // when the requested playlist is not actually available.
        var masterRequest = URLRequest(url: playlist)
        masterRequest.httpMethod = "GET"
        masterRequest.timeoutInterval = 40
        masterRequest.setValue("application/vnd.apple.mpegurl", forHTTPHeaderField: "Accept")
        let (master, masterResponse): (Data, URLResponse)
        do {
            (master, masterResponse) = try await URLSession.shared.data(for: masterRequest)
        } catch {
            throw PlexPlaybackError.server(
                "Unraid could not deliver the Plex HLS playlist: \(error.localizedDescription).")
        }
        guard let masterHTTP = masterResponse as? HTTPURLResponse else {
            throw PlexPlaybackError.server("No HTTP response from the Plex HLS playlist.")
        }
        guard masterHTTP.statusCode == 200 else {
            let detail = (try? JSONDecoder().decode(PlexServerError.self, from: master))?.detail
            throw PlexPlaybackError.server(
                "Plex HLS master returned HTTP \(masterHTTP.statusCode): " +
                (detail ?? "Check the Unraid /api/player proxy and Plex transcoder."))
        }
        guard let playlistText = String(data: master, encoding: .utf8),
              playlistText.trimmingCharacters(in: .whitespacesAndNewlines)
                .hasPrefix("#EXTM3U") else {
            throw PlexPlaybackError.server(
                "Unraid returned HTTP 200 but not a valid Plex HLS playlist.")
        }
        // A valid master alone is not enough for native HLS playback.
        // Verify the first referenced child playlist from the same Unraid
        // proxy. This turns otherwise opaque AVPlayer 404 errors into an
        // actionable explanation before opening the player.
        let lines = playlistText.components(separatedBy: .newlines)
        if let variantTag = lines.firstIndex(where: { $0.hasPrefix("#EXT-X-STREAM-INF") }) {
            let children = lines.dropFirst(variantTag + 1)
            guard let reference = children.first(where: {
                let line = $0.trimmingCharacters(in: .whitespacesAndNewlines)
                return !line.isEmpty && !line.hasPrefix("#")
            }),
                  let childURL = URL(string: reference.trimmingCharacters(in: .whitespaces),
                                     relativeTo: playlist)?.absoluteURL,
                  childURL.scheme == playlist.scheme,
                  childURL.host == playlist.host,
                  childURL.port == playlist.port,
                  childURL.path.hasPrefix("/api/player/\(sessionID)/") else {
                throw PlexPlaybackError.server(
                    "Plex HLS master lists an invalid child playlist URL. Update the Universal Guide Docker container.")
            }
            var childRequest = URLRequest(url: childURL)
            childRequest.timeoutInterval = 30
            childRequest.setValue("application/vnd.apple.mpegurl", forHTTPHeaderField: "Accept")
            let (childData, childResponse): (Data, URLResponse)
            do {
                (childData, childResponse) = try await URLSession.shared.data(for: childRequest)
            } catch {
                throw PlexPlaybackError.server(
                    "Plex HLS child playlist is unreachable: \(error.localizedDescription).")
            }
            guard let childHTTP = childResponse as? HTTPURLResponse else {
                throw PlexPlaybackError.server("Plex HLS child returned no HTTP response.")
            }
            guard childHTTP.statusCode == 200 else {
                throw PlexPlaybackError.server(
                    "Plex HLS child playlist returned HTTP \(childHTTP.statusCode). " +
                    "Update the Universal Guide Docker container on Unraid, then retry.")
            }
            guard let childText = String(data: childData, encoding: .utf8),
                  childText.trimmingCharacters(in: .whitespacesAndNewlines)
                    .hasPrefix("#EXTM3U") else {
                throw PlexPlaybackError.server(
                    "Plex HLS child returned data that is not an HLS playlist.")
            }
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
