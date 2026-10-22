import SwiftUI

struct GameProgressFields: View {
    @Binding var draft: GameProgressDraft
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            field("Hours played", text: hours, unit: "h")
            field("Percent complete", text: $draft.percentage, unit: "%")
            Text("Leave either blank if you don't know.")
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

    /// Whole hours only: typing hours drops any minutes left over from an import.
    private var hours: Binding<String> {
        Binding(get: { draft.hours }, set: { draft.hours = $0; draft.minutes = "" })
    }

    private func field(_ title: String, text: Binding<String>, unit: String) -> some View {
        HStack {
            Text(title)
            Spacer()
            TextField("–", text: text)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 90)
                .accessibilityLabel(title)
            Text(unit).foregroundStyle(.secondary)
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
            .navigationTitle("Edit Playthrough")
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

/// The plus button's sheet for a game: the book sheet's grid, sized to its buttons.
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
    @State private var contentHeight: CGFloat = 320

    private var isLive: Bool { game?.hasLivePlaythrough == true }

    private var playingTitle: String {
        switch status {
        case "Paused": "Resume Playing"
        case "In progress": "Playing"
        default: "Start Playing"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Track this game").font(.title3.weight(.bold))
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                actionButton(status == "Planning" ? "Planning" : "Add to Planning", systemImage: "bookmark",
                             disabled: status == "Planning" || isLive) { await onAction(.planning) }
                actionButton(playingTitle, systemImage: "play.fill",
                             disabled: game?.hasPlayingPlaythrough == true) { await onAction(.currently) }
                if isLive {
                    actionButton("Update Progress", systemImage: "slider.horizontal.3") { onProgress() }
                    actionButton("Pause", systemImage: "pause.fill", disabled: status == "Paused") { await onAction(.paused) }
                    actionButton("Drop", systemImage: "xmark") { confirmDrop = true }
                } else {
                    actionButton("Paused", systemImage: "pause.fill", disabled: status == "Paused") { await onAction(.paused) }
                    actionButton("Dropped", systemImage: "xmark", disabled: status == "Dropped") { await onAction(.stopped) }
                }
                actionButton("Log Completion", systemImage: "square.and.pencil") { onLog() }
                if !isLive, game?.canUpdateProgress == true {
                    actionButton("Update Progress", systemImage: "slider.horizontal.3") { onProgress() }
                }
            }
            if isLive {
                Text("Pause, drop or finish this playthrough before moving the game to Planning.")
                    .font(.caption.weight(.medium)).foregroundStyle(.secondary)
            }
            if status != nil {
                Button(role: .destructive) { confirmRemove = true } label: {
                    Label("Remove from Library", systemImage: "trash").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .disabled(isSaving || game?.canRemoveTracking == false)
                if game?.canRemoveTracking == false {
                    Text("Delete playthroughs and completion logs first. Custom lists stay unchanged.")
                        .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                }
            }
            if let errorMessage {
                Text(errorMessage)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.red.opacity(0.92))
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 26)
        .padding(.bottom, 18)
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
        .frame(maxHeight: .infinity, alignment: .top)
        .presentationDetents([.height(contentHeight)])
        .presentationDragIndicator(.visible)
        .presentationBackground(.regularMaterial)
        .confirmationDialog("Drop this playthrough?", isPresented: $confirmDrop, titleVisibility: .visible) {
            Button("Drop", role: .destructive) { Task { await onAction(.stopped) } }
        } message: { Text("Your progress will be kept in history.") }
        .confirmationDialog("Remove from Library?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove", role: .destructive) { Task { await onRemove() } }
        } message: { Text("This clears current rating and heart. Custom lists stay unchanged.") }
    }

    private func actionButton(_ title: String, systemImage: String, disabled: Bool = false,
                              action: @escaping () async -> Void) -> some View {
        Button { Task { await action() } } label: {
            Label(title, systemImage: systemImage)
                .font(.subheadline.weight(.bold))
                .frame(maxWidth: .infinity, minHeight: 46)
        }
        .buttonStyle(.bordered)
        .tint(.white.opacity(0.84))
        .disabled(isSaving || disabled)
    }
}

/// Update Progress for a game, laid out like the book progress panel: one number,
/// hours or percent, against the last saved value.
struct GameProgressSheet: View {
    private enum Mode: String, CaseIterable, Identifiable {
        case hours = "Hours"
        case percent = "Percent"
        var id: String { rawValue }
    }

    private enum PendingAction: Identifiable {
        case drop, restart, delete
        var id: Self { self }
    }

    @Environment(\.dismiss) private var dismiss
    let detail: MediaDetail
    let playthrough: GamePlaythroughState
    let onSave: (GamePlaythroughWriteRequest) async -> Bool
    let onAction: (String) async -> Bool
    let onDelete: () async -> Bool
    let onFinish: () -> Void
    let onEditDates: () -> Void
    let errorMessage: () -> String?
    @State private var mode: Mode
    @State private var input = ""
    @State private var saving = false
    @State private var error: String?
    @State private var pending: PendingAction?
    @State private var contentHeight: CGFloat = 300
    @FocusState private var focused: Bool

    init(detail: MediaDetail, playthrough: GamePlaythroughState,
         onSave: @escaping (GamePlaythroughWriteRequest) async -> Bool, onAction: @escaping (String) async -> Bool,
         onDelete: @escaping () async -> Bool, onFinish: @escaping () -> Void, onEditDates: @escaping () -> Void,
         errorMessage: @escaping () -> String?) {
        self.detail = detail
        self.playthrough = playthrough
        self.onSave = onSave
        self.onAction = onAction
        self.onDelete = onDelete
        self.onFinish = onFinish
        self.onEditDates = onEditDates
        self.errorMessage = errorMessage
        let startsWithPercent = playthrough.totalMinutes == nil && playthrough.percentage != nil
        _mode = State(initialValue: startsWithPercent ? .percent : .hours)
    }

