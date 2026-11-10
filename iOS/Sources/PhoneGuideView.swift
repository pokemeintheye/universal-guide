import SwiftUI
import AVKit
import UIKit

private let navy = Color(red: 0.055, green: 0.075, blue: 0.125)
private let navyLight = Color(red: 0.105, green: 0.15, blue: 0.24)
private let gridLine = Color(red: 0.19, green: 0.25, blue: 0.35)
private let stationWidth: CGFloat = 115
private let minuteWidth: CGFloat = 3.2
private let guideHeight: CGFloat = 70

struct PhoneGuideView: View {
    @AppStorage("phone.unraidURL") private var serverURL = ""
    @StateObject private var store = PhoneGuideStore()
    @StateObject private var controller = BackboneController()
    @State private var showingSettings = false
    @State private var details = false
    @State private var diagnostics = true
    @State private var channelIndex = 0
    @State private var programIndex = 0
    @State private var playback: PlaybackSession?
    @State private var busy = false
    @State private var actionError: String?
    @State private var philoResult: String?
    @State private var philoBusy = false

    private var channel: PhoneChannel? {
        store.channels.indices.contains(channelIndex) ? store.channels[channelIndex] : nil
    }
    private var program: PhoneProgram? {
        guard let channel, channel.programs.indices.contains(programIndex) else { return nil }
        return channel.programs[programIndex]
    }
    private var anchor: Date {
        let now = Date()
        let seconds = now.timeIntervalSince1970
        return Date(timeIntervalSince1970: floor(seconds / 1800) * 1800)
    }
    private let duration: TimeInterval = 8 * 3600
    private var timelineWidth: CGFloat { CGFloat(duration / 60) * minuteWidth }

