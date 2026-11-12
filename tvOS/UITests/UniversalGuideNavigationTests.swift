import XCTest

final class UniversalGuideNavigationTests: XCTestCase {
    func testDirectionalFocusAndReturnToGuide() throws {
        let app = XCUIApplication()
        app.launchArguments = ["--navigation-test"]
        app.launch()
        let first = app.buttons["guide.program.0.0"]
        XCTAssertTrue(first.waitForExistence(timeout: 30),
                      "The full interactive guide did not render its first program tile")
        // A real tvOS remote event, not a screenshot-only approximation.
        XCUIRemote.shared.press(.down)
        XCUIRemote.shared.press(.right)
        let focused = app.buttons.matching(NSPredicate(format: "hasFocus == true"))
        XCTAssertGreaterThan(focused.count, 0, "No focusable control after remote navigation")
        let screenshot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = "Guide after Down and Right"
        attachment.lifetime = .keepAlways
        add(attachment)
        XCUIRemote.shared.press(.select)
        XCUIRemote.shared.press(.menu)
        XCTAssertTrue(first.waitForExistence(timeout: 10),
                      "Back navigation did not return to the guide")
    }
}
