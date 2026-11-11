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
    @State private var selectedArtworkURL: URL?
    @State private var artworkTask: Task<Void, Never>?
    @State private var artworkCache: [String: URL] = [:]
    @State private var noArtwork: Set<String> = []
    @FocusState private var focus: String?
    // iOS gamepad input is not part of tvOS's native FocusState engine.
    // Keep a separately-rendered selection so the purple ring always follows
    // the Backbone even when UIKit declines to focus SwiftUI buttons.
    @State private var phoneSelection = ""
    @State private var tvSelection = ""
    private var selectedFocus: String? { isCompact ? phoneSelection : tvSelection }
    @State private var now = Date()
    #if os(iOS)
    private let minutePixels: CGFloat = 3.5
    private let stationWidth: CGFloat = 115
    private let rowHeight: CGFloat = 64
    #else
    private let minutePixels: CGFloat = 7
    private let stationWidth: CGFloat = 232
    private let rowHeight: CGFloat = 100
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
            HStack(spacing: isCompact ? 12 : 14) {
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
            .padding(.horizontal, isCompact ? 8 : 20)
            .padding(.vertical, isCompact ? 5 : 14)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            // Only one tvOS focus target receives remote presses.
            // It consumes direction/OK before the spatial focus engine can
            // advance again, fixing two jumps for a single remote press.
            if !isCompact && selectedAction == nil && !showFilters &&
                !showSearch && !showSettings && mediaSession == nil {
                UniversalTVGuideRemoteReceiver(
                    onMove: moveTVFocus,
                    onSelect: activateFocused,
                    onBack: focusSelectedTVProgram
                )
                .ignoresSafeArea()
            }
            if loadingPlayback {
                Color.black.opacity(0.55).ignoresSafeArea()
                ProgressView("Preparing video…")
                    .font(.title2)
            }

        }
        .tint(TVPalette.purple)
        .preferredColorScheme(.dark)
        .modifier(UniversalGuideFullscreenLayout())
        // A true modal presentation isolates the Plex choice focus from
        // the guide. The former ZStack overlay allowed the guide to keep
        // processing remote directions and lose the chosen program.
        .fullScreenCover(isPresented: Binding(get: {
            selectedAction == .plex
        }, set: { shown in
            if !shown { dismissPlexChoice() }
        })) {
            UniversalPlexChoiceScreen(title: selected?.1.title ?? "Plex",
                canJoin: selected?.1.isOnNow == true,
                onBeginning: { playPlex("beginning") },
                onNow: { playPlex("join") },
                onCancel: { dismissPlexChoice() })
        }
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
        .fullScreenCover(isPresented: $showFilters) {
            TVGuideFilterPanel(currentFilter: filter, favoritesOnly: favoriteOnly,
                onFilter: { name in
                    filter = name
                    resetSelection()
                    showFilters = false
                },
                onFavorites: {
                    favoriteOnly.toggle()
                    resetSelection()
                    showFilters = false
                },
                onClose: { showFilters = false })
        }
        .alert("Playback or app handoff unavailable", isPresented: Binding(get: { playbackError != nil }, set: { if !$0 { playbackError = nil } })) {
            Button("OK") { playbackError = nil }
        } message: { Text(playbackError ?? "") }
        .task { await refreshGuide(); refreshSelectedArtwork() }
        .onChange(of: selected?.1.id) { _, _ in refreshSelectedArtwork() }
        .onChange(of: serverURL) { _, _ in
            artworkCache.removeAll()
            noArtwork.removeAll()
            refreshSelectedArtwork()
        }
        .onChange(of: store.channels.count) { _, _ in resetSelection() }
        .onChange(of: focus) { _, next in
            if isCompact, let next, next.hasPrefix("program-") {
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
            if mediaSession == nil && selectedAction == nil &&
               !showFilters && !showSettings && !showSearch,
               let raw = event.object as? String { movePhoneDirection(raw) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            if mediaSession == nil && selectedAction == nil &&
               !showFilters && !showSettings && !showSearch { activateFocused() }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadBack)) { _ in
            #if os(iOS)
            if selectedAction == .plex { dismissPlexChoice() }
            else if mediaSession == nil { selectPhoneProgram() }
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
            Text("UG")
                .font(.system(size: 15, weight: .heavy))
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
            .foregroundStyle(selectedFocus == key ? .white : Color(red: 0.83, green: 0.78, blue: 0.94))
            .frame(width: isCompact ? 56 : 80, height: isCompact ? 48 : 72)
            .background(selectedFocus == key ? TVPalette.purple.opacity(0.38) : Color.clear,
                        in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13)
                .stroke(selectedFocus == key ? TVPalette.purple : .clear, lineWidth: 3))
        }
        .buttonStyle(TVFocusCard())
        .focused($focus, equals: key)
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Universal Guide")
                    .font(.system(size: isCompact ? 19 : 35, weight: .heavy))
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
                                    .font(.system(size: isCompact ? 18 : 24, weight: .bold))
                                if channel.number != nil && channel.label != channel.number {
                                    Text(channel.label)
                                        .font(.system(size: isCompact ? 11 : 16))
                                        .foregroundStyle(.secondary).lineLimit(1)
                                }
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
                if channels.indices.contains(value) {
                    // Avoid two competing scroll animations for one remote press.
                    vertical.scrollTo("row-\(value)", anchor: .center)
                }
            }
            .onChange(of: selectedProgram) { _, value in
                // A single immediate scroll per selection is much quicker
                // than simultaneous animated focus and position changes.
                vertical.scrollTo("program-\(selectedChannel)-\(value)", anchor: .center)
            }
        }
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13)
            .stroke(Color(red: 0.28, green: 0.26, blue: 0.4), lineWidth: 1))
        .frame(maxHeight: .infinity)
    }

    private func timelineRow(_ channel: GuideChannel, index: Int) -> some View {
        // Real HStack geometry instead of ZStack .offset: ScrollViewReader
        // can now scroll to the tile's actual horizontal position.
        HStack(spacing: 0) {
            ForEach(Array(channel.programs.enumerated()), id: \.element.id) { pi, program in
                let start = max(0, (program.startDate ?? anchor).timeIntervalSince(anchor) / 60)
                let end = min(durationMinutes, (program.endDate ?? anchor).timeIntervalSince(anchor) / 60)
                let previousEnd = pi > 0 ?
                    max(0, min(durationMinutes,
                        (channel.programs[pi-1].endDate ?? anchor).timeIntervalSince(anchor) / 60)) : 0
                let cellStart = max(start, previousEnd)
                if end > cellStart {
                    Color.clear.frame(width: CGFloat(max(0,start-previousEnd))*minutePixels)
                    Button {
                        choose(index, pi)
                        activateGuideProgram(channel, program)
                    } label: {
                        VStack(alignment: .leading, spacing: isCompact ? 5 : 7) {
                            Text(program.title)
                                .font(.system(size: isCompact ? 13 : 20, weight: .bold))
                                .lineLimit(1)
                                .foregroundStyle(.white)
                            if program.plex != nil {
                                Label("IN PLEX", systemImage: "checkmark.circle.fill")
                                    .font(.system(size: isCompact ? 9 : 11, weight: .bold))
                                    .foregroundStyle(Color(red: 1, green: 0.85, blue: 0.51))
                                    .lineLimit(1)
                            } else if let subtitle = program.subtitle {
                                Text(subtitle)
                                    .font(.system(size: isCompact ? 10 : 14))
                                    .foregroundStyle(.white.opacity(0.77))
                                    .lineLimit(1)
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .padding(.horizontal, isCompact ? 9 : 14)
                        .background(program.plex != nil ?
                                    Color(red: 0.16, green: 0.30, blue: 0.31) :
                                    TVPalette.cell,
                                    in: RoundedRectangle(cornerRadius: 9))
                        .overlay(RoundedRectangle(cornerRadius: 9)
                            .stroke(selectedFocus == "program-\(index)-\(pi)" ?
                                    TVPalette.purple : .clear, lineWidth: 3))
                        .shadow(color: selectedFocus == "program-\(index)-\(pi)" ?
                                TVPalette.purple.opacity(0.55) : .clear, radius: 7)
                    }
                    .frame(width: max(2,CGFloat(end-cellStart)*minutePixels-7),
                           height: rowHeight-(isCompact ? 10 : 14))
                    .padding(.trailing, 7)
                    .buttonStyle(TVFocusCard())
                    .focused($focus, equals: "program-\(index)-\(pi)")
                    .id("program-\(index)-\(pi)")
                }
            }
            Spacer(minLength: 0)
        }
        .frame(width: CGFloat(durationMinutes)*minutePixels, height: rowHeight,
               alignment: .leading)
        .background(alignment: .leading) {
            HStack(spacing: 0) {
                ForEach(0..<16, id: \.self) { _ in
                    Rectangle().fill(.white.opacity(0.075))
                        .frame(width: 1)
                        .frame(width: minutePixels*30, alignment: .leading)
                }
            }
        }
        .clipped()
    }

    private var detailBar: some View {
        HStack(spacing: isCompact ? 7 : 22) {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(Color(red: 0.08, green: 0.10, blue: 0.17))
                if let selectedArtworkURL {
                    AsyncImage(url: selectedArtworkURL) { phase in
                        if let image = phase.image {
                            image.resizable().scaledToFit()
                        } else {
                            Image(systemName: "tv")
                                .font(.system(size: isCompact ? 22 : 45))
                                .foregroundStyle(.white.opacity(0.28))
                        }
                    }
                } else {
                    Image(systemName: "tv")
                        .font(.system(size: isCompact ? 22 : 45))
                        .foregroundStyle(.white.opacity(0.28))
                }
            }
            .frame(width: isCompact ? 64 : 150, height: isCompact ? 90 : 207)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            if let (_, program) = selected {
                VStack(alignment: .leading, spacing: 6) {
                    Text(program.title).font(.system(size: isCompact ? 15 : 25, weight: .bold)).lineLimit(1)
                    Text([program.subtitle, program.timeRange].compactMap{$0}.joined(separator: " · "))
                        .font(.system(size: 14)).foregroundStyle(.secondary).lineLimit(1)
                    Text(program.description ?? "Select a program to see viewing options.")
                        .font(.system(size: isCompact ? 10 : 15)).foregroundStyle(Color(red: 0.83, green: 0.82, blue: 0.92))
                        .lineLimit(isCompact ? 2 : 4)
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
                            openPlexChoice()
                        }
                    }
                    if selected?.0.source == "tablo" && program.isOnNow {
                        watchButton("Watch Live", key: "watch-live") { playTablo() }
                    }
                    if selected?.0.source == "philo" {
                        watchButton("Open in Philo", key: "watch-philo") {
                            if let (ch,entry) = selected { openPhilo(ch,entry) }
                        }
                    }
                }
                .frame(width: isCompact ? 120 : 260)
            } else {
                Text("Select a program in the guide")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(isCompact ? 7 : 18)
        .frame(height: isCompact ? 112 : 248)
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 17))

    }

    private func watchButton(_ label: String, key: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label).font(.system(size: isCompact ? 11 : 17, weight: .bold))
                .frame(maxWidth: .infinity).padding(.vertical, isCompact ? 7 : 13)
                .foregroundStyle(.white)
                .background(selectedFocus == key ? TVPalette.purple.opacity(0.55) :
                            Color(red: 0.32, green: 0.29, blue: 0.45),
                            in: RoundedRectangle(cornerRadius: 11))
                .overlay(RoundedRectangle(cornerRadius: 11)
                    .stroke(selectedFocus == key ? TVPalette.purple : .clear, lineWidth: 3))
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
        let target = "program-\(selectedChannel)-\(selectedProgram)"
        if isCompact { phoneSelection = target }
        else { tvSelection = target }
    }
    private func goToday() {
        now = Date()
        resetSelection()
        Task { await refreshGuide() }
    }
    private func refreshGuide() async {
        await store.refresh(server: serverURL)
    }
    private func refreshSelectedArtwork() {
        artworkTask?.cancel()
        selectedArtworkURL = nil
        guard let (_, program) = selected else { return }
        let kind = program.programKind == "movie" ? "movie" : "series"
        let key = "\(kind)|\(program.title)|\(kind == "movie" ? program.year ?? 0 : 0)"
        if let cached = artworkCache[key] {
            selectedArtworkURL = cached
            return
        }
        if noArtwork.contains(key) { return }
        let server = serverURL
        let ratingKey = program.plex?.ratingKey
        artworkTask = Task { @MainActor in
            guard let base = URL(string: server), base.host != nil else { return }
            var components = URLComponents(url: base, resolvingAgainstBaseURL: false)
            components?.path = "/api/guide/artwork"
            var query = [URLQueryItem(name: "title", value: program.title)]
            if kind == "movie" {
                query.append(URLQueryItem(name: "kind", value: "movie"))
                if let year = program.year {
                    query.append(URLQueryItem(name: "year", value: String(year)))
                }
            }
            components?.queryItems = query
            var result: URL?
            if let url = components?.url {
                do {
                    var request = URLRequest(url: url)
                    request.timeoutInterval = 6
                    let (data, response) = try await URLSession.shared.data(for: request)
                    if !Task.isCancelled,
                       let http = response as? HTTPURLResponse, http.statusCode == 200,
                       let art = try? JSONDecoder().decode(TVArtworkReply.self, from: data),
                       let path = art.imageURL,
                       path.hasPrefix("/api/guide/artwork/cached/") {
                        result = URL(string: path, relativeTo: base)?.absoluteURL
                    }
                } catch { /* Plex fallback below */ }
            }
            if result == nil, let ratingKey, let rating = Int(ratingKey), rating > 0 {
                result = URL(string: "/api/guide/art/\(rating)", relativeTo: base)?.absoluteURL
            }
            guard !Task.isCancelled else { return }
            if let result {
                artworkCache[key] = result
                if selected?.1.id == program.id { selectedArtworkURL = result }
            } else {
                noArtwork.insert(key)
            }
        }
    }
    private var isCompact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private var railWidth: CGFloat { isCompact ? 70 : 94 }

    // iPhone and Apple TV render the same native program tiles, sidebar and
    // actions. Only their input adapters differ. Apple TV uses system focus;
    // iPhone maps Backbone presses to a visible, explicit target.
    // Exactly one D-pad handler on tvOS. The stock spatial focus engine
    // chooses unpredictable tiles when adjacent rows have unequal durations.
    // We choose explicit neighboring programs and keep their airing time.
    private func moveTVFocus(_ direction: String) {
        guard !isCompact && !showFilters && !showSearch && !showSettings,
              !loadingPlayback && mediaSession == nil else { return }
        // The choice dialog and native TV filters own their own tvSelection.
        if selectedAction == .plex { return }
        let keys = ["rail-search", "rail-filter", "rail-settings", "rail-today"]
        if let index = keys.firstIndex(of: tvSelection) {
            switch direction {
            case "up": self.tvSelection = keys[max(0,index-1)]
            case "down": self.tvSelection = keys[min(keys.count-1,index+1)]
            case "right": focusSelectedTVProgram()
            default: break
            }
            return
        }
        if ["watch-primary","watch-live","watch-philo"].contains(tvSelection) {
            switch direction {
            case "up", "left":
                if tvSelection == "watch-primary" || selected?.1.plex == nil {
                    focusSelectedTVProgram()
                } else {
                    self.tvSelection = "watch-primary"
                }
            case "right", "down":
                if tvSelection == "watch-primary", selected?.0.source == "philo" {
                    self.tvSelection = "watch-philo"
                } else if tvSelection == "watch-primary", selected?.0.source == "tablo" {
                    self.tvSelection = "watch-live"
                }
            default: break
            }
            return
        }
        guard channels.indices.contains(selectedChannel) else { return }
        let programs = channels[selectedChannel].programs
        guard programs.indices.contains(selectedProgram) else { return }
        let visible = programs.indices.filter {
            (programs[$0].endDate ?? .distantPast) > anchor &&
            (programs[$0].startDate ?? .distantFuture) <
                anchor.addingTimeInterval(durationMinutes*60)
        }
        switch direction {
        case "left":
            guard let index = visible.firstIndex(of: selectedProgram), index > 0 else {
                tvSelection = "rail-search"
                return
            }
            selectedProgram = visible[index-1]
        case "right":
            guard let index = visible.firstIndex(of: selectedProgram),
                  index+1 < visible.count else { return }
            selectedProgram = visible[index+1]
        case "up", "down":
            let nextRow = selectedChannel + (direction == "down" ? 1 : -1)
            if !channels.indices.contains(nextRow) {
                if direction == "down" {
                    if selected?.1.plex != nil { tvSelection = "watch-primary" }
                    else if selected?.0.source == "tablo" { tvSelection = "watch-live" }
                    else if selected?.0.source == "philo" { tvSelection = "watch-philo" }
                }
                return
            }
            let current = programs[selectedProgram]
            let targetTime: Date = {
                if let start=current.startDate, let end=current.endDate {
                    return start.addingTimeInterval(end.timeIntervalSince(start)/2)
                }
                return now
            }()
            let candidates = channels[nextRow].programs
            guard !candidates.isEmpty else { return }
            let best = candidates.indices.min {
                func distance(_ p: GuideProgram) -> Double {
                    guard let start=p.startDate, let end=p.endDate else {
                        return Double.greatestFiniteMagnitude
                    }
                    if start <= targetTime && targetTime < end { return 0 }
                    return min(abs(start.timeIntervalSince(targetTime)),
                               abs(end.timeIntervalSince(targetTime)))
                }
                return distance(candidates[$0]) < distance(candidates[$1])
            } ?? 0
            selectedChannel = nextRow
            selectedProgram = best
        default: return
        }
        focusSelectedTVProgram()
    }
    private func focusSelectedTVProgram() {
        guard channels.indices.contains(selectedChannel),
              channels[selectedChannel].programs.indices.contains(selectedProgram) else { return }
        tvSelection = "program-\(selectedChannel)-\(selectedProgram)"
    }

    private func selectPhoneProgram() {
        guard channels.indices.contains(selectedChannel) else {return}
        let entries = channels[selectedChannel].programs
        guard !entries.isEmpty else { return }
        selectedProgram = min(selectedProgram, entries.count-1)
        phoneSelection = "program-\(selectedChannel)-\(selectedProgram)"
    }
    // Use the same visible regions as the Apple TV layout, reachable entirely
    // with a four-way D-pad. No shoulder or extra face buttons are necessary.
    private func movePhoneDirection(_ direction: String) {
        if selectedAction == .plex {
            let choices = selected?.1.isOnNow == true ?
                ["plex-beginning", "plex-now", "plex-cancel"] :
                ["plex-beginning", "plex-cancel"]
            let index = choices.firstIndex(of: phoneSelection) ?? 0
            let delta = (direction == "up" || direction == "left") ? -1 : 1
            phoneSelection = choices[max(0,min(choices.count-1,index+delta))]
            return
        }
        let rail = ["rail-search", "rail-filter", "rail-settings", "rail-today"]
        if let index = rail.firstIndex(of: phoneSelection) {
            switch direction {
            case "up": phoneSelection = rail[max(0,index-1)]
            case "down": phoneSelection = rail[min(rail.count-1,index+1)]
            case "right": selectPhoneProgram()
            default: break
            }
            return
        }
        let hasPlex = selected?.1.plex != nil
        let hasTablo = selected?.0.source == "tablo" && selected?.1.isOnNow == true
        let hasPhilo = selected?.0.source == "philo"
        if phoneSelection == "watch-primary" || phoneSelection == "watch-live" ||
            phoneSelection == "watch-philo" {
            switch direction {
            case "up": selectPhoneProgram()
            case "left":
                if phoneSelection != "watch-primary" && hasPlex {
                    phoneSelection = "watch-primary"
                } else {
                    selectPhoneProgram()
                }
            case "right", "down":
                if phoneSelection == "watch-primary" && hasTablo {
                    phoneSelection = "watch-live"
                } else if phoneSelection == "watch-primary" && hasPhilo {
                    phoneSelection = "watch-philo"
                }
            default: break
            }
            return
        }
        // At the first visible airing, Left leads to the sidebar.
        if direction == "left", channels.indices.contains(selectedChannel) {
            let entries = channels[selectedChannel].programs
            let firstVisible = entries.firstIndex(where: {
                ($0.endDate ?? .distantPast) > anchor
            }) ?? 0
            if selectedProgram <= firstVisible {
                phoneSelection = "rail-search"
                return
            }
        }
        // Down from the last channel moves to the bottom viewing actions.
        if direction == "down" && !channels.isEmpty &&
            selectedChannel >= channels.count-1 {
            if hasPlex { phoneSelection = "watch-primary"; return }
            if hasTablo { phoneSelection = "watch-live"; return }
            if hasPhilo { phoneSelection = "watch-philo"; return }
        }
        // In the guide, the playback choices are always also reachable
        // by pressing OK on a program with Plex or live Tablo.
        guard !channels.isEmpty else { return }
        if direction == "up" { selectedChannel = max(0,selectedChannel-1) }
        if direction == "down" { selectedChannel = min(channels.count-1,selectedChannel+1) }
        let entries = channels[selectedChannel].programs
        guard !entries.isEmpty else { selectedProgram = 0; return }
        if direction == "up" || direction == "down" {
            selectedProgram = entries.firstIndex(where: {$0.isOnNow}) ??
                entries.firstIndex(where: {($0.endDate ?? .distantPast) > anchor}) ?? 0
        } else if direction == "left" {
            selectedProgram = max(0,selectedProgram-1)
        } else if direction == "right" {
            selectedProgram = min(entries.count-1,selectedProgram+1)
        }
        selectPhoneProgram()
    }
    private func openPlexChoice() {
        selectedAction = .plex
        // Modal screen takes ownership of focus and controller commands.
    }
    private func dismissPlexChoice() {
        selectedAction = nil
        if isCompact { selectPhoneProgram() }
        else { focusSelectedTVProgram() }
    }
    private func activateFocused() {
        if selectedAction == .plex {
            switch phoneSelection {
            case "plex-now": playPlex("join")
            case "plex-cancel": dismissPlexChoice()
            default: playPlex("beginning")
            }
            return
        }
        switch isCompact ? phoneSelection : tvSelection {
        case "rail-search": showSearch = true
        case "rail-filter": showFilters = true
        case "rail-settings": showSettings = true
        case "rail-today": goToday()
        case "watch-primary":
            if selected?.1.plex != nil { openPlexChoice() }
        case "watch-live":
            if selected?.0.source == "tablo" && selected?.1.isOnNow == true {
                playTablo()
            }
        case "watch-philo":
            if let (channel,program) = selected { openPhilo(channel,program) }
        default:
            if let (channel,program) = selected {
                activateGuideProgram(channel,program)
            }
        }
    }
    private func activateGuideProgram(_ channel: GuideChannel, _ program: GuideProgram) {
        if program.plex != nil {
            // Never automatically launch Philo when Plex also matches.
            // OK moves to the bottom source choices, just like the web guide.
            if isCompact { phoneSelection = "watch-primary" }
            else { tvSelection = "watch-primary" }
        } else if channel.source == "philo" {
            openPhilo(channel, program)
        } else if channel.source == "tablo" && program.isOnNow {
            playTablo()
        }
    }

    private func openPhilo(_ channel: GuideChannel, _ program: GuideProgram) {
        let source = program.philoLinks?.channel?.url ?? channel.philoChannelURL
        guard let raw = source,
              let url = URL(string: raw),
              url.scheme == "https",
              url.host == "www.philo.com",
              url.path.hasPrefix("/player/channel/"),
              !url.lastPathComponent.isEmpty else {
            playbackError = "No verified Philo channel link is available for \(channel.label)."
            return
        }
        UIApplication.shared.open(url, options: [.universalLinksOnly: true]) { success in
            if !success {
                playbackError = "Philo could not open this channel from Universal Guide on this Apple TV. Open the Philo app directly for now."
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


/// A dedicated presentation isolates filter focus from the underlying EPG.
/// iPhone controller A/D-pad and Apple TV native focus use identical actions.
private struct TVGuideFilterPanel: View {
    let currentFilter: String
    let favoritesOnly: Bool
    let onFilter: (String) -> Void
    let onFavorites: () -> Void
    let onClose: () -> Void
    @FocusState private var focused: Int?
    @State private var phoneIndex = 0
    private let choices = ["All Channels", "In Plex", "Tablo", "Philo",
                           "Favorites", "Cancel"]
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)
    private var isCompact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private var selection: Int? { isCompact ? phoneIndex : focused }

    var body: some View {
        ZStack {
            Color.black.opacity(0.92).ignoresSafeArea()
            VStack(spacing: isCompact ? 8 : 16) {
                Text("Filter Guide").font(isCompact ? .title3.bold() : .largeTitle.bold())
                    .foregroundStyle(.white)
                    .padding(.bottom, 6)
                ForEach(Array(choices.enumerated()), id: \.offset) { index, name in
                    Button { activate(index) } label: {
                        HStack {
                            Text(name)
                            Spacer()
                            if name == currentFilter ||
                                name == "All Channels" && currentFilter == "All" ||
                                name == "Favorites" && favoritesOnly {
                                Image(systemName: "checkmark")
                            }
                        }
                        .padding(.horizontal, 16)
                        .frame(height: isCompact ? 35 : 58)
                        .foregroundStyle(.white)
                        .background(selection == index ? purple.opacity(0.40) :
                                    Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
                        .overlay(RoundedRectangle(cornerRadius: 12)
                            .stroke(selection == index ? purple : .clear, lineWidth: 3))
                    }
                    .buttonStyle(.plain)
                    .focused($focused, equals: index)
                }
            }
            .padding(24)
            .frame(width: isCompact ? 360 : 600)
            .background(Color(red: 0.11, green: 0.12, blue: 0.22),
                        in: RoundedRectangle(cornerRadius: 18))
        }
        .onAppear { focused = 0; phoneIndex = 0 }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            guard let direction = event.object as? String else {return}
            if direction == "down" || direction == "right" {
                phoneIndex = min(choices.count-1, phoneIndex+1)
            } else if direction == "up" || direction == "left" {
                phoneIndex = max(0, phoneIndex-1)
            }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            activate(phoneIndex)
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadBack)) { _ in
            #if os(iOS)
            onClose()
            #endif
        }
        #if os(tvOS)
        .onExitCommand(perform: onClose)
        #endif
    }

    private func activate(_ index: Int) {
        switch index {
        case 0: onFilter("All")
        case 1: onFilter("In Plex")
        case 2: onFilter("Tablo")
        case 3: onFilter("Philo")
        case 4: onFavorites()
        default: onClose()
        }
    }
}

private struct TVArtworkReply: Decodable {
    let imageURL: String?
    enum CodingKeys: String, CodingKey { case imageURL = "image_url" }
}

private struct UniversalGuideFullscreenLayout: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        #if os(tvOS)
        content.ignoresSafeArea(.container, edges: .all)
        #else
        content
        #endif
    }
}


