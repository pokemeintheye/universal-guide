import SwiftUI

/// Lightweight filters and search for the landscape iPhone controller test.
struct PhoneSidebar: View {
    @Binding var searchText: String
    @Binding var sourceFilter: String
    @Binding var plexOnly: Bool
    let onSettings: () -> Void
    let onClose: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section("Find a show or channel") {
                    TextField("Search title or network", text: $searchText)
                        .textInputAutocapitalization(.never)
                    if !searchText.isEmpty {
                        Button("Clear search") { searchText = "" }
                    }
                }
                Section("Guide sources") {
                    Picker("Source", selection: $sourceFilter) {
                        Text("All").tag("All")
                        Text("Philo").tag("philo")
                        Text("Tablo").tag("tablo")
                        Text("Other guide").tag("xmltv")
                    }
                    .pickerStyle(.segmented)
                    Toggle("Only channels with Plex matches", isOn: $plexOnly)
                }
                Section("App") {
                    Button("Server & Controller Settings", action: onSettings)
                }
                Section {
                    Text("Backbone: B closes details, A selects a program. Search results are filtered locally from the existing eight-hour guide window.")
                        .foregroundStyle(.secondary)
                        .font(.footnote)
                }
            }
            .navigationTitle("Guide Menu")
            .toolbar { Button("Done", action: onClose) }
        }
    }
}