    private var lastValue: Int? {
        mode == .hours ? playthrough.totalMinutes.map { $0 / 60 } : playthrough.percentage
    }

    private var lastText: String {
        switch mode {
        case .hours: playthrough.totalMinutes.map(GameProgressValues.timeLabel) ?? "--"
        case .percent: playthrough.percentage.map { "\($0)%" } ?? "--"
        }
    }

    private var newValue: Int? { Int(input) }

    private var validation: String? {
        guard let newValue, mode == .percent, newValue > 100 else { return nil }
        return "Enter a percentage from 0 to 100."
    }

    var body: some View {
        VStack(spacing: 18) {
            header
            HStack(spacing: 8) {
                lastStat
                nowStat
            }
            if let message = validation ?? error {
                Text(message)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            saveButton
        }
        .padding(.horizontal, 16)
        .padding(.top, 22)
        .padding(.bottom, 16)
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
        .frame(maxHeight: .infinity, alignment: .top)
        .disabled(saving)
        .presentationDetents([.height(contentHeight)])
        .presentationDragIndicator(.visible)
        .presentationBackground(Color(uiColor: .secondarySystemBackground))
        .interactiveDismissDisabled(saving)
        .onAppear { focused = true }
        .onChange(of: mode) { input = "" }
        .onChange(of: input) { _, value in
            let digits = String(value.filter(\.isNumber).prefix(mode == .hours ? 5 : 3))
            if digits != value { input = digits }
        }
        .confirmationDialog(confirmationTitle, isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } }),
                            titleVisibility: .visible) {
            Button(confirmationButton, role: .destructive) {
                let action = pending
                pending = nil
                switch action {
                case .drop: run { await onAction("drop") }
                case .restart: run { await onAction("restart") }
                case .delete: run { await onDelete() }
                case nil: break
                }
            }
            Button("Cancel", role: .cancel) { pending = nil }
        } message: { Text(confirmationMessage) }
    }

    private var header: some View {
        HStack(spacing: 12) {
            MediaArtwork(url: detail.displayPosterURL, title: detail.title, slot: .searchRow,
                         mediaType: detail.ref.mediaType, orientation: detail.posterOrientation)
                .shadow(color: .black.opacity(0.22), radius: 8, y: 4)
            VStack(alignment: .leading, spacing: 6) {
                Text("Update progress").font(.headline.weight(.semibold))
                Picker("Progress as", selection: $mode) {
                    ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 180)
            }
            Spacer(minLength: 0)
            Menu { menuItems } label: {
                Image(systemName: "ellipsis").font(.system(size: 14, weight: .semibold))
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.circle)
            .accessibilityLabel("Playthrough actions")
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.circle)
            .accessibilityLabel("Close")
        }
    }

    @ViewBuilder
    private var menuItems: some View {
        if playthrough.status == "In progress" {
            Button("Pause", systemImage: "pause.fill") { run { await onAction("pause") } }
        }
        if playthrough.status == "Paused" {
            Button("Resume", systemImage: "play.fill") { run { await onAction("resume") } }
        }
        if playthrough.isUnfinished {
            Button("Finish & Log", systemImage: "checkmark", action: onFinish)
            Button("Drop", systemImage: "xmark") { pending = .drop }
            Button("Restart from Beginning", systemImage: "arrow.counterclockwise") { pending = .restart }
        }
        Button("Edit Dates", systemImage: "calendar", action: onEditDates)
        if playthrough.isUnfinished {
            Button("Delete Playthrough", systemImage: "trash", role: .destructive) { pending = .delete }
        }
    }

    private var lastStat: some View {
        VStack(spacing: 6) {
            Text("Last").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Text(lastText)
                .font(.title3.monospacedDigit().weight(.semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var nowStat: some View {
        VStack(spacing: 6) {
            Text("Now").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack(spacing: 2) {
                TextField(lastValue.map(String.init) ?? "0", text: $input)
                    .keyboardType(.numberPad)
                    .focused($focused)
                    .multilineTextAlignment(.center)
                    .font(.title3.monospacedDigit().weight(.semibold))
                    .fixedSize()
                    .accessibilityLabel(mode == .hours ? "Hours played" : "Percent complete")
                Text(mode == .hours ? "h" : "%")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture { focused = true }
    }

    private var saveButton: some View {
        let enabled = newValue != nil && validation == nil && !saving
        return Button(action: save) {
            Group {
                if saving { ProgressView() } else { Text("Save Progress").font(.headline.weight(.semibold)) }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 50)
            .foregroundStyle(enabled ? .black : .white.opacity(0.48))
            .background(enabled ? Color.white : Color.white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }

    private func save() {
        guard let value = newValue, validation == nil else { return }
        var request = GamePlaythroughWriteRequest(progressedOn: CalendarDateCodec.string(from: Date()))
        switch mode {
        case .hours:
            request.totalMinutes = value * 60
            request.includesMinutes = true
        case .percent:
            request.percentage = value
            request.includesPercentage = true
        }
        run { await onSave(request) }
    }

    private var confirmationTitle: String {
        switch pending {
        case .restart: "Restart from Beginning?"
        case .delete: "Delete this playthrough?"
        default: "Drop this playthrough?"
        }
    }
    private var confirmationButton: String {
        switch pending {
        case .restart: "Restart"
        case .delete: "Delete Playthrough"
        default: "Drop"
        }
    }
    private var confirmationMessage: String {
        switch pending {
        case .restart: "The previous attempt and its progress will remain in history as Dropped."
        case .delete: "This deletes this attempt and all its progress. Earlier attempts and logs stay unchanged."
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
