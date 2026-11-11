import SwiftUI
import AVKit

// Native tvOS counterpart to the iPhone's Unraid web guide. tvOS cannot host
// WKWebView; its focused controls and shared backend are implemented natively.
private enum TVPalette {
    static let background = Color(red: 0.038, green: 0.052, blue: 0.102)
    static let panel = Color(red: 0.112, green: 0.128, blue: 0.214)
    static let cell = Color(red: 0.15, green: 0.17, blue: 0.27)
    static let purple = Color(red: 0.72, green: 0.53, blue: 1)
}

private struct TVFocusCard: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

struct TVGuideScreen: View {
    @AppStorage("guide.serverURL") private var serverURL = ""
    @StateObject private var store = GuideStore()
    @State private var filter = "All"
    @State private var searchText = ""
    @State private var favoriteOnly = false
    @State private var showSettings = false
    @State private var showSearch = false
    @State private var showFilters = false
    @State private var selectedChannel = 0
    @State private var selectedProgram = 0
    @State private var selectedAction: TVWatchAction?
    @State private var mediaSession: TVMediaSession?
    @State private var playbackError: String?
    @State private var loadingPlayback = false
    @FocusState private var focus: String?
    @State private var now = Date()
    #if os(iOS)
    private let minutePixels: CGFloat = 3.5
    private let stationWidth: CGFloat = 115
    private let rowHeight: CGFloat = 54
    #else
    private let minutePixels: CGFloat = 6
    private let stationWidth: CGFloat = 205
    private let rowHeight: CGFloat = 88
    #endif
    private var anchor: Date {
        let minute = Calendar.current.component(.minute, from: now)
        return Calendar.current.date(byAdding: .minute, value: -(minute % 30),
                                     to: Calendar.current.date(bySetting: .second, value: 0, of: now) ?? now) ?? now
    }
    private var channels: [GuideChannel] {
        store.channels.filter { ch in
            (!favoriteOnly || ch.favorite == true) &&
            (filter == "All" || filter == "In Plex" && ch.programs.contains(where: {$0.plex != nil}) ||
             filter == "Tablo" && ch.source == "tablo" ||
             filter == "Philo" && ch.source == "philo") &&
            (searchText.isEmpty ||
             ch.label.localizedCaseInsensitiveContains(searchText) ||
             ch.programs.contains(where: {$0.title.localizedCaseInsensitiveContains(searchText)}))
        }
    }
    private var selected: (GuideChannel, GuideProgram)? {
        guard channels.indices.contains(selectedChannel) else { return nil }
        let ch = channels[selectedChannel]
        guard ch.programs.indices.contains(selectedProgram) else { return nil }
        return (ch, ch.programs[selectedProgram])
    }
    private var durationMinutes: Double { 480 }

