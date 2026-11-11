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

    // GameController callbacks may arrive off the main thread; SwiftUI
    // view state must receive every gamepad event on the main run loop.
    nonisolated private static func emit(_ name: Notification.Name, _ payload: String? = nil) {
        DispatchQueue.main.async {
            NotificationCenter.default.post(name: name, object: payload)
        }
    }
    private func connect() {
        guard let pad = GCController.controllers().compactMap({$0.extendedGamepad}).first else {return}
        pad.dpad.up.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "up") }}
        pad.dpad.down.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "down") }}
        pad.dpad.left.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "left") }}
        pad.dpad.right.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "right") }}
        pad.buttonA.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadSelect) }}
        pad.buttonB.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadBack) }}
        pad.buttonY.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadPlayPause) }}
        // X reaches the bottom information panel's Watch buttons.
        pad.buttonX.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "details") }}
        pad.leftShoulder.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "rail") }}
        pad.rightShoulder.pressedChangedHandler = { _,_,down in if down {
            Self.emit(.ugGamepadDirection, "guide") }}
    }
}
