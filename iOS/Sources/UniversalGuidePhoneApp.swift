import SwiftUI

@main struct UniversalGuidePhoneApp: App {
    @StateObject private var gamepad = NativeGamepad()
    init() {
        // Carry over the user's existing Unraid address from the former
        // iPhone web shell into the shared native guide.
        let prefs = UserDefaults.standard
        if (prefs.string(forKey: "guide.serverURL") ?? "").isEmpty,
           let legacy = prefs.string(forKey: "phone.unraidURL"), !legacy.isEmpty {
            prefs.set(legacy, forKey: "guide.serverURL")
        }
    }
    var body: some Scene {
        WindowGroup {
            TVGuideScreen()
                .environmentObject(gamepad)
        }
    }
}
