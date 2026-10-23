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
    let onAction: (String) -> Void
    let onDelete: () -> Void
    let onFinish: () -> Void
    let errorMessage: () -> String?
    @State private var draft: GameProgressDraft
    private let initialDraft: GameProgressDraft
    @State private var startDate: Date
    @State private var endDate: Date
    @State private var hasStartDate: Bool
    @State private var saving = false
    @State private var isClosing = false
    @State private var error: String?
    @State private var confirmation: String?

    init(playthrough: GamePlaythroughState, onSave: @escaping (GamePlaythroughWriteRequest) async -> Bool,
         onAction: @escaping (String) -> Void, onDelete: @escaping () -> Void,
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
                            choose { onAction(playthrough.status == "Paused" ? "resume" : "pause") }
                        }
                        Button("Finish", action: onFinish)
                        Button("Drop", role: .destructive) { confirmation = "drop" }
                    }
                    .disabled(saving)
                    Section {
                        Button("Restart from Beginning") { confirmation = "restart" }
                        Button("Delete Playthrough", role: .destructive) { confirmation = "delete" }
                    }
                    .disabled(saving)
                } else if playthrough.status == "Dropped" {
                    Section { Button("Delete Playthrough", role: .destructive) { confirmation = "delete" } }
                        .disabled(saving)
                }
            }
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
                    choose { action == "delete" ? onDelete() : onAction(action) }
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
    /// Status changes apply behind the sheet, so it closes on the tap. A second tap while it slides away is ignored.
    private func choose(_ choice: () -> Void) {
        guard !saving, !isClosing else { return }
        isClosing = true
        choice()
        dismiss()
    }

    private func run(_ operation: @escaping () async -> Bool) {
        guard !saving, !isClosing else { return }
        saving = true
        error = nil
        Task {
            if await operation() { dismiss() }
            else { error = errorMessage() ?? "Could not save. Try again." }
            saving = false
        }
    }
}

/// Sizes a sheet to its list's rows, so it opens with no empty space and has no taller
/// height to drag into. Apply it to the List inside the sheet.
struct FitsSheetToContent: ViewModifier {
    @State private var height: CGFloat = 320

    func body(content: Content) -> some View {
        content
            .listSectionSpacing(.compact)
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                geometry.contentSize.height + geometry.contentInsets.top + geometry.contentInsets.bottom
            } action: { _, newHeight in
                height = newHeight
            }
            .presentationDetents([.height(height)])
    }
}

extension View {
    func fitsSheetToContent() -> some View { modifier(FitsSheetToContent()) }
}

struct GameActionSheet: View {
    /// What the sheet showed when a choice was made. The rows keep it while the sheet slides away, so neither
    /// they nor the detent change under the finger, and a second tap is ignored.
    private struct Snapshot {
        let status: String?
        let game: GameTrackingState?
    }

    let status: String?
    let game: GameTrackingState?
    let onAction: (MediaDetailQuickAction) -> Void
    let onLog: () -> Void
    let onProgress: () -> Void
    let onRemove: () -> Void
    @State private var committed: Snapshot?
    @State private var confirmDrop = false
    @State private var confirmRemove = false

    private var shown: Snapshot { committed ?? Snapshot(status: status, game: game) }

