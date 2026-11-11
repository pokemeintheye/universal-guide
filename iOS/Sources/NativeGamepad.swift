import Foundation
import GameController

// Same native SwiftUI guide and player used on Apple TV; no WKWebView.
// iPhone's Backbone sends commands to those shared views for testing.
@MainActor final class NativeGamepad: ObservableObject {
    private var observers: [NSObjectProtocol] = []
    init() {
        observers.append(NotificationCenter.default.addObserver(
            forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.connect() }
            })
        observers.append(NotificationCenter.default.addObserver(
            forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.connect() }
            })
        connect()
    }
    deinit { observers.forEach(NotificationCenter.default.removeObserver) }

    private func connect() {
        guard let pad = GCController.controllers().compactMap({$0.extendedGamepad}).first else {return}
        pad.dpad.up.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadDirection,object:"up") }}
        pad.dpad.down.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadDirection,object:"down") }}
        pad.dpad.left.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadDirection,object:"left") }}
        pad.dpad.right.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadDirection,object:"right") }}
        pad.buttonA.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadSelect,object:nil) }}
        pad.buttonB.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadBack,object:nil) }}
        pad.buttonY.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadPlayPause,object:nil) }}
        pad.leftShoulder.pressedChangedHandler = { _,_,down in if down {
            NotificationCenter.default.post(name: .ugGamepadDirection,object:"rail") }}
    }
}
