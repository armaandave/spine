import SwiftUI

struct GameProgressFields: View {
    @Binding var draft: GameProgressDraft
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Total hours this playthrough").font(.subheadline.weight(.semibold))
            HStack {
                TextField("Hours", text: $draft.hours).accessibilityLabel("Total hours this playthrough")
                Text("h")
                TextField("Minutes", text: $draft.minutes).accessibilityLabel("Minutes")
                Text("m")
                Button("Clear time") { draft.hours = ""; draft.minutes = "" }
                    .font(.caption)
                    .buttonStyle(.borderless)
            }
            HStack {
                TextField("Percentage", text: $draft.percentage).accessibilityLabel("Percentage")
                Text("%")
                Button("Clear percentage") { draft.percentage = "" }.font(.caption).buttonStyle(.borderless)
            }
            Text("Blank means unknown. Zero means zero. Time replaces the total; it does not add a session.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .keyboardType(.numberPad)
        .focused($focused)
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { focused = false }
            }
        }
    }
}

struct GamePlaythroughEditor: View {
    @Environment(\.dismiss) private var dismiss
    let playthrough: GamePlaythroughState
    let onSave: (GamePlaythroughWriteRequest) async -> Bool
    let onAction: (String) async -> Bool
    let onDelete: () async -> Bool
    let onFinish: () -> Void
    let errorMessage: () -> String?
    @State private var draft: GameProgressDraft
    private let initialDraft: GameProgressDraft
    @State private var startDate: Date
    @State private var endDate: Date
    @State private var hasStartDate: Bool
    @State private var saving = false
    @State private var error: String?
    @State private var confirmation: String?

    init(playthrough: GamePlaythroughState, onSave: @escaping (GamePlaythroughWriteRequest) async -> Bool,
         onAction: @escaping (String) async -> Bool, onDelete: @escaping () async -> Bool,
         onFinish: @escaping () -> Void, errorMessage: @escaping () -> String?) {
        self.playthrough = playthrough
        self.onSave = onSave
        self.onAction = onAction
        self.onDelete = onDelete
        self.onFinish = onFinish
        self.errorMessage = errorMessage
        let draft = GameProgressDraft(totalMinutes: playthrough.totalMinutes, percentage: playthrough.percentage)
        initialDraft = draft
        _draft = State(initialValue: draft)
        _startDate = State(initialValue: CalendarDateCodec.date(from: playthrough.startDate) ?? Date())
        _endDate = State(initialValue: CalendarDateCodec.date(from: playthrough.endDate) ?? Date())
        _hasStartDate = State(initialValue: playthrough.startDate != nil)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Progress") { GameProgressFields(draft: $draft) }
                Section("Playthrough dates") {
                    if playthrough.origin != "live" { Toggle("Known start date", isOn: $hasStartDate) }
                    if hasStartDate { DatePicker("Started", selection: $startDate, in: ...Date(), displayedComponents: .date) }
                    if playthrough.status == "Dropped" {
                        DatePicker("Dropped", selection: $endDate, in: ...Date(), displayedComponents: .date)
                    }
                }
                if let error { Section { Text(error).foregroundStyle(.red) } }
                if playthrough.isUnfinished {
                    Section("Status") {
                        Button(playthrough.status == "Paused" ? "Resume" : "Pause") {
                            run { await onAction(playthrough.status == "Paused" ? "resume" : "pause") }
                        }
                        Button("Finish", action: onFinish)
                        Button("Drop", role: .destructive) { confirmation = "drop" }
                    }
                    Section {
                        Button("Restart from Beginning") { confirmation = "restart" }
                        Button("Delete Playthrough", role: .destructive) { confirmation = "delete" }
                    }
                } else if playthrough.status == "Dropped" {
                    Section { Button("Delete Playthrough", role: .destructive) { confirmation = "delete" } }
                }
            }
            .disabled(saving)
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle(playthrough.status == "Dropped" ? "Edit Playthrough" : "Update Progress")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        do {
                            let start = hasStartDate ? CalendarDateCodec.string(from: startDate) : nil
                            let end = playthrough.status == "Dropped" ? CalendarDateCodec.string(from: endDate) : nil
                            var request = try draft.request(comparedTo: initialDraft,
                                startDate: start == playthrough.startDate ? nil : start,
                                endDate: end == playthrough.endDate ? nil : end)
                            request.includesStartDate = start != playthrough.startDate
                            run { await onSave(request) }
                        } catch { self.error = error.localizedDescription }
                    }.disabled(saving)
                }
            }
            .overlay { if saving { ProgressView() } }
            .confirmationDialog(confirmationTitle, isPresented: Binding(get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }), titleVisibility: .visible) {
                Button(confirmation == "restart" ? "Restart" : confirmation == "delete" ? "Delete Playthrough" : "Drop", role: .destructive) {
                    let action = confirmation ?? ""
                    confirmation = nil
                    run { action == "delete" ? await onDelete() : await onAction(action) }
                }
                Button("Cancel", role: .cancel) { confirmation = nil }
            } message: { Text(confirmationMessage) }
        }
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(saving)
    }

    private var confirmationTitle: String {
        switch confirmation {
        case "restart": "Restart from Beginning?"
        case "delete": "Delete this playthrough?"
        default: "Drop this playthrough?"
        }
    }
    private var confirmationMessage: String {
        switch confirmation {
        case "restart": "The previous attempt and its progress will remain in history as Dropped."
        case "delete": "This deletes this attempt and all its progress. Earlier attempts and logs stay unchanged."
        default: "Your progress will be kept in history."
        }
    }
    private func run(_ operation: @escaping () async -> Bool) {
        guard !saving else { return }
        saving = true
        error = nil
        Task {
            if await operation() { dismiss() }
            else { error = errorMessage() ?? "Could not save. Try again." }
            saving = false
        }
    }
}

