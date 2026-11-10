import SwiftUI

@main
struct UniversalGuideTVApp: App {
    var body: some Scene {
        WindowGroup {
            GuideHomeView()
        }
    }
}

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
    let programs: [GuideProgram]

    enum CodingKeys: String, CodingKey {
        case id, label, source, number, network, favorite, programs
        case scheduleUnavailable = "schedule_unavailable"
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

    var id: String { "\(title)|\(start)|\(stop)" }

    enum CodingKeys: String, CodingKey {
        case title, subtitle, start, stop, description, year, plex
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

struct GuideHomeView: View {
    @AppStorage("guide.serverURL") private var serverURL = ""
    @StateObject private var store = GuideStore()
    @State private var showSettings = false
    @State private var favoritesOnly = false

    private var visibleChannels: [GuideChannel] {
        store.channels.filter { !favoritesOnly || $0.favorite == true }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.055, green: 0.065, blue: 0.12).ignoresSafeArea()
                VStack(alignment: .leading, spacing: 22) {
                    header
                    if let error = store.error {
                        ContentUnavailableView("Guide unavailable",
                                               systemImage: "wifi.exclamationmark",
                                               description: Text(error))
                        Button("Open Settings") { showSettings = true }
                    } else if store.isLoading && store.channels.isEmpty {
                        ProgressView("Loading live TV guide…")
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if visibleChannels.isEmpty {
                        ContentUnavailableView("No channels found",
                                               systemImage: "tv",
                                               description: Text("Check the server and channel filters."))
                    } else {
                        ScrollView(.vertical) {
                            LazyVStack(alignment: .leading, spacing: 28) {
                                ForEach(visibleChannels) { channel in
                                    channelRow(channel)
                                }
                            }
                            .padding(.vertical, 12)
                        }
                    }
                }
                .padding(.horizontal, 55)
                .padding(.top, 18)
            }
            .navigationDestination(for: ProgramSelection.self) { selection in
                ProgramDetailView(selection: selection)
            }
            .sheet(isPresented: $showSettings, onDismiss: {
                Task { await store.refresh(server: serverURL) }
            }) {
                GuideSettingsView(serverURL: $serverURL)
            }
            .task { await store.refresh(server: serverURL) }
        }
        .tint(.purple)
    }

    private var header: some View {
        HStack(spacing: 24) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Universal Guide").font(.system(size: 42, weight: .bold))
                Text("Live TV · Plex · Philo · Tablo")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button(favoritesOnly ? "All Channels" : "Favorites") {
                favoritesOnly.toggle()
            }
            Button {
                Task { await store.refresh(server: serverURL) }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .disabled(store.isLoading)
            Button {
                showSettings = true
            } label: {
                Label("Settings", systemImage: "gearshape")
            }
        }
    }

    private func channelRow(_ channel: GuideChannel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 14) {
                Text(channel.number ?? channel.label)
                    .font(.title3.bold())
                if let network = channel.network, !network.isEmpty {
                    Text(network).foregroundStyle(.secondary)
                } else {
                    Text(channel.label).foregroundStyle(.secondary)
                }
                Spacer()
                Text(channel.source.uppercased())
                    .font(.caption.bold())
                    .foregroundStyle(.secondary)
            }
            ScrollView(.horizontal) {
                LazyHStack(spacing: 16) {
                    if channel.programs.isEmpty {
                        Text("Schedule unavailable")
                            .foregroundStyle(.secondary)
                            .frame(width: 360, height: 110)
                            .background(.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
                    }
                    ForEach(channel.programs) { program in
                        NavigationLink(value: ProgramSelection(channel: channel, program: program)) {
                            VStack(alignment: .leading, spacing: 9) {
                                HStack {
                                    if program.isOnNow {
                                        Text("NOW").font(.caption.bold()).foregroundStyle(.green)
                                    }
                                    Text(program.timeRange).font(.caption).foregroundStyle(.secondary)
                                }
                                Text(program.title)
                                    .font(.headline)
                                    .lineLimit(2)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                if let subtitle = program.subtitle, !subtitle.isEmpty {
                                    Text(subtitle).font(.caption).lineLimit(1).foregroundStyle(.secondary)
                                }
                            }
                            .frame(width: 320, height: 110, alignment: .topLeading)
                            .padding(20)
                            .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(.card)
                    }
                }
                .padding(.horizontal, 5)
            }
            .scrollClipDisabled()
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

struct ProgramDetailView: View {
    let selection: ProgramSelection

    var body: some View {
        ZStack {
            Color(red: 0.055, green: 0.065, blue: 0.12).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 24) {
                Text(selection.program.title).font(.system(size: 50, weight: .bold))
                Text("\(selection.channelLabel) · \(selection.program.timeRange)")
                    .font(.title3).foregroundStyle(.secondary)
                if let subtitle = selection.program.subtitle, !subtitle.isEmpty {
                    Text(subtitle).font(.title2)
                }
                Text(selection.program.description ?? "No program description available.")
                    .font(.title3)
                if selection.program.plex?.ratingKey != nil {
                    Label("Available in Plex", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.yellow)
                }
                Text("Playback handoff is not yet enabled in this tvOS preview.")
                    .font(.footnote).foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(70)
        }
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
