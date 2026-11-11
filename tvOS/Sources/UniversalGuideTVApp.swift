import SwiftUI

@main
struct UniversalGuideTVApp: App {
    var body: some Scene {
        WindowGroup {
            TVGuideScreen()
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

struct ProgramDetailView: View {
    let selection: ProgramSelection
    @AppStorage("guide.serverURL") private var serverURL = ""
    @State private var playback: PlexPlaybackSession?
    @State private var isStarting = false
    @State private var playbackError: String?

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
                if let ratingKey = selection.program.plex?.ratingKey,
                   let key = Int(ratingKey), key > 0 {
                    HStack(spacing: 20) {
                        Button("Watch from Beginning") {
                            startPlexPlayback(ratingKey: key, mode: "beginning")
                        }
                        if selection.program.isOnNow {
                            Button("Watch from Current Point") {
                                startPlexPlayback(ratingKey: key, mode: "join")
                            }
                        }
                    }
                    .disabled(isStarting)
                    if isStarting { ProgressView("Preparing Plex playback…") }
                } else {
                    Text("No playable Plex match is available for this program.")
                        .foregroundStyle(.secondary)
                }
                if let playbackError {
                    Text(playbackError).foregroundStyle(.red)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(70)
        }
        .fullScreenCover(item: $playback) { session in
            PlexPlaybackScreen(session: session)
        }
    }

    private func startPlexPlayback(ratingKey: Int, mode: String) {
        guard !isStarting else { return }
        isStarting = true
        playbackError = nil
        Task {
            defer { isStarting = false }
            do {
                playback = try await PlexPlaybackService.start(
                    serverURL: serverURL,
                    ratingKey: ratingKey,
                    mode: mode,
                    airingStart: selection.program.start,
                    airingEnd: selection.program.stop
                )
            } catch {
                playbackError = error.localizedDescription
            }
        }
    }
}

