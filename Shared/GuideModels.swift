import SwiftUI

struct GuideResponse: Decodable {
    let channels: [GuideChannel]
}

struct GuideChannel: Decodable, Identifiable {
    let id: String
    let label: String
    let source: String
    let number: String?
    let network: String?
    let favorite: Bool?
    let scheduleUnavailable: Bool?
    let philoChannelURL: String?
    let programs: [GuideProgram]

    enum CodingKeys: String, CodingKey {
        case id, label, source, number, network, favorite, programs
        case scheduleUnavailable = "schedule_unavailable"
        case philoChannelURL = "philo_channel_url"
    }
}

struct GuideProgram: Decodable, Identifiable {
    let title: String
    let subtitle: String?
    let start: String
    let stop: String
    let description: String?
    let year: Int?
    let programKind: String?
    let plex: PlexMatch?
    let philoLinks: PhiloLinks?

    var id: String { "\(title)|\(start)|\(stop)" }

    enum CodingKeys: String, CodingKey {
        case title, subtitle, start, stop, description, year, plex
        case philoLinks = "philo_links"
        case programKind = "program_kind"
    }

    var startDate: Date? { GuideDate.parse(start) }
    var endDate: Date? { GuideDate.parse(stop) }
    var isOnNow: Bool {
        guard let startDate, let endDate else { return false }
        return startDate <= Date() && Date() < endDate
    }
    var timeRange: String {
        guard let startDate, let endDate else { return "" }
        return "\(startDate.formatted(date: .omitted, time: .shortened)) – \(endDate.formatted(date: .omitted, time: .shortened))"
    }
}

struct PhiloLink: Decodable { let url: String? }
struct PhiloLinks: Decodable { let channel: PhiloLink? }

struct PlexMatch: Decodable {
    // The Unraid API sends Plex rating_key as a numeric string.
    let ratingKey: String?
    enum CodingKeys: String, CodingKey { case ratingKey = "rating_key" }
}

enum GuideDate {
    static func parse(_ value: String) -> Date? {
        let full = ISO8601DateFormatter()
        full.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = full.date(from: value) { return date }
        full.formatOptions = [.withInternetDateTime]
        return full.date(from: value)
    }
}

enum GuideServiceError: LocalizedError {
    case missingServer
    case invalidAddress
    case httpError(Int)

    var errorDescription: String? {
        switch self {
        case .missingServer: return "Enter your Unraid Universal Guide server address in Settings."
        case .invalidAddress: return "Use your Unraid server's local URL, for example http://192.168.1.10:8787"
        case .httpError(let status): return "The guide server returned HTTP \(status)."
        }
    }
}

@MainActor
final class GuideStore: ObservableObject {
    @Published var channels: [GuideChannel] = []
    @Published var error: String?
    @Published var isLoading = false
    @Published var lastUpdated: Date?

    func refresh(server: String) async {
        let trimmed = server.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !trimmed.isEmpty else {
            error = GuideServiceError.missingServer.localizedDescription
            return
        }
        guard let base = URL(string: trimmed),
              let scheme = base.scheme?.lowercased(),
              (scheme == "http" || scheme == "https"),
              base.host != nil,
              base.user == nil, base.password == nil,
              var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            error = GuideServiceError.invalidAddress.localizedDescription
            return
        }
        parts.path = "/api/guide"
        parts.queryItems = [URLQueryItem(name: "hours", value: "8")]
        guard let url = parts.url else {
            error = GuideServiceError.invalidAddress.localizedDescription
            return
        }

        isLoading = true
        error = nil
        defer { isLoading = false }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 15
            request.setValue("application/json", forHTTPHeaderField: "Accept")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw GuideServiceError.invalidAddress
            }
            guard (200..<300).contains(http.statusCode) else {
                throw GuideServiceError.httpError(http.statusCode)
            }
            channels = try JSONDecoder().decode(GuideResponse.self, from: data).channels
            lastUpdated = Date()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ProgramSelection: Hashable {
    let channelLabel: String
    let source: String
    let program: GuideProgram

    init(channel: GuideChannel, program: GuideProgram) {
        channelLabel = channel.label
        source = channel.source
        self.program = program
    }

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.channelLabel == rhs.channelLabel &&
        lhs.source == rhs.source && lhs.program.id == rhs.program.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(channelLabel)
        hasher.combine(source)
        hasher.combine(program.id)
    }
}

struct GuideSettingsView: View {
    @Binding var serverURL: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Universal Guide server") {
                    TextField("http://192.168.1.10:8787", text: $serverURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("Enter the local URL of your existing Universal Guide Docker container on Unraid.")
                        .foregroundStyle(.secondary)
                }
                Section {
                    Button("Save and Connect") { dismiss() }
                }
            }
            .navigationTitle("Settings")
        }
    }
}
