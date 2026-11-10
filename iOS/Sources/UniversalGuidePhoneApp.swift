import SwiftUI
import AVKit
import GameController
import UIKit

// Matches the existing Unraid /api/guide response; no credentials are stored.
struct PhoneGuide: Decodable { let channels: [PhoneChannel] }
struct PhoneChannel: Decodable, Identifiable {
    let id: String
    let label: String
    let source: String
    let number: String?
    let network: String?
    let programs: [PhoneProgram]
    var station: String { network?.isEmpty == false ? network! : label }
}
struct PhoneProgram: Decodable, Identifiable {
    let title: String
    let subtitle: String?
    let start: String
    let stop: String
    let description: String?
    let plex: PhonePlex?
    let philoLinks: PhonePhiloLinks?
    let channelID: String?
    var id: String { "\(title)|\(start)|\(stop)" }
    enum CodingKeys: String, CodingKey {
        case title, subtitle, start, stop, description, plex
        case philoLinks = "philo_links"
        case channelID = "channel_id"
    }
    var begins: Date? { DateParser.parse(start) }
    var ends: Date? { DateParser.parse(stop) }
    var onNow: Bool {
        guard let a = begins, let b = ends else { return false }
        return a <= Date() && Date() < b
    }
}
struct PhonePlex: Decodable {
    let ratingKey: String?
    enum CodingKeys: String, CodingKey { case ratingKey = "rating_key" }
}
struct PhonePhiloLinks: Decodable {
    let channel: PhonePhiloLink?
    let show: PhonePhiloLink?
    let episode: PhonePhiloLink?
}
struct PhonePhiloLink: Decodable { let url: String; let label: String? }
enum DateParser {
    // A program's times are read thousands of times while controller selection
    // updates. Formatting each ISO timestamp on *every* redraw was expensive.
    private static let cache: NSCache<NSString, NSDate> = {
        let result = NSCache<NSString, NSDate>()
        result.countLimit = 20000
        return result
    }()
    static func parse(_ raw: String) -> Date? {
        let key = raw as NSString
        if let existing = cache.object(forKey: key) { return existing as Date }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = formatter.date(from: raw) {
            cache.setObject(parsed as NSDate, forKey: key)
            return parsed
        }
        formatter.formatOptions = [.withInternetDateTime]
        guard let parsed = formatter.date(from: raw) else { return nil }
        cache.setObject(parsed as NSDate, forKey: key)
        return parsed
    }
}
enum PhoneAPI {
    static func base(_ value: String) throws -> URL {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let u = URL(string: trimmed),
              ["http", "https"].contains(u.scheme?.lowercased() ?? ""),
              u.host != nil, u.user == nil, u.password == nil else {
            throw APIError.message("Set a valid local Unraid URL in Settings.")
        }
        return u
    }
    static func url(_ base: URL, _ path: String) throws -> URL {
        guard let url = URL(string: path, relativeTo: base)?.absoluteURL else {
            throw APIError.message("Invalid server endpoint")
        }
        return url
    }
    static func get<T: Decodable>(_ type: T.Type, _ url: URL) async throws -> T {
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        return try await send(type, request)
    }
    static func post<T: Decodable>(_ type: T.Type, _ url: URL, body: Data? = nil) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 35
        request.httpBody = body
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        return try await send(type, request)
    }
    static func send<T: Decodable>(_ type: T.Type, _ request: URLRequest) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw APIError.message("Unable to contact Universal Guide.")
        }
        guard (200..<300).contains(response.statusCode) else {
            let detail = (try? JSONDecoder().decode(ServerError.self, from: data))?.detail
            throw APIError.message(detail ?? "Server responded HTTP \(response.statusCode).")
        }
        return try JSONDecoder().decode(type, from: data)
    }
    static func delete(_ url: URL) async {
        var request = URLRequest(url: url)
        request.httpMethod = "DELETE"
        request.timeoutInterval = 10
        _ = try? await URLSession.shared.data(for: request)
    }
}
struct ServerError: Decodable { let detail: String? }
enum APIError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        if case .message(let message) = self { return message }
        return nil
    }
}
struct PlayStart: Decodable { let session_id: String?; let playlist_url: String; let title: String? }
struct PlaybackSession: Identifiable {
    let id: String
    let kind: String
    let title: String
    let playlist: URL
    let stopURL: URL
}
enum PhonePlayback {
    static func plex(server: String, program: PhoneProgram, mode: String) async throws -> PlaybackSession {
        guard let key = Int(program.plex?.ratingKey ?? ""), key > 0 else {
            throw APIError.message("This program is not matched to playable Plex media.")
        }
        let base = try PhoneAPI.base(server)
        var body: [String: Any] = ["rating_key": key, "mode": mode]
        if mode == "join" {
            body["airing_start"] = program.start
            body["airing_end"] = program.stop
        }
        let json = try JSONSerialization.data(withJSONObject: body)
        let result = try await PhoneAPI.post(PlayStart.self, PhoneAPI.url(base, "/api/player/start"), body: json)
        guard result.playlist_url.hasPrefix("/api/player/"),
              result.playlist_url.hasSuffix("/master.m3u8"),
              let sessionID = result.playlist_url.split(separator: "/").dropFirst(2).first,
              !sessionID.isEmpty else {
            throw APIError.message("Invalid Plex session response.")
        }
        return PlaybackSession(id: String(sessionID), kind: "plex", title: result.title ?? program.title,
                               playlist: try PhoneAPI.url(base, result.playlist_url),
                               stopURL: try PhoneAPI.url(base, "/api/player/\(sessionID)"))
    }
    static func tablo(server: String, channel: PhoneChannel, program: PhoneProgram) async throws -> PlaybackSession {
        guard channel.source == "tablo", let id = program.channelID,
              id.hasPrefix("/guide/channels/") else {
            throw APIError.message("Tablo channel identifier is missing from the guide.")
        }
        let base = try PhoneAPI.base(server)
        var url = URLComponents(url: try PhoneAPI.url(base, "/api/tablo/live/start"), resolvingAgainstBaseURL: false)!
        url.queryItems = [URLQueryItem(name: "channel_id", value: id)]
        let start = try await PhoneAPI.post(PlayStart.self, url.url!)
        guard let session = start.session_id,
              start.playlist_url == "/api/tablo/live/\(session)/index.m3u8",
              !session.isEmpty else {
            throw APIError.message("Invalid Tablo live session response.")
        }
        return PlaybackSession(id: session, kind: "tablo", title: channel.station,
                               playlist: try PhoneAPI.url(base, start.playlist_url),
                               stopURL: try PhoneAPI.url(base, "/api/tablo/live/\(session)"))
    }
}

