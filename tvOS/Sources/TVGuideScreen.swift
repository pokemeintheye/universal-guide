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
    @State private var pendingPlexSession: PlexPlaybackSession?
    @State private var plexChoiceDismissed = true
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
    // A shared 16:9 design canvas scales the guide's vertical proportions
    // on iPhone landscape and Apple TV, including six visible program rows.
    #if os(iOS)
    @State private var viewportHeight: CGFloat = 390
    private let minutePixels: CGFloat = 3.5
    private let stationWidth: CGFloat = 105
    #else
    @State private var viewportHeight: CGFloat = 1080
    private let minutePixels: CGFloat = 7
    private let stationWidth: CGFloat = 232
    #endif
    private var uiScale: CGFloat { max(0.28, viewportHeight / 1080) }
    private var rowHeight: CGFloat { (isCompact ? 94 : 120) * uiScale }
    private var timelineHeaderHeight: CGFloat { (isCompact ? 38 : 44) * uiScale }
    private var guideRowCount: Int { isCompact ? 6 : 7 }
    private var timelineHeight: CGFloat { timelineHeaderHeight + CGFloat(guideRowCount) * rowHeight }
    private var detailHeight: CGFloat { (isCompact ? 300 : 335) * uiScale }
    // Always render the selected row inside the six-row viewport. This
    // avoids SwiftUI's asynchronous 2-axis scrolling losing the cursor
    // during rapid remote presses.
    private var visibleChannelRange: Range<Int> {
        let count = channels.count
        let window = min(guideRowCount, count)
        let start = min(max(0, selectedChannel - (isCompact ? 2 : 3)), max(0, count - window))
        return start..<(start + window)
    }

    private var anchor: Date {
        let hour = Calendar.current.dateInterval(of: .hour, for: now)?.start ?? now
        let minute = Calendar.current.component(.minute, from: now)
        return Calendar.current.date(byAdding: .minute,
                                     value: minute >= 30 ? 30 : 0, to: hour) ?? hour
    }
    private var channels: [GuideChannel] {
        store.channels.filter { ch in
            (!favoriteOnly || ch.favorite == true) &&
            (filter == "All" || filter == "In Plex" && ch.programs.contains(where: {$0.plex != nil}) ||
             filter == "Tablo" && ch.source.lowercased().contains("tablo") ||
             filter == "Philo" && ch.source.lowercased().contains("philo")) &&
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
    private var playerMetadata: TVPlaybackMetadata {
        guard let (channel,program) = selected else {
            return TVPlaybackMetadata(channelName: nil, title: "", subtitle: nil,
                                      season: nil, episode: nil, year: nil, isMovie: false)
        }
        return TVPlaybackMetadata(channelName: channel.network ?? channel.label,
                                  title: program.title, subtitle: program.subtitle,
                                  season: program.season, episode: program.episode,
                                  year: program.year, isMovie: program.programKind == "movie")
    }
    private var durationMinutes: Double { 480 }
    // The timeline's clock starts after the fixed station column.
    private var nowTimelineX: CGFloat {
        stationWidth + max(0, min(CGFloat(durationMinutes)*minutePixels,
            CGFloat(now.timeIntervalSince(anchor)/60)*minutePixels))
    }

    var body: some View {
        ZStack {
            TVPalette.background.ignoresSafeArea()
            HStack(spacing: isCompact ? 7 : 10) {
                sidebarRail
                    .frame(width: railWidth)
                VStack(spacing: isCompact ? 7 : 6) {
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
                        if channels.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "line.3.horizontal.decrease.circle")
                                    .font(.system(size: isCompact ? 24 : 44))
                                    .foregroundStyle(TVPalette.purple)
                                Text("No channels match this filter")
                                    .font(.system(size: isCompact ? 14 : 25, weight: .semibold))
                                Button("Show All Channels") {
                                    filter = "All"
                                    favoriteOnly = false
                                    searchText = ""
                                    resetSelection()
                                }
                                .buttonStyle(.borderedProminent)
                            }
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else {
                            timeline
                                .id("epg-\(filter)-\(favoriteOnly)-\(searchText)")
                            if isCompact { detailBar }
                        }
                    }
                }
            }
            .padding(.horizontal, isCompact ? 8 : 16)
            .padding(.vertical, isCompact ? 5 : 10)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background {
                GeometryReader { geometry in
                    Color.clear
                        .onAppear { viewportHeight = geometry.size.height }
                        .onChange(of: geometry.size.height) { _, value in
                            viewportHeight = value
                        }
                }
            }
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
        .modifier(UniversalGuideBackNavigation {
            if selectedAction == .plex {
                dismissPlexChoice()
            } else {
                focusSelectedTVProgram()
            }
        })
        // A true modal presentation isolates the Plex choice focus from
        // the guide. The former ZStack overlay allowed the guide to keep
        // processing remote directions and lose the chosen program.
        .fullScreenCover(isPresented: Binding(get: {
            selectedAction == .plex
        }, set: { shown in
            if !shown { dismissPlexChoice() }
        }), onDismiss: {
            plexChoiceDismissed = true
            if let pending = pendingPlexSession {
                pendingPlexSession = nil
                mediaSession = .plex(pending)
            }
        }) {
            UniversalPlexChoiceScreen(title: selected?.1.title ?? "Plex",
                canJoin: selected?.1.isOnNow == true,
                onBeginning: { playPlex("beginning") },
                onNow: { playPlex("join") },
                onExtra: selected?.0.source == "philo" ? "Open in Philo" : nil,
                onExtraAction: {
                    if let (channel, program) = selected {
                        dismissPlexChoice()
                        openPhilo(channel, program)
                    }
                },
                onCancel: { dismissPlexChoice() })
        }
        .fullScreenCover(item: $mediaSession) { session in
            TVMediaPlayerScreen(session: session, metadata: playerMetadata)
        }
        .fullScreenCover(isPresented: $showSettings, onDismiss: {
            Task { await refreshGuide() }
        }) {
            TVGuideRemoteTextPanel(title: "Settings", prompt: "Universal Guide server URL",
                                   value: $serverURL, mode: .server,
                                   onClose: { showSettings = false })
        }
        .fullScreenCover(isPresented: $showSearch) {
            TVGuideRemoteTextPanel(title: "Search", prompt: "Search channels or programs",
                                   value: $searchText, mode: .search,
                                   onClose: { showSearch = false })
        }
        .fullScreenCover(isPresented: $showFilters) {
            TVGuideFilterPanel(currentFilter: filter, favoritesOnly: favoriteOnly,
                onFilter: { name in
                    filter = name
                    favoriteOnly = false
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
        .onReceive(Timer.publish(every: 30, on: .main, in: .common).autoconnect()) { _ in
            now = Date()
        }
        .onChange(of: selected?.1.id) { _, _ in refreshSelectedArtwork() }
        .onChange(of: serverURL) { _, _ in
            artworkCache.removeAll()
            noArtwork.removeAll()
            refreshSelectedArtwork()
        }
        .onChange(of: store.channels.count) { _, _ in resetSelection() }
        .onChange(of: searchText) { _, _ in resetSelection() }
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
            Spacer()
        }
        .padding(isCompact ? 10 : 7)
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 16))
    }

    private func railButton(_ icon: String, name: String, key: String,
                            action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: isCompact ? 19 : 31, weight: .medium))
                .accessibilityLabel(name)
            .foregroundStyle(selectedFocus == key ? .white : Color(red: 0.83, green: 0.78, blue: 0.94))
            .frame(width: isCompact ? 38 : 80, height: isCompact ? 42 : 78)
            .background(Color.clear,
                        in: RoundedRectangle(cornerRadius: 13))
            .overlay(RoundedRectangle(cornerRadius: 13)
                .stroke(selectedFocus == key ? TVPalette.purple.opacity(0.9) : .clear, lineWidth: 2))
        }
        .buttonStyle(TVFocusCard())
        .focused($focus, equals: key)
    }

    private var header: some View {
        HStack(spacing: 18) {
            if isCompact {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Universal Guide")
                        .font(.system(size: 17, weight: .heavy))
                    Text("Live TV  ·  Plex  ·  Tablo  ·  Philo")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(now.formatted(date: .abbreviated, time: .shortened))
                    .font(.system(size: 12, weight: .semibold))
            } else {
                Text("Today")
                    .font(.system(size: 28, weight: .bold))
                Image(systemName: "bolt.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(TVPalette.purple)
                Text(now.formatted(date: .omitted, time: .shortened))
                    .font(.system(size: 23, weight: .medium))
                    .foregroundStyle(.secondary)
                Spacer()
                Text("UNIVERSAL GUIDE")
                    .font(.system(size: 15, weight: .semibold))
                    .tracking(3)
                    .foregroundStyle(.white.opacity(0.6))
            }
            if filter != "All" || favoriteOnly {
                Text([filter != "All" ? filter : "", favoriteOnly ? "Favorites" : ""]
                    .filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption.bold())
                    .padding(8)
                    .background(TVPalette.purple.opacity(0.18), in: Capsule())
            }
        }
        .frame(height: (isCompact ? 60 : 52) * uiScale)
    }

    private var timeline: some View {
        ScrollViewReader { horizontal in
            ScrollView(.horizontal) {
                VStack(alignment: .leading, spacing: 0) {
                    // The ruler never moves vertically, and only one axis
                    // (time) scrolls. Channel navigation shifts the visible
                    // six-row window instead of scheduling scroll animations.
                    HStack(spacing: 0) {
                        Text("CHANNEL")
                            .font(.system(size: isCompact ? 10 : 17, weight: .bold))
                            .foregroundStyle(.secondary)
                            .frame(width: stationWidth, alignment: .leading)
                            .padding(.leading, 13)
                        ForEach(0..<16, id: \.self) { slot in
                            Text(anchor.addingTimeInterval(Double(slot * 1800))
                                .formatted(date: .omitted, time: .shortened))
                                .font(.system(size: isCompact ? 10 : 18, weight: .semibold))
                                .foregroundStyle(Color(red: 0.83, green: 0.77, blue: 0.95))
                                .frame(width: minutePixels * 30, alignment: .leading)
                        }
                    }
                    .frame(height: max(isCompact ? 22 : 38, timelineHeaderHeight))
                    .background(Color(red: 0.105, green: 0.11, blue: 0.19))
                    .overlay(alignment: .leading) {
                        Rectangle()
                            .fill(TVPalette.purple)
                            .frame(width: isCompact ? 1.5 : 3)
                            .offset(x: nowTimelineX)
                            .allowsHitTesting(false)
                    }
                    ForEach(visibleChannelRange, id: \.self) { channelIndex in
                        let channel = channels[channelIndex]
                        HStack(spacing: 0) {
                            VStack(alignment: .leading, spacing: 2) {
                                if let net = channel.network, !net.isEmpty {
                                    Text(net.uppercased())
                                        .font(.system(size: isCompact ? 9 : 16, weight: .bold))
                                        .foregroundStyle(TVPalette.purple)
                                }
                                Text(channel.number ?? channel.label)
                                    .font(.system(size: isCompact ? 14 : 27, weight: .semibold))
                                if channel.number != nil && channel.label != channel.number {
                                    Text(channel.label)
                                        .font(.system(size: isCompact ? 10 : 18))
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                            }
                            .padding(.leading, isCompact ? 7 : 12)
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
                .frame(width: stationWidth + CGFloat(durationMinutes) * minutePixels,
                       alignment: .leading)
            }
            // Only program selection may scroll the horizontal time axis.
            // Never issue a vertical scroll: the selected channel is in
            // visibleChannelRange by construction.
            .onChange(of: selectedProgram) { _, _ in
                followSelectedTime(horizontal)
            }
            .onChange(of: selectedChannel) { _, _ in
                followSelectedTime(horizontal)
            }
        }
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13)
            .stroke(Color(red: 0.28, green: 0.26, blue: 0.4), lineWidth: 1))
        .frame(height: timelineHeight, alignment: .top)
    }

    private func followSelectedTime(_ proxy: ScrollViewProxy) {
        guard channels.indices.contains(selectedChannel),
              channels[selectedChannel].programs.indices.contains(selectedProgram) else { return }
        let channel = selectedChannel
        let program = selectedProgram
        // Wait for the six-row window to materialize its new program tile;
        // there is no secondary vertical scroll to race this request.
        DispatchQueue.main.async {
            guard selectedChannel == channel, selectedProgram == program else { return }
            proxy.scrollTo("program-\(channel)-\(program)", anchor: .center)
        }
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
                        Group {
                            if isCompact {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(program.title)
                                        .font(.system(size: 11, weight: .semibold))
                                        .lineLimit(1)
                                    if program.plex != nil {
                                        Label("IN PLEX", systemImage: "checkmark.circle.fill")
                                            .font(.system(size: 8, weight: .bold))
                                            .foregroundStyle(Color(red: 1, green: 0.85, blue: 0.51))
                                            .lineLimit(1)
                                    } else if let subtitle = program.subtitle {
                                        Text(subtitle)
                                            .font(.system(size: 9))
                                            .foregroundStyle(.white.opacity(0.77))
                                            .lineLimit(1)
                                    }
                                }
                            } else {
                                // The focused item becomes the details panel.
                                // Its bounds remain fixed: no focus-driven row
                                // reflow or jumping while using the TV remote.
                                HStack(alignment: .center, spacing: 14) {
                                    VStack(alignment: .leading, spacing: 7) {
                                        Text(program.title)
                                            .font(.system(size: selectedFocus == "program-\(index)-\(pi)" ? 27 : 24,
                                                          weight: .semibold))
                                            .lineLimit(1)
                                        if selectedFocus == "program-\(index)-\(pi)" {
                                            if let subtitle = program.subtitle, !subtitle.isEmpty {
                                                Text(subtitle)
                                                    .font(.system(size: 18))
                                                    .foregroundStyle(.white.opacity(0.84))
                                                    .lineLimit(1)
                                            }
                                            if let time = program.timeRange {
                                                Text(time)
                                                    .font(.system(size: 17))
                                                    .foregroundStyle(.white.opacity(0.78))
                                                    .lineLimit(1)
                                            }
                                        } else if let subtitle = program.subtitle {
                                            Text(subtitle)
                                                .font(.system(size: 16))
                                                .foregroundStyle(.white.opacity(0.8))
                                                .lineLimit(1)
                                        }
                                        if program.plex != nil {
                                            Label("IN PLEX", systemImage: "checkmark.circle.fill")
                                                .font(.system(size: 13, weight: .bold))
                                                .foregroundStyle(Color(red: 1, green: 0.85, blue: 0.51))
                                                .lineLimit(1)
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    if selectedFocus == "program-\(index)-\(pi)",
                                       CGFloat(end-cellStart)*minutePixels > 380,
                                       let selectedArtworkURL {
                                        AsyncImage(url: selectedArtworkURL) { phase in
                                            if let image = phase.image {
                                                image.resizable().scaledToFill()
                                            } else {
                                                Color.white.opacity(0.08)
                                            }
                                        }
                                        .frame(width: 152 * uiScale, height: 88 * uiScale)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))
                                    }
                                }
                                .padding(.bottom, program.isOnNow ? 10 : 0)
                                .overlay(alignment: .bottomLeading) {
                                    if program.isOnNow,
                                       let startDate = program.startDate,
                                       let endDate = program.endDate,
                                       endDate > startDate {
                                        GeometryReader { geometry in
                                            let duration = endDate.timeIntervalSince(startDate)
                                            let progress = max(0, min(1, now.timeIntervalSince(startDate) / duration))
                                            Capsule()
                                                .fill(Color.white.opacity(0.16))
                                                .frame(height: 5)
                                                .overlay(alignment: .leading) {
                                                    Capsule()
                                                        .fill(TVPalette.purple.opacity(0.9))
                                                        .frame(width: geometry.size.width * progress,
                                                               height: 5)
                                                }
                                                .frame(maxHeight: .infinity, alignment: .bottom)
                                        }
                                        .frame(height: 6)
                                    }
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                        .padding(.horizontal, isCompact ? 7 : 17)
                        .padding(.vertical, isCompact ? 0 : 10)
                        .foregroundStyle(.white)
                        .background(isCompact ?
                                    (program.plex != nil ? TVPalette.purple.opacity(0.22) : TVPalette.cell) :
                                    Color(red: 0.23, green: 0.25, blue: 0.30),
                                    in: RoundedRectangle(cornerRadius: isCompact ? 9 : 7))
                        .overlay(RoundedRectangle(cornerRadius: isCompact ? 9 : 7)
                            .stroke(selectedFocus == "program-\(index)-\(pi)" ?
                                    Color.white.opacity(0.9) : Color.white.opacity(0.13),
                                    lineWidth: selectedFocus == "program-\(index)-\(pi)" ? 2.5 : 1))
                        .shadow(color: .clear, radius: 0)
                    }
                    .frame(width: max(2,CGFloat(end-cellStart)*minutePixels-7),
                           height: rowHeight-(isCompact ? 6 : 14))
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
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(TVPalette.purple.opacity(0.95))
                .frame(width: isCompact ? 1.5 : 3)
                .offset(x: nowTimelineX - stationWidth)
                .allowsHitTesting(false)
        }
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
        HStack(alignment: .top, spacing: isCompact ? 7 : 28) {
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
            .frame(width: (isCompact ? 185 : 242) * uiScale,
                   height: (isCompact ? 250 : 290) * uiScale)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            if let (_, program) = selected {
                VStack(alignment: .leading, spacing: isCompact ? 5 : 14) {
                    Text(program.title)
                        .font(.system(size: isCompact ? 13 : 35, weight: .semibold))
                        .lineLimit(1)
                    Text([program.subtitle, program.timeRange]
                         .compactMap { $0 }.joined(separator: " · "))
                        .font(.system(size: isCompact ? 10 : 19))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Text(program.description ?? "Select a program to see viewing options.")
                        .font(.system(size: isCompact ? 10 : 22))
                        .foregroundStyle(Color(red: 0.83, green: 0.82, blue: 0.92))
                        .lineLimit(isCompact ? 2 : 5)
                    if program.plex != nil {
                        Text("✓ In Plex")
                            .font(.system(size: isCompact ? 9 : 19, weight: .semibold))
                            .foregroundStyle(Color(red: 1, green: 0.8, blue: 0.44))
                    }
                    Spacer(minLength: 0)
                    HStack(spacing: isCompact ? 7 : 14) {
                        if selected?.0.source == "tablo" && program.isOnNow {
                            watchButton("Watch Live", key: "watch-live") { playTablo() }
                        }
                        if program.plex != nil {
                            watchButton("Watch on Plex", key: "watch-primary") {
                                openPlexChoice()
                            }
                        }
                        if selected?.0.source == "philo" {
                            watchButton("Open in Philo", key: "watch-philo") {
                                if let (ch, entry) = selected { openPhilo(ch, entry) }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .trailing)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                Text("Select a program in the guide")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(isCompact ? 7 : 18)
        .frame(height: detailHeight)
        .background(TVPalette.panel, in: RoundedRectangle(cornerRadius: 17))

    }

    private func watchButton(_ label: String, key: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label).font(.system(size: isCompact ? 10 : 22, weight: .semibold))
                .lineLimit(1)
                .padding(.horizontal, isCompact ? 11 : 27)
                .padding(.vertical, isCompact ? 7 : 16)
                .foregroundStyle(.white)
                .background(Color(red: 0.22, green: 0.20, blue: 0.31),
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
        selectedProgram = channels.first?.programs.firstIndex(where: { $0.isOnNow }) ??
                          channels.first?.programs.firstIndex(where: {
                              ($0.endDate ?? .distantPast) > anchor
                          }) ?? 0
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
    private var railWidth: CGFloat { isCompact ? 48 : 68 }

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
        let keys = ["rail-search", "rail-filter", "rail-settings"]
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
                // No bottom action panel on Apple TV: OK on a program
                // opens its available playback actions.
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
        let rail = ["rail-search", "rail-filter", "rail-settings"]
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
        plexChoiceDismissed = false
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
            // The Apple TV guide now places details within each tile.
            // Keep the full Plex playback choice without a hidden panel.
            if isCompact { phoneSelection = "watch-primary" }
            else { openPlexChoice() }
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
                // Never present the player until the source-choice
                // fullScreenCover has fully dismissed. Competing covers can
                // cause an early onDisappear and end the HLS session.
                if plexChoiceDismissed {
                    mediaSession = .plex(session)
                } else {
                    pendingPlexSession = session
                }
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

// D-pad/OK text entry shared by iPhone controller and Apple TV remote.
// The TextField remains available for touch or a physical keyboard, but is
// not required to complete either Search or Settings.
private enum TVGuideTextMode: Equatable {
    case search
    case server
}

private struct TVGuideRemoteTextPanel: View {
    let title: String
    let prompt: String
    @Binding var value: String
    let mode: TVGuideTextMode
    let onClose: () -> Void
    @State private var selectedKey = 0
    private let columns = 10
    private let purple = Color(red: 0.72, green: 0.53, blue: 1)
    private var compact: Bool {
        #if os(iOS)
        true
        #else
        false
        #endif
    }
    private var keys: [String] {
        let letters = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/._-").map { String($0) }
        return letters + ["SPACE", "DELETE", "CLEAR", "DONE", "CANCEL"]
    }

    var body: some View {
        ZStack {
            Color(red: 0.038, green: 0.052, blue: 0.102).ignoresSafeArea()
            VStack(alignment: .leading, spacing: compact ? 7 : 18) {
                HStack {
                    Text(title)
                        .font(.system(size: compact ? 21 : 38, weight: .bold))
                    Spacer()
                    Text("Arrows · OK · Back")
                        .font(.system(size: compact ? 10 : 17))
                        .foregroundStyle(.secondary)
                }
                Text(prompt)
                    .font(.system(size: compact ? 11 : 19))
                    .foregroundStyle(.secondary)
                TextField(prompt, text: $value)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.system(size: compact ? 15 : 28))
                    .padding(compact ? 9 : 18)
                    .background(Color.white.opacity(0.08),
                                in: RoundedRectangle(cornerRadius: 11))
                    .accessibilityLabel(prompt)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 5),
                                         count: columns), spacing: compact ? 5 : 10) {
                    ForEach(Array(keys.enumerated()), id: \.offset) { index, key in
                        Button { activate(index) } label: {
                            Text(key == "SPACE" ? "␣" :
                                 key == "DELETE" ? "⌫" :
                                 key == "CLEAR" ? "CLR" :
                                 key == "CANCEL" ? "ESC" : key)
                                .font(.system(size: compact ? 12 : 24,
                                              weight: .semibold))
                                .minimumScaleFactor(0.65)
                                .lineLimit(1)
                                .frame(maxWidth: .infinity)
                                .frame(height: compact ? 31 : 60)
                                .foregroundStyle(.white)
                                .background(selectedKey == index ?
                                            purple.opacity(0.35) :
                                            Color.white.opacity(0.08),
                                            in: RoundedRectangle(cornerRadius: 8))
                                .overlay(RoundedRectangle(cornerRadius: 8)
                                    .stroke(selectedKey == index ? purple : .clear,
                                            lineWidth: compact ? 2 : 3))
                        }
                        .buttonStyle(.plain)
                    }
                }
                HStack {
                    Text(mode == .server ? "Use your Unraid guide URL" :
                         "Search titles and channel names")
                        .font(.system(size: compact ? 10 : 16))
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Done") { onClose() }
                        .buttonStyle(.borderedProminent)
                }
            }
            .padding(compact ? 18 : 38)
            .frame(maxWidth: compact ? 740 : 1050)
            #if os(tvOS)
            UniversalTVGuideRemoteReceiver(
                onMove: move,
                onSelect: { activate(selectedKey) },
                onBack: onClose
            )
            .ignoresSafeArea()
            #endif
        }
        .preferredColorScheme(.dark)
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadDirection)) { event in
            #if os(iOS)
            if let direction = event.object as? String { move(direction) }
            #endif
        }
        .onReceive(NotificationCenter.default.publisher(for: .ugGamepadSelect)) { _ in
            #if os(iOS)
            activate(selectedKey)
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

    private func move(_ direction: String) {
        let row = selectedKey / columns
        let column = selectedKey % columns
        switch direction {
        case "left": selectedKey = max(0, selectedKey - 1)
        case "right": selectedKey = min(keys.count - 1, selectedKey + 1)
        case "up": selectedKey = max(0, (row - 1) * columns + column)
        case "down": selectedKey = min(keys.count - 1, (row + 1) * columns + column)
        default: break
        }
    }

    private func activate(_ index: Int) {
        guard keys.indices.contains(index) else { return }
        switch keys[index] {
        case "SPACE": value += " "
        case "DELETE": if !value.isEmpty { value.removeLast() }
        case "CLEAR": value = ""
        case "DONE", "CANCEL": onClose()
        default: value += mode == .server ? keys[index].lowercased() : keys[index]
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
    let onExtra: String?
    let onExtraAction: () -> Void
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
        var items = ["Watch from Beginning"]
        if canJoin { items.append("Watch from Current Point") }
        if let onExtra, !isPhone { items.append(onExtra) }
        items.append("Cancel")
        return items
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
                            .background(Color.white.opacity(selectedIndex == index ? 0.11 : 0.06),
                                        in: RoundedRectangle(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12)
                                .stroke(selectedIndex == index ?
                                            purple.opacity(0.95) : .clear, lineWidth: 2))
                    }
                    .buttonStyle(.plain)
                    #if os(tvOS)
                    .focusEffectDisabled()
                    #endif
                    .focused($nativeFocus, equals: index)
                }
            }
            .padding(isPhone ? 24 : 36)
            .frame(width: isPhone ? 330 : 640)
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
        switch actions[index] {
        case "Watch from Beginning": onBeginning()
        case "Watch from Current Point": onNow()
        case "Cancel": onCancel()
        default: onExtraAction()
        }
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

private struct UniversalGuideBackNavigation: ViewModifier {
    let action: () -> Void
    @ViewBuilder func body(content: Content) -> some View {
        #if os(tvOS)
        content.onExitCommand(perform: action)
        #else
        content
        #endif
    }
}
