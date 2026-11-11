import SwiftUI

@main struct UniversalGuidePhoneApp: App {
    @StateObject private var gamepad = NativeGamepad()
    var body: some Scene {
        WindowGroup {
            TVGuideScreen()
                .environmentObject(gamepad)
        }
    }
}