    var body: some View {
        let status = shown.status
        let game = shown.game
        NavigationStack {
            List {
                Section {
                    statusButton("Playing", symbol: "play.fill", action: .currently, selected: status == "In progress", disabled: game?.hasPlayingPlaythrough == true)
                    statusButton("Planning", symbol: "bookmark", action: .planning, selected: status == "Planning", disabled: status == "Planning" || game?.hasLivePlaythrough == true)
                    statusButton("Paused", symbol: "pause.fill", action: .paused, selected: status == "Paused")
                    Button { if game?.hasLivePlaythrough == true { confirmDrop = true } else { commit { onAction(.stopped) } } } label: {
                        Label(status == "Dropped" ? "Dropped ✓" : "Dropped", systemImage: "stop.fill")
                    }.disabled(status == "Dropped")
                    if status == "Paused" { Button("Resume") { commit { onAction(.currently) } } }
                    Button("Log Completion", systemImage: "square.and.pencil") { commit(onLog) }
                }
                if game?.hasLivePlaythrough == true {
                    Text("Pause, drop, or delete the unfinished playthrough before choosing Planning.").font(.caption).foregroundStyle(.secondary)
                }
                if game?.canUpdateProgress == true { Button("Update Progress", systemImage: "slider.horizontal.3") { commit(onProgress) } }
                if status != nil {
                    Section {
                        Button("Remove from Library", role: .destructive) { confirmRemove = true }
                            .disabled(game?.canRemoveTracking == false)
                        if game?.canRemoveTracking == false {
                            Text("Delete playthroughs and completion logs first. Custom lists stay unchanged.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .fitsSheetToContent()
            .navigationTitle("Track this game").navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Drop this playthrough?", isPresented: $confirmDrop, titleVisibility: .visible) {
                Button("Drop", role: .destructive) { commit { onAction(.stopped) } }
            } message: { Text("Your progress will be kept in history.") }
            .confirmationDialog("Remove from Library?", isPresented: $confirmRemove, titleVisibility: .visible) {
                Button("Remove", role: .destructive) { commit(onRemove) }
            } message: { Text("This clears current rating and heart. Custom lists stay unchanged.") }
        }.preferredColorScheme(.dark)
    }

    private func commit(_ choice: () -> Void) {
        guard committed == nil else { return }
        committed = Snapshot(status: status, game: game)
        choice()
    }

    private func statusButton(_ title: String, symbol: String, action: MediaDetailQuickAction, selected: Bool, disabled: Bool? = nil) -> some View {
        Button { commit { onAction(action) } } label: { Label(selected ? "\(title) ✓" : title, systemImage: symbol) }
            .disabled(disabled ?? selected)
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
    let onAction: (String) -> Void
    let onDelete: () -> Void
    let onFinish: () -> Void
    let onEditDates: () -> Void
    let errorMessage: () -> String?
    @State private var mode: Mode
    @State private var input = ""
    @State private var saving = false
    @State private var isClosing = false
    @State private var error: String?
    @State private var pending: PendingAction?
    @State private var contentHeight: CGFloat = 300
    @State private var focusRequest = 0

    init(detail: MediaDetail, playthrough: GamePlaythroughState,
         onSave: @escaping (GamePlaythroughWriteRequest) async -> Bool, onAction: @escaping (String) -> Void,
         onDelete: @escaping () -> Void, onFinish: @escaping () -> Void, onEditDates: @escaping () -> Void,
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
                .disabled(saving)
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
            if playthrough.isUnfinished {
                ProgressStatusRow(choices: [
                    ProgressStatusChoice(title: "Playing", systemImage: "play.fill",
                                         isCurrent: playthrough.status == "In progress") { choose { onAction("resume") } },
                    ProgressStatusChoice(title: "Paused", systemImage: "pause.fill",
                                         isCurrent: playthrough.status == "Paused") { choose { onAction("pause") } },
                    ProgressStatusChoice(title: "Finished", systemImage: "checkmark", isCurrent: false, action: onFinish),
                    ProgressStatusChoice(title: "Dropped", systemImage: "xmark", isCurrent: false) { pending = .drop },
                ])
                .disabled(saving)
            }
            saveButton
            HStack(spacing: 22) {
                Button("Edit Dates", action: onEditDates)
                if playthrough.isUnfinished {
                    Button("Restart") { pending = .restart }
                    Button("Delete", role: .destructive) { pending = .delete }
                }
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(.secondary)
            .buttonStyle(.borderless)
            .disabled(saving)
        }
        .padding(.horizontal, 16)
        .padding(.top, 22)
        .padding(.bottom, 16)
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
        .frame(maxHeight: .infinity, alignment: .top)
        .presentationDetents([.height(contentHeight)])
        .presentationDragIndicator(.visible)
        .presentationBackground(Color(uiColor: .secondarySystemBackground))
        .interactiveDismissDisabled(saving)
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
                case .drop: choose { onAction("drop") }
                case .restart: choose { onAction("restart") }
                case .delete: choose(onDelete)
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
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
            }
            .buttonStyle(.bordered)
            .buttonBorderShape(.circle)
            .accessibilityLabel("Close")
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
                AutofocusNumberField(text: $input, placeholder: lastValue.map(String.init) ?? "0",
                                     accessibilityLabel: mode == .hours ? "Hours played" : "Percent complete",
                                     focusRequest: focusRequest)
                Text(mode == .hours ? "h" : "%")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture { focusRequest += 1 }
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

    /// Status changes apply behind the sheet, so it closes on the tap. A second tap while it slides away is ignored.
    private func choose(_ choice: () -> Void) {
        guard !saving, !isClosing else { return }
        isClosing = true
        choice()
        dismiss()
    }

    private func run(_ operation: @escaping () async -> Bool) {
        guard !saving, !isClosing else { return }
        saving = true
        error = nil
        Task {
            if await operation() { dismiss() }
            else { error = errorMessage() ?? "Could not save. Try again." }
            saving = false
        }
    }
}

/// One status option in an update-progress panel.
struct ProgressStatusChoice: Identifiable {
    let title: String
    let systemImage: String
    let isCurrent: Bool
    let action: () -> Void
    var id: String { title }
}

/// The status of the current playthrough or journey as a row of plain choices, the
/// current one highlighted, so changing status sits next to the progress itself.
struct ProgressStatusRow: View {
    let choices: [ProgressStatusChoice]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Status").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                ForEach(choices) { choice in
                    Button(action: choice.action) {
                        VStack(spacing: 4) {
                            Image(systemName: choice.systemImage).font(.system(size: 15, weight: .semibold))
                            Text(choice.title)
                                .font(.caption.weight(.semibold))
                                .lineLimit(1)
                                .minimumScaleFactor(0.8)
                        }
                        .frame(maxWidth: .infinity)
                        .frame(height: 54)
                        .foregroundStyle(choice.isCurrent ? Color.black : Color.primary)
                        .background(choice.isCurrent ? Color.white : Color.primary.opacity(0.1),
                                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .disabled(choice.isCurrent)
                    .accessibilityAddTraits(choice.isCurrent ? .isSelected : [])
                }
            }
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