    var body: some View {
        ZStack {
            TVPalette.background.ignoresSafeArea()
            HStack(spacing: 20) {
                sidebarRail
                    .frame(width: railWidth)
                VStack(spacing: 13) {
                    header
                    if let error = store.error, store.channels.isEmpty {
                        VStack(spacing: 20) {
                            Label(error, systemImage: "wifi.exclamationmark")
                                .font(.title3)
                            Button("Settings") { showSettings = true }
                        }.frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if store.isLoading && store.channels.isEmpty {
                        ProgressView("Loading Universal Guide…")
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        timeline
                        detailBar
                    }
                }
            }
            .padding(.horizontal, isCompact ? 8 : 45)
            .padding(.vertical, isCompact ? 5 : 30)
            if loadingPlayback {
                Color.black.opacity(0.55).ignoresSafeArea()
                ProgressView("Preparing video…")
                    .font(.title2)
            }
        }
        .tint(TVPalette.purple)
        .preferredColorScheme(.dark)
        .fullScreenCover(item: $mediaSession) { session in
            TVMediaPlayerScreen(session: session)
        }
        .sheet(isPresented: $showSettings, onDismiss: {
            Task { await refreshGuide() }
        }) { GuideSettingsView(serverURL: $serverURL) }
        .sheet(isPresented: $showSearch) {
            NavigationStack {
                Form {
                    TextField("Search channels or programs", text: $searchText)
                    Button("Clear Search") { searchText = "" }
                    Button("Return to Guide") { showSearch = false }
                }
                .navigationTitle("Search")
            }
        }
        .confirmationDialog("Filter Guide", isPresented: $showFilters) {
            ForEach(["All", "In Plex", "Tablo", "Philo"], id: \.self) { name in
                Button(name) {
                    filter = name
                    resetSelection()
                }
            }
            Button(favoriteOnly ? "Show All Channels" : "Favorites Only") {
                favoriteOnly.toggle()
                resetSelection()
            }
        }
        .alert("Playback unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
        .task { await refreshGuide() }
        .onChange(of: store.channels.count) { _, _ in resetSelection() }
        .onChange(of: focus) { _, next in
            if let next, next.hasPrefix("program-") {
                let parts = next.split(separator: "-")
                if parts.count == 3,
                   let ch = Int(parts[1]), let pg = Int(parts[2]),
                   channels.indices.contains(ch), channels[ch].programs.indices.contains(pg) {
                    choose(ch, pg)
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            if let raw = event.object as? String { movePhoneDirection(raw) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            activateFocused()
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadBack)) { _ in
            #if os(iOS)
            focus = "program-\(selectedChannel)-\(selectedProgram)"
            #endif
        }
    }

    private var sidebarRail: some View {
        VStack(spacing: isCompact ? 5 : 16) {
            railButton("magnifyingglass", name: "Search", key: "rail-search") { showSearch = true }
            railButton("line.3.horizontal.decrease", name: "Filter", key: "rail-filter") { showFilters = true }
            railButton("gearshape", name: "Settings", key: "rail-settings") { showSettings = true }
            Divider().overlay(TVPalette.purple)
            railButton("clock.arrow.circlepath", name: "Today", key: "rail-today") { goToday() }
            Spacer()
            Text("UNIVERSAL")
                .font(.system(size: 10, weight: .heavy))
                .foregroundStyle(TVPalette.purple)
        }
        .padding(10)
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 20))
    }

    private func railButton(_ icon: String, name: String, key: String,
                            action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: icon).font(.system(size: isCompact ? 16 : 27, weight: .medium))
                Text(name).font(.system(size: isCompact ? 9 : 13, weight: .semibold))
            }
            .foregroundStyle(focus == key ? .white : Color(red: 0.83, green: 0.78, blue: 0.94))
            .frame(width: isCompact ? 56 : 80, height: isCompact ? 48 : 72)
            .background(focus == key ? TVPalette.purple.opacity(0.38) : Color.clear,
                        in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13)
                .stroke(focus == key ? TVPalette.purple : .clear, lineWidth: 3))
        }
        .buttonStyle(TVFocusCard())
        .focused($focus, equals: key)
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Universal Guide")
                    .font(.system(size: isCompact ? 19 : 32, weight: .heavy))
                Text("Live TV  ·  Plex  ·  Tablo  ·  Philo")
                    .font(.system(size: isCompact ? 10 : 15))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(now.formatted(date: .abbreviated, time: .shortened))
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Color(red: 0.83, green: 0.77, blue: 0.95))
            if filter != "All" || favoriteOnly {
                Text([filter != "All" ? filter : "", favoriteOnly ? "Favorites" : ""]
                    .filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.caption.bold()).padding(8)
                .background(TVPalette.purple.opacity(0.26), in: Capsule())
            }
        }
        .frame(height: isCompact ? 36 : 65)
    }

    private var timeline: some View {
        ScrollViewReader { vertical in
            ScrollView([.vertical, .horizontal]) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 0) {
                        Text("CHANNEL")
                            .font(.caption.bold()).foregroundStyle(.secondary)
                            .frame(width: stationWidth, alignment: .leading)
                            .padding(.leading, 13)
                        ForEach(0..<16, id: \.self) { slot in
                            Text(anchor.addingTimeInterval(Double(slot * 1800))
                                .formatted(date: .omitted, time: .shortened))
                                .font(.caption.bold())
                                .foregroundStyle(Color(red: 0.83, green: 0.77, blue: 0.95))
                                .frame(width: minutePixels * 30, alignment: .leading)
                        }
                    }
                    .frame(height: 38)
                    .background(Color(red: 0.105, green: 0.11, blue: 0.19))
                    ForEach(Array(channels.enumerated()), id: \.element.id) { channelIndex, channel in
                        HStack(spacing: 0) {
                            VStack(alignment: .leading, spacing: 2) {
                                if let net = channel.network, !net.isEmpty {
                                    Text(net.uppercased())
                                        .font(.caption2.bold())
                                        .foregroundStyle(TVPalette.purple)
                                }
                                Text(channel.number ?? channel.label)
                                    .font(.system(size: 18, weight: .bold))
                                Text(channel.label)
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            .padding(.leading, 12)
                            .frame(width: stationWidth, height: rowHeight, alignment: .leading)
                            .background(Color(red: 0.135, green: 0.145, blue: 0.225))
                            timelineRow(channel, index: channelIndex)
                        }
                        .frame(height: rowHeight)
                        .background(channelIndex.isMultiple(of: 2) ?
                                    TVPalette.background : TVPalette.panel.opacity(0.6))
                        .id("row-\(channelIndex)")
                    }
                }
                .frame(width: stationWidth + CGFloat(durationMinutes) * minutePixels)
            }
            .onChange(of: selectedChannel) { _, value in
                withAnimation(.easeOut(duration: 0.1)) { vertical.scrollTo("row-\(value)", anchor: .center) }
            }
        }
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13)
            .stroke(Color(red: 0.28, green: 0.26, blue: 0.4), lineWidth: 1))
        .frame(maxHeight: .infinity)
    }

    private func timelineRow(_ channel: GuideChannel, index: Int) -> some View {
        ZStack(alignment: .leading) {
            HStack(spacing: 0) {
                ForEach(0..<16, id: \.self) { _ in
                    Rectangle().fill(.white.opacity(0.075))
                        .frame(width: 1)
                        .frame(width: minutePixels * 30, alignment: .leading)
                }
            }
            ForEach(Array(channel.programs.enumerated()), id: \.element.id) { pi, program in
                let start = max(0, (program.startDate ?? anchor).timeIntervalSince(anchor) / 60)
                let end = min(durationMinutes, (program.endDate ?? anchor).timeIntervalSince(anchor) / 60)
                if end > start {
                    Button {
                        choose(index, pi)
                        if program.plex != nil { focus = "watch-primary" }
                        else if channel.source == "tablo" && program.isOnNow {
                            focus = "watch-live"
                        }
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(program.title)
                                .font(.system(size: 16, weight: .bold))
                                .lineLimit(1)
                                .foregroundStyle(.white)
                            if program.plex != nil {
                                Label("IN PLEX", systemImage: "checkmark.circle.fill")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundStyle(Color(red: 1, green: 0.85, blue: 0.51))
                            } else if let subtitle = program.subtitle {
                                Text(subtitle).font(.caption2).foregroundStyle(.white.opacity(0.75))
                                    .lineLimit(1)
                            }
                        }
                        .frame(width: max(56, CGFloat(end-start)*minutePixels)-10,
                               height: rowHeight-11, alignment: .leading)
                        .padding(.horizontal, 8)
                        .background(program.plex != nil ?
                                    Color(red: 0.16, green: 0.30, blue: 0.31) :
                                    TVPalette.cell,
                                    in: RoundedRectangle(cornerRadius: 8))
                        .overlay(RoundedRectangle(cornerRadius: 8)
                            .stroke(focus == "program-\(index)-\(pi)" ? TVPalette.purple : .clear,
                                    lineWidth: 4))
                        .shadow(color: focus == "program-\(index)-\(pi)" ?
                                TVPalette.purple.opacity(0.6) : .clear, radius: 10)
                    }
                    .buttonStyle(TVFocusCard())
                    .focused($focus, equals: "program-\(index)-\(pi)")
                    .offset(x: CGFloat(start)*minutePixels)
                }
            }
        }
        .frame(width: CGFloat(durationMinutes)*minutePixels, height: rowHeight, alignment: .leading)
        .clipped()
    }

    private var detailBar: some View {
        HStack(spacing: 25) {
            RoundedRectangle(cornerRadius: 12)
                .fill(LinearGradient(colors: [TVPalette.purple.opacity(0.5), TVPalette.panel],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                .overlay(Image(systemName: "tv").font(.system(size: 48)).foregroundStyle(.white.opacity(0.5)))
                .frame(width: isCompact ? 0 : 180, height: isCompact ? 0 : 132)
                .opacity(isCompact ? 0 : 1)
            if let (_, program) = selected {
                VStack(alignment: .leading, spacing: 6) {
                    Text(program.title).font(.system(size: isCompact ? 15 : 25, weight: .bold)).lineLimit(1)
                    Text([program.subtitle, program.timeRange].compactMap{$0}.joined(separator: " · "))
                        .font(.system(size: 14)).foregroundStyle(.secondary).lineLimit(1)
                    Text(program.description ?? "Select a program to see viewing options.")
                        .font(.system(size: isCompact ? 10 : 15)).foregroundStyle(Color(red: 0.83, green: 0.82, blue: 0.92))
                        .lineLimit(2)
                    Spacer(minLength: 0)
                    if program.plex != nil {
                        Text("✓ In Plex").font(.caption.bold())
                            .foregroundStyle(Color(red: 1, green: 0.8, blue: 0.44))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(spacing: 12) {
                    if program.plex != nil {
                        watchButton("Watch on Plex", key: "watch-primary") {
                            selectedAction = .plex
                        }
                    }
                    if selected?.0.source == "tablo" && program.isOnNow {
                        watchButton("Watch Live", key: "watch-live") { playTablo() }
                    } else if selected?.0.source == "philo" {
                        Text("Philo playback opens in the Philo app")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .frame(width: isCompact ? 120 : 260)
            } else {
                Text("Select a program in the guide")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(isCompact ? 7 : 18)
        .frame(height: isCompact ? 102 : 170)
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 17))
        .confirmationDialog("Watch on Plex", isPresented: Binding(get: {
            selectedAction == .plex
        }, set: { if !$0 { selectedAction = nil } })) {
            Button("Watch from Beginning") { playPlex("beginning") }
            if selected?.1.isOnNow == true {
                Button("Watch from Current Point") { playPlex("join") }
            }
            Button("Cancel", role: .cancel) { selectedAction = nil }
        }
    }

    private func watchButton(_ label: String, key: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label).font(.system(size: isCompact ? 11 : 17, weight: .bold))
                .frame(maxWidth: .infinity).padding(.vertical, isCompact ? 7 : 13)
                .foregroundStyle(.white)
                .background(focus == key ? TVPalette.purple.opacity(0.55) :
                            Color(red: 0.32, green: 0.29, blue: 0.45),
                            in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11)
                    .stroke(focus == key ? TVPalette.purple : .clear, lineWidth: 3))
        }
        .buttonStyle(TVFocusCard())
        .focused($focus, equals: key)
    }

    private func choose(_ channel: Int, _ program: Int) {
        selectedChannel = channel
        selectedProgram = program
    }
    private func resetSelection() {
        selectedChannel = 0
        selectedProgram = channels.first?.programs.firstIndex(where: { $0.isOnNow }) ?? 0
        focus = "program-\(selectedChannel)-\(selectedProgram)"
    }
    private func goToday() {
        now = Date()
        resetSelection()
        Task { await refreshGuide() }
    }
    private func refreshGuide() async {
        await store.refresh(server: serverURL)
    }
    private var isCompact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private var railWidth: CGFloat { isCompact ? 70 : 100 }

    // On iOS the gamepad sends explicit commands to these same SwiftUI
    // controls. tvOS uses its native focus engine, never this second handler.
    private func movePhoneDirection(_ direction: String) {
        let rail = ["rail-search", "rail-filter", "rail-settings", "rail-today"]
        if let value = focus, let i = rail.firstIndex(of: value) {
            if direction == "down" { focus = rail[min(rail.count-1,i+1)] }
            if direction == "up" { focus = rail[max(0,i-1)] }
            if direction == "right" { focus = "program-\(selectedChannel)-\(selectedProgram)" }
            return
        }
        if focus == "watch-primary" || focus == "watch-live" {
            if direction == "up" { focus = "program-\(selectedChannel)-\(selectedProgram)" }
            if direction == "left" { focus = "watch-primary" }
            if direction == "right" || direction == "down" { focus = "watch-live" }
            return
        }
        if direction == "left" && selectedProgram == 0 { focus = "rail-search"; return }
        guard !channels.isEmpty else { return }
        if direction == "up" { selectedChannel = max(0,selectedChannel-1) }
        if direction == "down" { selectedChannel = min(channels.count-1, selectedChannel+1) }
        let programs = channels[selectedChannel].programs
        guard !programs.isEmpty else { return }
        if direction == "left" { selectedProgram = max(0, selectedProgram-1) }
        if direction == "right" { selectedProgram = min(programs.count-1, selectedProgram+1) }
        selectedProgram = min(selectedProgram, programs.count-1)
        focus = "program-\(selectedChannel)-\(selectedProgram)"
    }
    private func activateFocused() {
        switch focus {
        case "rail-search": showSearch = true
        case "rail-filter": showFilters = true
        case "rail-settings": showSettings = true
        case "rail-today": goToday()
        case "watch-primary": selectedAction = .plex
        case "watch-live": playTablo()
        default:
            if selected?.1.plex != nil { focus = "watch-primary" }
            else if selected?.0.source == "tablo" && selected?.1.isOnNow == true {
                focus = "watch-live"
            }
        }
    }
    private func playPlex(_ mode: String) {
        guard let (channel,program) = selected,
              let value = program.plex?.ratingKey, let key = Int(value) else { return }
        selectedAction = nil
        loadingPlayback = true
        Task {
            defer { loadingPlayback = false }
            do {
                let session = try await PlexPlaybackService.start(serverURL: serverURL,
                    ratingKey: key, mode: mode, airingStart: program.start,
                    airingEnd: program.stop)
                mediaSession = .plex(session)
            } catch { playbackError = error.localizedDescription }
            _ = channel
        }
    }
    private func playTablo() {
        guard let (channel,program) = selected else { return }
        loadingPlayback = true
        Task {
            defer { loadingPlayback = false }
            do {
                mediaSession = try await TVTabloService.start(serverURL: serverURL,
                                                           channel: channel, title: program.title)
            } catch { playbackError = error.localizedDescription }
        }
    }
}

private enum TVWatchAction { case plex }

extension Notification.Name {
    static let ugGamepadDirection = Notification.Name("UGNativeGuideDirection")
    static let ugGamepadSelect = Notification.Name("UGNativeGuideSelect")
    static let ugGamepadBack = Notification.Name("UGNativeGuideBack")
    static let ugGamepadPlayPause = Notification.Name("UGNativeGuidePlayPause")
}
