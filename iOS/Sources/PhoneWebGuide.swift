import SwiftUI
import WebKit
import GameController
import UIKit

// The existing Unraid web app IS the player: use its disk-backed Tablo HLS
// buffer, Plex transcoder, track menus, guide, and left-side settings rail.
// The native shell supplies Backbone commands, not a second playback stack.
struct PhoneWebGuide: View {
    @AppStorage("phone.unraidURL") private var serverAddress = ""
    @StateObject private var controller = BackboneWebController()
    @State private var isEditingServer = false
    @State private var loadError: String?
    private var destination: URL? {
        let raw = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: raw),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              url.host != nil, url.user == nil, url.password == nil else { return nil }
        return url
    }
    var body: some View {
        ZStack {
            Color(red: 0.035, green: 0.05, blue: 0.095).ignoresSafeArea()
            if let url = destination {
                PhoneGuideWebView(url: url, controller: controller, error: $loadError)
                    .ignoresSafeArea()
            } else {
                VStack(spacing: 14) {
                    Text("Universal Guide").font(.title.bold())
                    Text("Connect to the existing Unraid guide to use its original Plex and Tablo players.")
                        .multilineTextAlignment(.center)
                    Button("Set Guide Address") { isEditingServer = true }
                        .buttonStyle(.borderedProminent)
                }
                .padding()
            }
            if let loadError {
                VStack(spacing: 10) {
                    Text(loadError).font(.footnote).multilineTextAlignment(.center)
                    Button("Change Server Address") { isEditingServer = true }
                }
                .padding(18)
                .background(.black.opacity(0.95), in: RoundedRectangle(cornerRadius: 12))
                .padding()
            }
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $isEditingServer) {
            NavigationStack {
                Form {
                    Section("Existing Unraid web guide") {
                        TextField("http://192.168.1.10:8787", text: $serverAddress)
                            .keyboardType(.URL).textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Text("The existing web guide's left rail includes Search, Filters and Settings, all navigable with your Backbone.")
                    }
                    Section("Backbone controls") {
                        Text("D-pad — Guide selection / player controls")
                        Text("A — Select / Play or Pause")
                        Text("B — Back / Close")
                        Text("LB — Focus sidebar: Search, Filter, Settings")
                        Text("RB — Return focus to guide")
                        Text("X — Plex Chapters or options")
                        Text("Y — Go Live on Tablo")
                    }
                    Button("Save and Open") {
                        loadError = nil
                        isEditingServer = false
                    }
                }
                .navigationTitle("Guide Connection")
                .toolbar { Button("Done") { loadError = nil; isEditingServer = false } }
            }
        }
        .onAppear { if destination == nil { isEditingServer = true } }
    }
}

@MainActor final class BackboneWebController: ObservableObject {
    @Published var connected = false
    weak var view: WKWebView?
    private var tokens: [NSObjectProtocol] = []
    init() {
        tokens.append(NotificationCenter.default.addObserver(
            forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.configure() }
            })
        tokens.append(NotificationCenter.default.addObserver(
            forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.configure() }
            })
        configure()
    }
    deinit { tokens.forEach(NotificationCenter.default.removeObserver) }
    func configure() {
        let controllers = GCController.controllers()
        for controller in controllers {
            controller.extendedGamepad?.valueChangedHandler = nil
        }
        guard let pad = controllers.compactMap({ $0.extendedGamepad }).first else {
            connected = false; return
        }
        connected = true
        pad.dpad.up.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("up") } }
        pad.dpad.down.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("down") } }
        pad.dpad.left.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("left") } }
        pad.dpad.right.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("right") } }
        pad.buttonA.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("select") } }
        pad.buttonB.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("back") } }
        pad.buttonX.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("options") } }
        pad.buttonY.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("live") } }
        pad.leftShoulder.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("rail") } }
        pad.rightShoulder.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("guide") } }
    }
    nonisolated private func send(_ command: String) {
        Task { @MainActor [weak self] in
            guard let view = self?.view else { return }
            // Fixed command whitelist; never interpolate arbitrary network data.
            let commands = ["up","down","left","right","select","back","options","live","rail","guide"]
            guard commands.contains(command) else { return }
            view.evaluateJavaScript("window.universalGuideGamepad?.('\(command)')") { _, error in
                if let error { print("Backbone web command error: \(error.localizedDescription)") }
            }
        }
    }
}

