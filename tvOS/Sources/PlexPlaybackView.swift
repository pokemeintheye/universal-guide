import SwiftUI
import AVKit

/// Backend-created, token-free HLS session. Never place Plex credentials on Apple TV.
struct PlexPlaybackSession: Identifiable {
    let id: String
    let playlistURL: URL
    let serverURL: URL
    let title: String
    let ratingKey: Int
    let offsetSeconds: Double
}

struct PlexChapter: Decodable, Identifiable {
    let title: String
    let start_seconds: Double
    var id: Double { start_seconds }
}

struct PlexTrack: Decodable, Identifiable {
    let id: Int
    let language: String
    let codec: String
    let title: String
    let selected: Bool
    let forced: Bool
    var label: String {
        let detail = title.isEmpty ? codec.uppercased() : title
        return [language, detail].filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

struct PlexPlaybackMetadata: Decodable {
    let chapters: [PlexChapter]
    let audio: [PlexTrack]
    let subtitles: [PlexTrack]
    let duration_seconds: Double
    let preview_available: Bool
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
    let offset_seconds: Double
}

private struct PlexServerError: Decodable {
    let detail: String?
}

private struct PlexProxyCapabilities: Decodable {
    let native_hls_resources: Int
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
                      airingStart: String, airingEnd: String,
                      offsetSeconds: Double? = nil,
                      audioStreamID: Int? = nil,
                      subtitleStreamID: Int? = nil) async throws -> PlexPlaybackSession {
        let base = try baseURL(serverURL)
        guard let checkURL = URL(string: "/api/player/capabilities",
                                 relativeTo: base)?.absoluteURL else {
            throw PlexPlaybackError.invalidServer
        }
        var checkRequest = URLRequest(url: checkURL)
        checkRequest.timeoutInterval = 10
        checkRequest.cachePolicy = .reloadIgnoringLocalCacheData
        let capabilities: PlexProxyCapabilities?
        do {
            let (body, response) = try await URLSession.shared.data(for: checkRequest)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                capabilities = try? JSONDecoder().decode(PlexProxyCapabilities.self,
                                                         from: body)
            } else {
                capabilities = nil
            }
        } catch {
            capabilities = nil
        }
        guard (capabilities?.native_hls_resources ?? 0) >= 2 else {
            throw PlexPlaybackError.server(
                "The Universal Guide Docker container on Unraid needs to be " +
                "updated for native Plex playback. Update the container from " +
                "ghcr.io/pokemeintheye/universal-guide:latest and try again.")
        }
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
        if let offsetSeconds { payload["offset_seconds"] = offsetSeconds }
        if let audioStreamID { payload["audio_stream_id"] = audioStreamID }
        if let subtitleStreamID { payload["subtitle_stream_id"] = subtitleStreamID }
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
            if mode == "resume" {
                // The existing preflight only proved the playlist exists.
                // After a track switch, an HLS playlist may contain only
                // empty MPEG-TS placeholders. Never destroy the working
                // player unless the replacement has actual video data.
                let candidates = childText.components(separatedBy: .newlines)
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty && !$0.hasPrefix("#") }
                    .prefix(5)
                guard !candidates.isEmpty else {
                    throw PlexPlaybackError.server(
                        "The replacement Plex playlist has no video segments.")
                }
                var playable = false
                for candidate in candidates {
                    guard let segment = URL(string: candidate, relativeTo: childURL)?.absoluteURL,
                          segment.scheme == childURL.scheme,
                          segment.host == childURL.host,
                          segment.port == childURL.port,
                          segment.path.hasPrefix("/api/player/\(sessionID)/") else {
                        continue
                    }
                    var segmentRequest = URLRequest(url: segment)
                    segmentRequest.timeoutInterval = 20
                    segmentRequest.cachePolicy = .reloadIgnoringLocalCacheData
                    guard let (segmentBytes, reply) = try? await URLSession.shared.data(for: segmentRequest),
                          (reply as? HTTPURLResponse)?.statusCode == 200 else { continue }
                    // Plex sometimes returns 188-byte placeholder packets
                    // for segments before its transcode offset.
                    if segmentBytes.count > 1880,
                       segmentBytes.first == 0x47 {
                        playable = true
                        break
                    }
                }
                guard playable else {
                    throw PlexPlaybackError.server(
                        "Plex returned HLS playlists but no playable MPEG-TS segments " +
                        "at the requested position. The original player was kept.")
                }
            }
        }
        return PlexPlaybackSession(id: sessionID, playlistURL: playlist,
                                   serverURL: base, title: info.title,
                                   ratingKey: ratingKey, offsetSeconds: info.offset_seconds)
    }

    static func metadata(_ session: PlexPlaybackSession) async throws -> PlexPlaybackMetadata {
        guard let url = URL(string: "/api/player/\(session.id)/metadata",
                            relativeTo: session.serverURL)?.absoluteURL else {
            throw PlexPlaybackError.invalidSession
        }
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw PlexPlaybackError.server("Unable to load Plex chapters and tracks.")
        }
        return try JSONDecoder().decode(PlexPlaybackMetadata.self, from: data)
    }

    static func selectTracks(_ session: PlexPlaybackSession,
                             audioID: Int? = nil,
                             subtitleID: Int? = nil) async throws {
        guard let url = URL(string: "/api/player/\(session.id)/tracks",
                            relativeTo: session.serverURL)?.absoluteURL else {
            throw PlexPlaybackError.invalidSession
        }
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Int] = [:]
        if let audioID { body["audio_stream_id"] = audioID }
        if let subtitleID { body["subtitle_stream_id"] = subtitleID }
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let detail = try? JSONDecoder().decode(PlexServerError.self, from: data)
            throw PlexPlaybackError.server(detail?.detail ?? "Plex rejected the track change.")
        }
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