struct GameActionSheet: View {
    let status: String?
    let game: GameTrackingState?
    let isSaving: Bool
    let errorMessage: String?
    let onAction: (MediaDetailQuickAction) async -> Void
    let onLog: () -> Void
    let onProgress: () -> Void
    let onRemove: () async -> Void
    @State private var confirmDrop = false
    @State private var confirmRemove = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    statusButton("Playing", symbol: "play.fill", action: .currently, selected: status == "In progress", disabled: game?.hasPlayingPlaythrough == true)
                    statusButton("Planning", symbol: "bookmark", action: .planning, selected: status == "Planning", disabled: status == "Planning" || game?.hasLivePlaythrough == true)
                    statusButton("Paused", symbol: "pause.fill", action: .paused, selected: status == "Paused")
                    Button { if game?.hasLivePlaythrough == true { confirmDrop = true } else { Task { await onAction(.stopped) } } } label: {
                        Label(status == "Dropped" ? "Dropped ✓" : "Dropped", systemImage: "stop.fill")
                    }.disabled(status == "Dropped")
                    if status == "Paused" { Button("Resume") { Task { await onAction(.currently) } } }
                    Button("Log Completion", systemImage: "square.and.pencil", action: onLog)
                }
                if game?.hasLivePlaythrough == true {
                    Text("Pause, drop, or delete the unfinished playthrough before choosing Planning.").font(.caption).foregroundStyle(.secondary)
                }
                if game?.canUpdateProgress == true { Button("Update Progress", systemImage: "slider.horizontal.3", action: onProgress) }
                if status != nil {
                    Section {
                        Button("Remove from Library", role: .destructive) { confirmRemove = true }
                            .disabled(game?.canRemoveTracking == false)
                        if game?.canRemoveTracking == false {
                            Text("Delete playthroughs and completion logs first. Custom lists stay unchanged.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if let errorMessage { Text(errorMessage).foregroundStyle(.red) }
            }
            .navigationTitle("Track this game").navigationBarTitleDisplayMode(.inline)
            .disabled(isSaving)
            .confirmationDialog("Drop this playthrough?", isPresented: $confirmDrop, titleVisibility: .visible) {
                Button("Drop", role: .destructive) { Task { await onAction(.stopped) } }
            } message: { Text("Your progress will be kept in history.") }
            .confirmationDialog("Remove from Library?", isPresented: $confirmRemove, titleVisibility: .visible) {
                Button("Remove", role: .destructive) { Task { await onRemove() } }
            } message: { Text("This clears current rating and heart. Custom lists stay unchanged.") }
        }.preferredColorScheme(.dark)
    }

    private func statusButton(_ title: String, symbol: String, action: MediaDetailQuickAction, selected: Bool, disabled: Bool? = nil) -> some View {
        Button { Task { await onAction(action) } } label: { Label(selected ? "\(title) ✓" : title, systemImage: symbol) }
            .disabled(disabled ?? selected)
    }
}

struct GamePlayHistorySection: View {
    let game: GameTrackingState
    let onEdit: (GamePlaythroughState) -> Void
    let onDeleteUndated: () -> Void
    @State private var confirmUndated = false

    var body: some View {
        if !game.playHistory.isEmpty || game.undatedCompletion != nil || game.importedLifetimeMinutes != nil {
            VStack(alignment: .leading, spacing: 14) {
                Text("Play History").font(.title3.weight(.bold))
                ForEach(game.playHistory.filter { $0.status == "Dropped" }) { attempt in
                    Button { onEdit(attempt) } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Dropped" + (attempt.progress.summary.isEmpty ? "" : " · \(attempt.progress.summary)"))
                                Text([attempt.startDate, attempt.endDate].compactMap { $0 }.joined(separator: " → "))
                                    .font(.caption).foregroundStyle(.white.opacity(0.65))
                            }
                            Spacer()
                            Image(systemName: "pencil")
                        }.padding(14).background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 14))
                    }.buttonStyle(.plain).accessibilityLabel("Edit dropped playthrough \(attempt.id)")
                }
                if game.undatedCompletion != nil {
                    HStack {
                        Text("Completed — date unknown")
                        Spacer()
                        Button(role: .destructive) { confirmUndated = true } label: { Image(systemName: "trash") }
                            .accessibilityLabel("Delete undated completion")
                    }
                }
                if let minutes = game.importedLifetimeMinutes {
                    Text("Imported lifetime playtime: \(GameProgressValues.timeLabel(minutes))")
                        .font(.subheadline).foregroundStyle(.white.opacity(0.65))
                    Text("Separate from playthrough hours.").font(.caption).foregroundStyle(.white.opacity(0.65))
                }
            }
            .foregroundStyle(.white)
            .confirmationDialog("Delete undated completion?", isPresented: $confirmUndated, titleVisibility: .visible) {
                Button("Delete", role: .destructive, action: onDeleteUndated)
            } message: { Text("Current playthroughs and diary logs stay unchanged.") }
        }
    }
}