struct PhoneGuideWebView: UIViewRepresentable {
    let url: URL
    @ObservedObject var controller: BackboneWebController
    @Binding var error: String?
    func makeCoordinator() -> Coordinator { Coordinator(base: url, error: $error) }
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.addUserScript(WKUserScript(
            source: Self.controllerScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false
        view.backgroundColor = .black
        view.scrollView.contentInsetAdjustmentBehavior = .never
        view.allowsBackForwardNavigationGestures = false
        controller.view = view
        view.load(URLRequest(url: url))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {
        controller.view = view
        if context.coordinator.base != url {
            context.coordinator.base = url
            view.load(URLRequest(url: url))
        }
    }
    final class Coordinator: NSObject, WKNavigationDelegate {
        var base: URL
        var error: Binding<String?>
        init(base: URL, error: Binding<String?>) { self.base = base; self.error = error }
        func webView(_ view: WKWebView, didFinish navigation: WKNavigation!) {
            error.wrappedValue = nil
        }
        func webView(_ view: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                     withError failure: Error) {
            error.wrappedValue = "Could not load Universal Guide: \(failure.localizedDescription)"
        }
        func webView(_ view: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let destination = action.request.url, let host = destination.host?.lowercased() else {
                decisionHandler(.cancel); return
            }
            if host == base.host?.lowercased() {
                decisionHandler(.allow); return
            }
            if destination.scheme == "https" && host == "www.philo.com"
                && destination.path.hasPrefix("/player/") {
                decisionHandler(.cancel)
                UIApplication.shared.open(destination, options: [.universalLinksOnly: true])
                return
            }
            decisionHandler(.cancel)
        }
    }

    // All regular playback actions call the EXISTING web page's own buttons.
    // The guide's existing keyboard handling owns program navigation, sidebar,
    // bottom details panel, Plex track selection and Tablo DVR timeline.
    private static let controllerScript = #"""
    (() => {
      const $ = id => document.getElementById(id);
      const active = () => document.activeElement;
      const shown = node => !!node && !node.hidden &&
        !node.closest('[hidden]') && node.getClientRects().length > 0;
      const focus = node => {
        if (!shown(node) || node.disabled) return false;
        node.focus({preventScroll:true});
        node.scrollIntoView({block:'nearest',inline:'nearest'});
        return true;
      };
      const click = id => {
        const node = $(id);
        if(!shown(node) || node.disabled) return false;
        node.click();
        return true;
      };
      const key = value => {
        const node = active() || document.body;
        node.dispatchEvent(new KeyboardEvent('keydown',
          {key:value,bubbles:true,cancelable:true}));
      };
      const player = () => shown($('playerbackdrop'));
      const tablo = () => player() && shown($('tablodvr'));
      const plex = () => player() && shown($('plexoverlay'));
      const trackPanel = () => shown($('plextrackpanel'));
      const controls = () => {
        const panel=trackPanel() ? $('plextrackpanel') :
                    tablo() ? $('tablodvr') : $('plexoverlay');
        return Array.from(panel?.querySelectorAll(
          'button:not([disabled]),input[type="range"]:not([disabled])') || []).filter(shown);
      };
      function movePlayerFocus(direction) {
        const items=controls();
        if(!items.length)return;
        const current=items.indexOf(active());
        const delta=direction==='up'||direction==='left'?-1:1;
        focus(items[Math.max(0,Math.min(items.length-1,
          current<0 ? 0 : current+delta))]);
      }
      function revealPlayer() {
        if(tablo()) {
          $('tablodvr').classList.remove('controls-hidden');
          focus($('tablodvrpause'));
        } else if(plex()) {
          $('plexoverlay').classList.remove('controls-hidden');
          focus($('plexpause'));
        }
      }
      window.universalGuideGamepad = function(command) {
        const directions={up:'ArrowUp',down:'ArrowDown',
                          left:'ArrowLeft',right:'ArrowRight'};
        if(command==='back') { key('Escape'); return; }
        if(player()) {
          if(trackPanel()) {
            if(directions[command]) key(directions[command]);
            else if(command==='select') {
              const el=active(); if(shown(el))el.click();
            } else if(command==='options')click('plextrackcancel');
            return;
          }
          if(command==='live' && tablo()) { click('tablodvrlive'); return; }
          if(command==='options' && plex()) {
            if(!click('plexchaptersbutton')) click('plexaudiobutton');
            return;
          }
          if(command==='select') {
            const el=active();
            if(el && el.matches('button,input') && shown(el))el.click();
            else click(tablo()?'tablodvrpause':'plexpause');
            return;
          }
          if(command==='left'||command==='right') {
            if(active()?.matches('input[type="range"]')) {
              const el=active();
              el.value=String(Math.max(0,Math.min(1000,
                Number(el.value)+(command==='right'?50:-50))));
              el.dispatchEvent(new Event('change',{bubbles:true}));
            } else {
              click(tablo() ?
                (command==='left'?'tablodvrback':'tablodvrforward') :
                (command==='left'?'plexback':'plexforward'));
            }
            return;
          }
          if(command==='up'||command==='down') {
            revealPlayer();
            movePlayerFocus(command);
          } else if(command==='rail'||command==='guide') {
            revealPlayer();
          }
          return;
        }
        if(command==='rail') {
          // Persistent LEFT SIDE rail, just as in the web interface.
          const rail=['opensearch','openfilters','opensettings'];
          const current=rail.indexOf(active()?.id);
          focus($(rail[current<0?0:(current+1)%rail.length]));
          return;
        }
        if(command==='guide') {
          if(typeof remoteGuideFocus==='function')remoteGuideFocus();
          else focus($('guide')?.querySelector('.epg-program[data-program]'));
          return;
        }
        if(command==='options') {
          focus($('openfilters'));click('openfilters');return;
        }
        if(command==='select') {
          const el=active();
          if(shown(el) && (el.matches('button,a[href],[role="button"],summary')))el.click();
          else if(typeof remoteGuideFocus==='function')remoteGuideFocus();
          return;
        }
        if(command==='live') { click('now'); return; }
        if(directions[command]) {
          if(!active() || active()===document.body) {
            if(typeof remoteGuideFocus==='function')remoteGuideFocus();
          }
          key(directions[command]);
        }
      };
      // Make guide ready without requiring an initial touchscreen tap.
      document.addEventListener('DOMContentLoaded',()=>{
        if(typeof remoteGuideFocus==='function')remoteGuideFocus();
      },{once:true});
    })();
    """#
}
