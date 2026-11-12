import Foundation

/// Offline, fictional guide data solely for automated simulator screenshots.
/// Enabled only by the explicit --visual-preview launch argument.
enum TVVisualPreview {
    static var enabled: Bool {
        ProcessInfo.processInfo.arguments.contains("--visual-preview")
    }

    static func channels() -> [GuideChannel] {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let calendar = Calendar.current
        let now = Date()
        let hour = calendar.dateInterval(of: .hour, for: now)?.start ?? now
        let halfHour = calendar.component(.minute, from: now) >= 30 ? 30 : 0
        let base = calendar.date(byAdding: .minute, value: halfHour, to: hour) ?? hour

        let stations: [(String, String, String, String)] = [
            ("4.1", "WPRV", "Preview One", "tablo"),
            ("7.1", "KTVM", "Metro Seven", "tablo"),
            ("9.1", "WSCI", "Science Nine", "tablo"),
            ("22", "COMEDY", "Comedy Network", "philo"),
            ("24", "HOME", "Home & Garden", "philo"),
            ("35", "MOVIES", "Movie Channel", "philo"),
            ("41", "HISTORY", "History Network", "philo"),
            ("48", "NATURE", "Nature TV", "philo"),
            ("51", "RETRO", "Retro Classics", "philo"),
            ("62", "TRAVEL", "Travel World", "philo"),
            ("73", "DRAMA", "Drama Central", "philo")
        ]
        let titles: [[(String, String)]] = [
            [("Morning Report", "Headlines and weather"), ("City Stories", "Season 3 · Episode 2"), ("The Daily Review", "Highlights")],
            [("The Mountain House", "A New Beginning"), ("Local News", "News at Noon"), ("The Weekend", "Episode 5")],
            [("Space Explorers", "Beyond the Moon"), ("Planet Earth", "Wild Places"), ("Science Today", "Discovery")],
            [("The Office", "Company Picnic"), ("Classic Comedy", "Best of the 90s"), ("Studio Laughs", "Episode 8")],
            [("Dream Home", "The Reveal"), ("Garden Rescue", "Spring Refresh"), ("Room by Room", "New Design")],
            [("Saturday Matinee", "An Adventure Begins"), ("Cinema Classics", "Feature Film"), ("After Hours", "Drama")],
            [("Hidden History", "Lost Cities"), ("Ancient Worlds", "Part Two"), ("Time Travelers", "Exploring History")],
            [("Ocean Life", "Deep Blue"), ("Wild Earth", "Mountain Range"), ("Animal Stories", "Episode 4")],
            [("Vintage TV", "The Reunion"), ("Family Favorites", "Episode 9"), ("The Good Old Days", "Memories")],
            [("Coastal Escapes", "North Shore"), ("Great Journeys", "Across the Country"), ("Food Trails", "Episode 7")],
            [("The Next Chapter", "A New Mystery"), ("City Drama", "The Finale"), ("The Long Road", "Part One")]
        ]

        var channelPayloads: [[String: Any]] = []
        for (index, station) in stations.enumerated() {
            var programs: [[String: Any]] = []
            for slot in 0..<5 {
                let startOffset = slot == 0 ? -30 : (slot == 1 ? 90 : 90 + (slot - 1) * 60)
                let duration = slot == 0 ? 120 : 60
                let start = base.addingTimeInterval(TimeInterval(startOffset * 60))
                let stop = start.addingTimeInterval(TimeInterval(duration * 60))
                let chosen = titles[index][slot % titles[index].count]
                var program: [String: Any] = [
                    "title": chosen.0,
                    "subtitle": chosen.1,
                    "start": formatter.string(from: start),
                    "stop": formatter.string(from: stop),
                    "description": "Fictional program used to review the Apple TV guide layout.",
                    "program_kind": "episode"
                ]
                if (index == 3 || index == 5) && slot == 0 {
                    program["plex"] = ["rating_key": "12345"]
                }
                programs.append(program)
            }
            channelPayloads.append([
                "id": "preview-\(index)",
                "label": station.2,
                "network": station.1,
                "number": station.0,
                "source": station.3,
                "favorite": false,
                "programs": programs
            ])
        }
        do {
            let data = try JSONSerialization.data(withJSONObject: ["channels": channelPayloads])
            return try JSONDecoder().decode(GuideResponse.self, from: data).channels
        } catch {
            assertionFailure("Failed to construct visual preview fixture: \(error)")
            return []
        }
    }
}