@MainActor final class PhoneGuideStore: ObservableObject {
    @Published var channels: [PhoneChannel] = []
    @Published var error: String?
    @Published var loading = false
    @Published var lastLoaded: Date?
    func refresh(server: String) async {
        loading = true; error = nil
        defer { loading = false }
        do {
            let base = try PhoneAPI.base(server)
            let url = try PhoneAPI.url(base, "/api/guide?hours=8")
            channels = try await PhoneAPI.get(PhoneGuide.self, url).channels
            lastLoaded = Date()
        } catch { self.error = error.localizedDescription }
    }
}

enum PadAction: String {
    case up = "↑", down = "↓", left = "←", right = "→"
    case select = "A", back = "B", options = "X", now = "Y"
    case previousPage = "LB", nextPage = "RB"
}
// Gamepad maps to the same semantic navigation events we can share with tvOS.
@MainActor final class BackboneController: ObservableObject {
    @Published var connected = false
    @Published var lastInput = "None"
    var onAction: ((PadAction) -> Void)?
    private var observers: [NSObjectProtocol] = []
    init() {
        observers.append(NotificationCenter.default.addObserver(forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.configure() }
        })
        observers.append(NotificationCenter.default.addObserver(forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.configure() }
        })
        configure()
    }
    deinit { observers.forEach(NotificationCenter.default.removeObserver) }
    func configure() {
        for controller in GCController.controllers() {
            controller.extendedGamepad?.valueChangedHandler = nil
        }
        guard let pad = GCController.controllers().compactMap({ $0.extendedGamepad }).first else {
            connected = false; return
        }
        connected = true
        pad.dpad.up.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.up) } }
        pad.dpad.down.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.down) } }
        pad.dpad.left.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.left) } }
        pad.dpad.right.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.right) } }
        pad.buttonA.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.select) } }
        pad.buttonB.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.back) } }
        pad.buttonX.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.options) } }
        pad.buttonY.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.now) } }
        pad.leftShoulder.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.previousPage) } }
        pad.rightShoulder.pressedChangedHandler = { [weak self] _, _, pressed in if pressed { self?.emit(.nextPage) } }
    }
    private func emit(_ action: PadAction) {
        Task { @MainActor in
            self.lastInput = action.rawValue
            self.onAction?(action)
        }
    }
}

@main struct UniversalGuidePhoneApp: App {
    var body: some Scene {
        WindowGroup { PhoneWebGuide() }
    }
}
