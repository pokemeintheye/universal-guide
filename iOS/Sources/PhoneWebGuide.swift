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
                        Text("A / OK — Reveal playback controls, then activate selection")
                        Text("Y — Play / Pause (temporary test mapping)")
                        Text("B — Back / Close")
                        Text("LB — Focus sidebar: Search, Filter, Settings")
                        Text("RB — Return focus to guide")
                        Text("X — Expand/collapse details and focus playback choices")
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
        pad.buttonY.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("playpause") } }
        pad.leftShoulder.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("rail") } }
        pad.rightShoulder.pressedChangedHandler = { [weak self] _, _, down in if down { self?.send("guide") } }
    }
    nonisolated private func send(_ command: String) {
        Task { @MainActor [weak self] in
            guard let view = self?.view else { return }
            // Fixed command whitelist; never interpolate arbitrary network data.
            let commands = ["up","down","left","right","select","back","options","live","rail","guide","playpause"]
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
      // The TV browser layout has a 190–275px detail strip, which consumes
      // most of the available landscape height on an iPhone. Compact only in
      // this native iPhone shell; never change the web or Apple TV layout.
      document.documentElement.classList.add('ug-phone');
      const compactStyle = document.createElement('style');
      compactStyle.textContent = `
      html.ug-phone .guide-app .guide-detail {
        height: 84px !important; min-height: 0 !important;
        max-height: 84px !important; flex: 0 0 84px !important;
        overflow: hidden !important; padding: 7px 12px !important;
        gap: 9px !important; border-radius: 12px !important;
      }
      html.ug-phone .guide-app .guide-detail .guide-art {
        display: none !important;
      }
      html.ug-phone .guide-app .guide-detail .guide-detail-content {
        padding: 0 90px 0 0 !important; overflow: hidden !important;
      }
      html.ug-phone .guide-app .guide-detail .guide-detail-content h2 {
        font-size: 17px !important; line-height: 1.15 !important;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      html.ug-phone .guide-app .guide-detail :is(
        #guidepreviewdesc,#guidepreviewsources,.guide-detail-actions) {
        display: none !important;
      }
      html.ug-phone .guide-app .guide-detail #guidepreviewepisode {
        font-size: 11px !important; white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis;
      }
      html.ug-phone .guide-app .guide-detail.ug-expanded {
        height: min(218px, 40dvh) !important;
        max-height: min(218px, 40dvh) !important;
        flex: 0 0 min(218px, 40dvh) !important;
        padding: 10px 12px !important;
        overflow: auto !important;
      }
      html.ug-phone .guide-app .guide-detail.ug-expanded .guide-art {
        display: grid !important; height: 100% !important;
        max-width: 110px !important; flex: 0 0 100px !important;
      }
      html.ug-phone .guide-app .guide-detail.ug-expanded .guide-detail-content {
        padding: 0 65px 0 0 !important;
      }
      html.ug-phone .guide-app .guide-detail.ug-expanded :is(
        #guidepreviewdesc,#guidepreviewsources,.guide-detail-actions) {
        display: flex !important;
      }
      html.ug-phone #ug-phone-preview-toggle {
        position: absolute; right: 8px; top: 8px;
        z-index: 4; min-width: 69px; min-height: 35px;
        border: 1px solid #70628b; border-radius: 9px;
        padding: 5px; background: #292943; color: #f7f4ff;
        font: 600 12px system-ui;
      }
      html.ug-phone .guide-app .guide-detail {
        position: relative !important;
      }
      html.ug-phone .guide-app .guide-main {
        flex: 1 1 0 !important; min-height: 0 !important;
        overflow: hidden !important;
      }
      html.ug-phone .player-backdrop .tablo-dvr.ug-pad-visible {
        opacity: 1 !important; visibility: visible !important;
        pointer-events: auto !important;
      }
      html.ug-phone .guide-app .guide-main .timeline {
        height: 100% !important; max-height: 100% !important;
        overflow: auto !important;
      }
      `;
      document.head.appendChild(compactStyle);
      const panel = $('guidedetail');
      const detailToggle = document.createElement('button');
      detailToggle.id = 'ug-phone-preview-toggle';
      detailToggle.type = 'button';
      detailToggle.textContent = 'X · Details';
      detailToggle.setAttribute('aria-label','Expand program details');
      detailToggle.addEventListener('click', () => {
        const expanded = panel?.classList.toggle('ug-expanded');
        detailToggle.textContent = expanded ? 'X · Minimize' : 'X · Details';
        detailToggle.setAttribute('aria-label',expanded?'Minimize program details':'Expand program details');
        // Return focus to the selected guide program after collapsing.
        if(!expanded && typeof remoteGuideFocus === 'function') remoteGuideFocus();
      });
      panel?.appendChild(detailToggle);
      const active = () => document.activeElement;
      const shown = node => !!node && !node.disabled &&
        !node.hidden && !node.closest('[hidden]') &&
        node.getClientRects().length > 0 &&
        getComputedStyle(node).visibility !== 'hidden' &&
        getComputedStyle(node).display !== 'none';
      const focus = node => {
        if(!shown(node))return false;
        node.focus({preventScroll:true});
        node.scrollIntoView({block:'nearest',inline:'nearest'});
        return true;
      };
      const click = id => {
        const node=$(id);
        if(!shown(node))return false;
        node.click();
        return true;
      };
      const key = value => {
        const node=active() || document.body;
        node.dispatchEvent(new KeyboardEvent('keydown',
          {key:value,bubbles:true,cancelable:true}));
      };
      const player = () => shown($('playerbackdrop'));
      // Native web player overlays auto-hide via visibility:hidden.
      // Determine the active media by the panel's hidden attribute instead
      // of visual visibility, or an invisible Tablo overlay is mistaken for Plex.
      const tablo = () => player() && !$('tablodvr')?.hidden;
      const plex = () => player() && !$('plexoverlay')?.hidden;
      const trackPanel = () => player() && shown($('plextrackpanel'));
      const playerOverlay = () => tablo() ? $('tablodvr') : $('plexoverlay');
      let overlayVisible = false;
      let overlayTimer = null;
      let guideFocusBeforeDetails = null;
      const controls = () => {
        let panel = trackPanel() ? $('plextrackpanel') : playerOverlay();
        return Array.from(panel?.querySelectorAll(
          'button:not([disabled]),input[type="range"]:not([disabled])') || [])
          .filter(shown);
      };
      const media = () => $('plexvideo');
      function transportClick(id) {
        // Transport buttons may be visibility:hidden while the overlay is
        // collapsed. Their original web click handlers still seek the
        // recording; direct click bypasses only the UI visibility check.
        const target=$(id);
        if(!target || target.disabled)return false;
        target.click();
        return true;
      }
      function playPause() {
        if(!player())return false;
        const video=media();
        if(!video)return false;
        const target=tablo()?'tablodvrpause':'plexpause';
        // The existing player's play/pause handler also refreshes its UI.
        const button=$(target);
        if(!button || button.disabled)return false;
        button.click();
        return true;
      }
      function clearOverlayTimer() {
        if(overlayTimer!==null)clearTimeout(overlayTimer);
        overlayTimer=null;
      }
      function hideOverlay() {
        if(!player() || trackPanel())return;
        overlayVisible=false;
        clearOverlayTimer();
        playerOverlay()?.classList.remove('ug-pad-visible');
        playerOverlay()?.classList.add('controls-hidden');
        if(playerOverlay()?.contains(active()))active()?.blur();
      }
      function revealOverlay(first=true) {
        if(!player())return;
        overlayVisible=true;
        clearOverlayTimer();
        const overlay=playerOverlay();
        overlay?.classList.remove('controls-hidden');
        overlay?.classList.add('ug-pad-visible');
        // Existing web player has a 4.5 second pointer timer, but a controller
        // overlay must persist until input stops. Reset that timer on each press.
        if(typeof showTabloOverlay==='function' && tablo())showTabloOverlay();
        if(typeof showPlexOverlay==='function' && plex())showPlexOverlay();
        if(first || !controls().includes(active()))
          focus($(tablo()?'tablodvrpause':'plexpause'));
        overlayTimer=setTimeout(hideOverlay,6000);
      }
      function moveControls(direction) {
        if(!overlayVisible){revealOverlay(true);return}
        const items=controls();
        if(!items.length)return;
        const current=items.indexOf(active());
        const delta=(direction==='up'||direction==='left')?-1:1;
        const next=current<0?0:Math.max(0,Math.min(items.length-1,current+delta));
        focus(items[next]);
        revealOverlay(false);
      }
      function chooseDetailButton() {
        const panel=$('guidedetail');
        if(!panel)return false;
        if(!panel.classList.contains('ug-expanded'))click('ug-phone-preview-toggle');
        const choices=Array.from(panel.querySelectorAll('.guide-detail-actions button:not([disabled])'))
          .filter(shown);
        return focus(choices[0] || $('ug-phone-preview-toggle'));
      }
      function guideFocus() {
        const selected=$('guide')?.querySelector('.epg-program.return-selected') ||
                       $('guide')?.querySelector('.epg-program[data-program]');
        if(selected)focus(selected);
        else focus($('openfilters'));
      }
      function guideDirection(command) {
        const directions={up:'ArrowUp',down:'ArrowDown',left:'ArrowLeft',right:'ArrowRight'};
        const focused=active();
        if(!shown(focused))guideFocus();
        // Explicitly handle rail focus because keyboard dispatch on WKWebView
        // does not always behave exactly like physical keyboard input.
        const rail=['opensearch','openfilters','opensettings'];
        const i=rail.indexOf(active()?.id);
        if(i>=0){
          if(command==='up'||command==='down'){
            focus($(rail[Math.max(0,Math.min(2,i+(command==='down'?1:-1)))]));
            return;
          }
          if(command==='right'){guideFocus();return}
        }
        if($('guidedetail')?.contains(active())){
          const actions=Array.from($('guidedetail').querySelectorAll(
            '.guide-detail-actions button:not([disabled])')).filter(shown);
          let at=actions.indexOf(active());
          if(command==='right'||command==='left'){
            if(actions.length)focus(actions[Math.max(0,Math.min(actions.length-1,
                 at+(command==='right'?1:-1)))]);return;
          }
          if(command==='up'){guideFocus();return}
          if(command==='down'){if(at<0)focus(actions[0]);return}
        }
        // Use the existing web guide's keyboard algorithm for EPG navigation.
        key(directions[command]);
      }
      // Reliable controller targets, without touching the backend or redefining
      // the web HLS playback implementation.
      window.universalGuideGamepad=function(command){
        if(command==='playpause'){playPause();return}
        if(player()){
          if(command==='back'){
            if(trackPanel())click('plextrackcancel');
            else {hideOverlay();key('Escape')}
            return;
          }
          if(trackPanel()){
            if(command==='select'){
              if(shown(active()) && active().matches('button'))active().click();
              else focus($('plextrackchoices')?.querySelector('button'));
            } else if(['up','down','left','right'].includes(command)){
              const choices=Array.from($('plextrackchoices')?.querySelectorAll('button')||[]).filter(shown);
              const at=choices.indexOf(active());
              if(choices.length)focus(choices[Math.max(0,Math.min(choices.length-1,
                (at<0?0:at)+(command==='up'||command==='left'?-1:1)))]);
            } else if(command==='options')click('plextrackcancel');
            return;
          }
          if(command==='select'){
            if(!overlayVisible){revealOverlay(true);return}
            if(shown(active()) && active().matches('button'))active().click();
            else revealOverlay(true);
            revealOverlay(false);return;
          }
          if(command==='up'||command==='down'){
            if(!overlayVisible)revealOverlay(true);
            else moveControls(command);
            return;
          }
          if(command==='left'||command==='right'){
            if(!overlayVisible){
              transportClick(tablo()?(command==='left'?'tablodvrback':'tablodvrforward'):
                (command==='left'?'plexback':'plexforward'));
            }else if(active()?.matches('input[type="range"]')){
              const input=active(),direction=command==='right'?1:-1;
              input.value=String(Math.max(0,Math.min(1000,Number(input.value)+direction*40)));
              input.dispatchEvent(new Event('change',{bubbles:true}));
              revealOverlay(false);
            }else moveControls(command);
            return;
          }
          if(command==='options' && plex()){
            revealOverlay(false);
            if(!click('plexchaptersbutton'))click('plexaudiobutton');
            return;
          }
          if(command==='live' && tablo()){click('tablodvrlive');return}
          if(command==='rail'||command==='guide'){revealOverlay(true);return}
          return;
        }
        overlayVisible=false;
        clearOverlayTimer();
        if(command==='rail'){
          const rail=['opensearch','openfilters','opensettings'];
          const index=rail.indexOf(active()?.id);
          focus($(rail[index<0?0:(index+1)%3]));return;
        }
        if(command==='guide'){guideFocus();return}
        if(command==='options'){chooseDetailButton();return}
        if(command==='back'){key('Escape');return}
        if(command==='live'){click('now');return}
        if(command==='select'){
          if(shown(active()) &&
             active().matches('button,a[href],[role="button"],summary'))active().click();
          else guideFocus();
          return;
        }
        if(['up','down','left','right'].includes(command))guideDirection(command);
      };
      // If focus is stolen by the video element, don't get stuck pressing
      // the D-pad repeatedly. Restore a guide target on next controller press.
      document.addEventListener('DOMContentLoaded',()=>guideFocus(),{once:true});
      window.addEventListener('pageshow',()=>guideFocus());

    })();
    """#
}