    var body: some View {
        GeometryReader { geom in
            VStack(spacing: 0) {
                topBar
                if let message = store.error {
                    VStack(spacing: 10) {
                        Text(message).foregroundStyle(.orange)
                        Button("Settings") { showingSettings = true }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if store.loading && store.channels.isEmpty {
                    ProgressView("Loading guide…").frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if store.channels.isEmpty {
                    VStack(spacing: 10) {
                        Text("Connect to your Unraid Universal Guide server")
                        Button("Settings") { showingSettings = true }
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    timeline(height: geom.size.height - 70)
                }
                if diagnostics {
                    HStack(spacing: 15) {
                        Text(controller.connected ? "Backbone connected" : "Backbone not detected")
                        Text("Input: \(controller.lastInput)")
                        Text("Channel: \(channelIndex + 1)/\(store.channels.count)")
                        Text("Program: \(programIndex + 1)")
                        Spacer()
                        Text("A Select  ·  B Back  ·  X Options  ·  Y Now")
                    }
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundStyle(.cyan.opacity(0.85))
                    .padding(.horizontal, 12).frame(height: 25)
                    .background(.black.opacity(0.7))
                }
            }
            .background(navy.ignoresSafeArea())
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showingSettings, onDismiss: { Task { await store.refresh(server: serverURL) } }) {
            NavigationStack {
                Form {
                    Section("Unraid server") {
                        TextField("http://192.168.1.10:8787", text: $serverURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        Text("Use the local Universal Guide Docker address. Do not expose this API to the public internet.")
                    }
                    Section("Testing") {
                        Toggle("Controller diagnostics", isOn: $diagnostics)
                        Text("Backbone controller: \(controller.connected ? "Connected" : "Not connected")")
                    }
                    Button("Save & Connect") { showingSettings = false }
                }
                .navigationTitle("Settings")
                .toolbar { Button("Done") { showingSettings = false } }
            }
        }
        .sheet(isPresented: $details) { detailSheet }
        .fullScreenCover(item: $playback) { session in
            PhoneVideoScreen(session: session)
        }
        .task {
            await store.refresh(server: serverURL)
            if serverURL.isEmpty { showingSettings = true }
        }
        .onAppear {
            controller.onAction = { action in handle(action) }
        }
        .onChange(of: store.channels.count) { _ in
            channelIndex = 0
            programIndex = 0
        }
    }

    private var topBar: some View {
        HStack(spacing: 12) {
            HStack(spacing: 7) {
                Image(systemName: "tv")
                    .foregroundStyle(.cyan)
                Text("UNIVERSAL GUIDE").font(.system(size: 16, weight: .heavy, design: .rounded))
            }
            Spacer()
            Text("LIVE  ·  PLEX  ·  PHILO  ·  TABLO")
                .font(.system(size: 9, weight: .bold)).foregroundStyle(.secondary)
            Button { diagnostics.toggle() } label: { Image(systemName: "gamecontroller") }
            Button { Task { await store.refresh(server: serverURL) } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .disabled(store.loading)
            Button { showingSettings = true } label: { Image(systemName: "gearshape") }
        }
        .font(.system(size: 14))
        .padding(.horizontal, 13)
        .frame(height: 43)
        .background(navyLight)
    }

    private func timeline(height: CGFloat) -> some View {
        let start = anchor
        return ScrollViewReader { verticalProxy in
            ScrollView(.vertical) {
                HStack(alignment: .top, spacing: 0) {
                    VStack(spacing: 0) {
                        Text("CHANNEL")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(.secondary)
                            .frame(width: stationWidth, height: 37, alignment: .leading)
                            .padding(.leading, 8)
                        ForEach(Array(store.channels.enumerated()), id: \.element.id) { i, channel in
                            Button {
                                channelIndex = i
                                programIndex = 0
                                details = true
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(channel.station)
                                        .font(.system(size: 12, weight: .bold))
                                        .lineLimit(2)
                                    if let number = channel.number, number != channel.station {
                                        Text(number).font(.system(size: 10)).foregroundStyle(.secondary)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 8)
                                .frame(height: guideHeight)
                                .background(i == channelIndex ? Color.cyan.opacity(0.20) : navy)
                                .overlay(alignment: .bottom) { gridLine.frame(height: 1) }
                            }
                            .buttonStyle(.plain)
                            .id("ch-\(i)")
                        }
                    }
                    .frame(width: stationWidth)
                    ScrollView(.horizontal) {
                        VStack(spacing: 0) {
                            timeHeader(start: start)
                            ForEach(Array(store.channels.enumerated()), id: \.element.id) { c, channel in
                                ZStack(alignment: .leading) {
                                    HStack(spacing: 0) {
                                        ForEach(0..<16, id: \.self) { _ in
                                            Rectangle().fill(navyLight.opacity(0.4))
                                                .frame(width: 30 * minuteWidth - 1, height: guideHeight)
                                                .overlay(alignment: .trailing) { gridLine.frame(width: 1) }
                                        }
                                    }
                                    ForEach(Array(channel.programs.enumerated()), id: \.element.id) { p, program in
                                        if let interval = visibleInterval(program, start: start) {
                                            Button {
                                                channelIndex = c; programIndex = p
                                                details = true
                                            } label: {
                                                VStack(alignment: .leading, spacing: 2) {
                                                    Text(program.title).font(.system(size: 12, weight: .semibold)).lineLimit(1)
                                                    if program.plex?.ratingKey != nil {
                                                        Text("IN PLEX")
                                                            .font(.system(size: 9, weight: .bold))
                                                            .foregroundStyle(.mint)
                                                    } else if let subtitle = program.subtitle, !subtitle.isEmpty {
                                                        Text(subtitle).font(.system(size: 10)).lineLimit(1).foregroundStyle(.secondary)
                                                    }
                                                }
                                                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                                                .padding(7)
                                                .background(program.plex?.ratingKey != nil
                                                    ? Color(red: 0.09, green: 0.30, blue: 0.25)
                                                    : Color(red: 0.15, green: 0.23, blue: 0.36))
                                                .clipShape(RoundedRectangle(cornerRadius: 5))
                                                .overlay {
                                                    RoundedRectangle(cornerRadius: 5)
                                                        .stroke(c == channelIndex && p == programIndex ? Color.cyan : gridLine,
                                                                lineWidth: c == channelIndex && p == programIndex ? 3 : 1)
                                                }
                                            }
                                            .buttonStyle(.plain)
                                            .frame(width: max(1, interval.width - 4), height: guideHeight - 6)
                                            .offset(x: interval.x + 2)
                                            .accessibilityIdentifier("program-\(c)-\(p)")
                                        }
                                    }
                                    if start <= Date() && Date() < start.addingTimeInterval(duration) {
                                        Rectangle().fill(Color.red.opacity(0.9)).frame(width: 2, height: guideHeight)
                                            .offset(x: CGFloat(Date().timeIntervalSince(start) / 60) * minuteWidth)
                                    }
                                }
                                .frame(width: timelineWidth, height: guideHeight, alignment: .leading)
                                .overlay(alignment: .bottom) { gridLine.frame(height: 1) }
                            }
                        }
                        .frame(width: timelineWidth, alignment: .leading)
                    }
                    .scrollIndicators(.hidden)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: max(100, height - (diagnostics ? 25 : 0)))
            .onChange(of: channelIndex) { index in
                withAnimation(.easeOut(duration: 0.15)) { verticalProxy.scrollTo("ch-\(index)", anchor: .center) }
            }
        }
    }

    private func timeHeader(start: Date) -> some View {
        HStack(spacing: 0) {
            ForEach(0..<16, id: \.self) { i in
                Text(start.addingTimeInterval(TimeInterval(i * 1800))
                    .formatted(date: .omitted, time: .shortened))
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 30 * minuteWidth, height: 37, alignment: .leading)
                    .padding(.leading, 5)
            }
        }
        .background(navyLight)
    }

    private func visibleInterval(_ program: PhoneProgram, start: Date) -> (x: CGFloat, width: CGFloat)? {
        guard let from = program.begins, let to = program.ends else { return nil }
        let a = max(0, from.timeIntervalSince(start))
        let b = min(duration, to.timeIntervalSince(start))
        if b <= a { return nil }
        return (CGFloat(a / 60) * minuteWidth, CGFloat((b - a) / 60) * minuteWidth)
    }

    private func handle(_ action: PadAction) {
        if playback != nil {
            if action == .back { playback = nil }
            return
        }
        if showingSettings { if action == .back { showingSettings = false }; return }
        if details {
            if action == .back { details = false }
            return
        }
        let count = store.channels.count
        guard count > 0 else { return }
        switch action {
        case .up, .previousPage:
            channelIndex = max(0, channelIndex - (action == .up ? 1 : 5))
            programIndex = min(programIndex, max(0, (channel?.programs.count ?? 1) - 1))
        case .down, .nextPage:
            channelIndex = min(count - 1, channelIndex + (action == .down ? 1 : 5))
            programIndex = min(programIndex, max(0, (channel?.programs.count ?? 1) - 1))
        case .left: programIndex = max(0, programIndex - 1)
        case .right: programIndex = min(max(0, (channel?.programs.count ?? 1) - 1), programIndex + 1)
        case .now:
            if let idx = channel?.programs.firstIndex(where: { $0.onNow }) { programIndex = idx }
        case .select, .options:
            if program != nil { details = true }
        case .back: break
        }
    }

    private var detailSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 13) {
                    if let channel, let program {
                        Text(program.title).font(.title2.bold())
                        HStack {
                            Text(channel.station)
                            if program.onNow { Text("ON NOW").foregroundStyle(.green).fontWeight(.bold) }
                        }.foregroundStyle(.secondary)
                        if let subtitle = program.subtitle { Text(subtitle).font(.headline) }
                        Text(program.description ?? "No description available.")
                            .foregroundStyle(.secondary)
                        Divider()
                        if let ratingKey = program.plex?.ratingKey, Int(ratingKey) != nil {
                            Text("AVAILABLE IN PLEX").foregroundStyle(.mint).font(.caption.bold())
                            HStack {
                                Button("Watch from Beginning") { playPlex("beginning", program) }
                                    .buttonStyle(.borderedProminent)
                                if program.onNow {
                                    Button("Watch from Current Point") { playPlex("join", program) }
                                        .buttonStyle(.bordered)
                                }
                            }
                        }
                        if channel.source == "tablo" && program.onNow {
                            Button("Watch Live on Tablo") { playTablo(channel, program) }
                                .buttonStyle(.borderedProminent)
                        }
                        if channel.source == "philo", let links = program.philoLinks {
                            Text("PHILO HANDOFF TEST").font(.caption.bold())
                            if let url = links.channel { Button("Open Channel in Philo") { openPhilo(url) } }
                            if let url = links.episode { Button("Open Episode in Philo") { openPhilo(url) } }
                            if let url = links.show { Button("Open Series in Philo") { openPhilo(url) } }
                            Text("iPhone Universal Links do not prove Apple TV handoff or automatic playback.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } else {
                        Text("No program selected")
                    }
                    if busy { ProgressView("Preparing playback…") }
                    if philoBusy { ProgressView("Opening Philo…") }
                    if let philoResult { Text(philoResult).foregroundStyle(.cyan).font(.caption) }
                    if let actionError { Text(actionError).foregroundStyle(.orange) }
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding(18)
            }
            .background(navy)
            .navigationTitle("Program details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { details = false } }
        }
        .presentationDetents([.large])
    }

    private func playPlex(_ mode: String, _ program: PhoneProgram) {
        guard !busy else { return }
        busy = true; actionError = nil
        Task {
            defer { busy = false }
            do {
                playback = try await PhonePlayback.plex(server: serverURL, program: program, mode: mode)
                details = false
            } catch { actionError = error.localizedDescription }
        }
    }
    private func playTablo(_ channel: PhoneChannel, _ program: PhoneProgram) {
        guard !busy else { return }
        busy = true; actionError = nil
        Task {
            defer { busy = false }
            do {
                playback = try await PhonePlayback.tablo(server: serverURL, channel: channel, program: program)
                details = false
            } catch { actionError = error.localizedDescription }
        }
    }
    private func openPhilo(_ link: PhonePhiloLink) {
        guard !philoBusy else { return }
        guard let u = URL(string: link.url), u.scheme == "https",
              u.host == "www.philo.com", u.path.hasPrefix("/player/") else {
            philoResult = "Invalid or untrusted Philo URL"
            return
        }
        philoBusy = true
        philoResult = "Requesting Philo app…"
        UIApplication.shared.open(u, options: [.universalLinksOnly: true]) { success in
            Task { @MainActor in
                philoBusy = false
                philoResult = success ? "iOS accepted Philo Universal Link. Check the destination and playback."
                                      : "iOS did not open Philo for this link; no Safari fallback."
            }
        }
    }
}

struct PhoneVideoScreen: View {
    let session: PlaybackSession
    @Environment(\.dismiss) private var dismiss
    @State private var player = AVPlayer()
    @State private var playerError: String?

    var body: some View {
        ZStack(alignment: .top) {
            Color.black.ignoresSafeArea()
            VideoPlayer(player: player).ignoresSafeArea()
            HStack {
                Button { dismiss() } label: {
                    Label("Back to Guide", systemImage: "chevron.backward")
                }
                .buttonStyle(.borderedProminent)
                Spacer()
                Text(session.title).font(.caption.bold())
                if session.kind == "tablo" {
                    Button("Live") { returnToLive() }.buttonStyle(.bordered)
                }
            }
            .padding(12)
            if let playerError {
                Text(playerError).foregroundStyle(.orange).padding(12)
                    .background(.black.opacity(0.7))
                    .frame(maxHeight: .infinity, alignment: .bottom)
            }
        }
        .onAppear {
            player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
            player.play()
        }
        .onDisappear {
            player.pause()
            player.replaceCurrentItem(with: nil)
            Task { await PhoneAPI.delete(session.stopURL) }
        }
    }
    private func returnToLive() {
        player.pause()
        player.replaceCurrentItem(with: AVPlayerItem(url: session.playlist))
        player.play()
    }
}