private final class UniversalTVGuideRemoteView: UIView {
    var onMove: ((String) -> Void)?
    var onSelect: (() -> Void)?
    var onBack: (() -> Void)?
    override var canBecomeFocused: Bool { true }
    #if os(tvOS)
    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window != nil {
            DispatchQueue.main.async { [weak self] in
                self?.setNeedsFocusUpdate()
                self?.updateFocusIfNeeded()
            }
        }
    }
    override func shouldUpdateFocus(in context: UIFocusUpdateContext) -> Bool {
        // Prevent the UIKit spatial engine from moving onto the next tile
        // after our explicit one-step navigator has already handled the D-pad.
        if context.previouslyFocusedView === self,
           context.nextFocusedView !== self { return false }
        return super.shouldUpdateFocus(in: context)
    }
    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var handled = false
        for press in presses {
            switch press.type {
            case .upArrow: onMove?("up"); handled = true
            case .downArrow: onMove?("down"); handled = true
            case .leftArrow: onMove?("left"); handled = true
            case .rightArrow: onMove?("right"); handled = true
            case .select: onSelect?(); handled = true
            case .menu: onBack?(); handled = true
            default: break
            }
        }
        if !handled { super.pressesBegan(presses, with: event) }
    }
    #endif
}

private struct UniversalTVGuideRemoteReceiver: UIViewRepresentable {
    let onMove: (String) -> Void
    let onSelect: () -> Void
    let onBack: () -> Void
    func makeUIView(context: Context) -> UniversalTVGuideRemoteView {
        let view = UniversalTVGuideRemoteView()
        view.backgroundColor = .clear
        return view
    }
    func updateUIView(_ view: UniversalTVGuideRemoteView, context: Context) {
        view.onMove = onMove
        view.onSelect = onSelect
        view.onBack = onBack
    }
}


private struct UniversalPlexChoiceScreen: View {
    let title: String
    let canJoin: Bool
    let onBeginning: () -> Void
    let onNow: () -> Void
    let onCancel: () -> Void
    @FocusState private var nativeFocus: Int?
    @State private var phoneIndex = 0
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)
    private var isPhone: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private var selectedIndex: Int { isPhone ? phoneIndex : (nativeFocus ?? 0) }
    private var actions: [String] {
        canJoin ? ["Watch from Beginning", "Watch from Current Point", "Cancel"] :
                  ["Watch from Beginning", "Cancel"]
    }
    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 20) {
                Text("Watch on Plex").font(.system(size: isPhone ? 22 : 36, weight: .bold))
                Text(title).font(.headline).foregroundStyle(.secondary)
                ForEach(Array(actions.enumerated()), id: \.offset) { index, label in
                    Button { activate(index) } label: {
                        Text(label)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, isPhone ? 12 : 19)
                            .foregroundStyle(.white)
                            .background(selectedIndex == index ? purple.opacity(0.35) :
                                        Color.white.opacity(0.08),
                                        in: RoundedRectangle(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12)
                                .stroke(selectedIndex == index ? purple : .clear, lineWidth: 4))
                    }
                    .buttonStyle(.plain)
                    .focused($nativeFocus, equals: index)
                }
            }
            .padding(32)
            .frame(width: isPhone ? 330 : 620)
            .background(Color(red: 0.11, green: 0.12, blue: 0.22),
                        in: RoundedRectangle(cornerRadius: 20))
        }
        .onAppear { nativeFocus = 0; phoneIndex = 0 }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            guard let direction = event.object as? String else { return }
            if direction == "down" || direction == "right" {
                phoneIndex = min(actions.count-1, phoneIndex+1)
            } else if direction == "up" || direction == "left" {
                phoneIndex = max(0, phoneIndex-1)
            }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            activate(phoneIndex)
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadBack)) { _ in
            #if os(iOS)
            onCancel()
            #endif
        }
        .modifier(UniversalPlexChoiceBack(onCancel: onCancel))
    }
    private func activate(_ index: Int) {
        if index == 0 { onBeginning() }
        else if canJoin && index == 1 { onNow() }
        else { onCancel() }
    }
}
private struct UniversalPlexChoiceBack: ViewModifier {
    let onCancel: () -> Void
    @ViewBuilder func body(content: Content) -> some View {
        #if os(tvOS)
        content.onExitCommand(perform: onCancel)
        #else
        content
        #endif
    }
}
