import SwiftUI

struct ProgressUpdateSaveRequest: Equatable {
    let mode: ProgressUpdateMode
    let value: Int
}

/// The title a progress sheet updates. A Home card knows it before the title's page has loaded, so the sheet opens at
/// once; the page's own copy takes over once it has.
struct ProgressSheetTitle: Identifiable, Equatable {
    let ref: MediaRef
    let title: String
    /// A book's page count from its page. Without it, page progress's total stands in.
    let totalPages: Int?

    var id: String { ref.id }

    init(detail: MediaDetail) {
        ref = detail.ref
        title = detail.displayTitle
        totalPages = detail.progressTotalPages
    }

    init(item: LibraryItem) {
        ref = item.media.ref
        title = item.media.displayTitle
        totalPages = nil
    }
}

@MainActor
@Observable
final class ProgressUpdateViewModel {
    var title: ProgressSheetTitle
    let progress: ProgressState?
    var mode: ProgressUpdateMode
    var input = ""
    private var hasEditedInput = false

    convenience init(detail: MediaDetail, progress: ProgressState?) {
        self.init(title: ProgressSheetTitle(detail: detail), progress: progress)
    }

    init(title: ProgressSheetTitle, progress: ProgressState?) {
        self.title = title
        self.progress = progress
        self.mode = Self.defaultMode(for: title, progress: progress)
        if let lastValue {
            input = String(lastValue)
        }
    }

    /// Percentage progress's total is 100, not a page count.
    var totalPages: Int? {
        title.totalPages ?? (progress?.mode == .pages ? Self.intValue(progress?.max) : nil)
    }

    var lastValue: Int? {
        guard let progress else { return nil }
        return progress.value(in: mode)
    }

    var currentValue: Int? {
        Int(input)
    }

    /// The field's text: empty until the first digit, so the last saved value shows as its placeholder and the first
    /// digit typed replaces it.
    var keyboardInputText: String {
        hasEditedInput ? input : ""
    }

    /// Under the number: where the last save left off, and how far this one moves from it. Nil before a first save.
    var summaryText: String? {
        guard let lastValue else { return nil }
        let last = mode == .percentage ? "\(lastValue)%" : "page \(lastValue)"
        guard let currentValue, currentValue != lastValue else { return "Last saved at \(last)" }
        let delta = abs(currentValue - lastValue)
        let amount = mode == .percentage ? "\(delta)%" : delta == 1 ? "1 page" : "\(delta) pages"
        return "\(currentValue > lastValue ? "+" : "−")\(amount) since \(last)"
    }

    var validationMessage: String? {
        guard let value = currentValue else { return nil }
        if mode == .percentage, value > 100 {
            return "Enter a value from 0 to 100."
        }
        if mode == .pages, let totalPages, value > totalPages {
            return "Enter a value from 0 to \(totalPages)."
        }
        return nil
    }

    var canSave: Bool {
        guard currentValue != nil, validationMessage == nil else { return false }
        return currentValue != lastValue
    }

    var isFullProgress: Bool {
        guard let currentValue else { return false }
        if mode == .percentage {
            return currentValue == 100
        }
        return totalPages.map { currentValue == $0 } ?? false
    }

    var saveRequest: ProgressUpdateSaveRequest? {
        guard canSave, let currentValue else { return nil }
        return ProgressUpdateSaveRequest(mode: mode, value: currentValue)
    }

    func selectMode(_ newMode: ProgressUpdateMode) {
        guard title.ref.mediaType == "book", newMode != mode else { return }
        if let currentValue {
            input = converted(currentValue, from: mode, to: newMode).map(String.init) ?? ""
        }
        mode = newMode
    }

    func updateInput(_ rawValue: String) {
        let limit = mode == .percentage ? 3 : 6
        let digits = String(rawValue.filter(\.isNumber).prefix(limit))
        if !hasEditedInput, digits == input {
            return
        }
        guard !digits.isEmpty else {
            input = ""
            hasEditedInput = true
            return
        }
        if !hasEditedInput {
            let replacement = !input.isEmpty && digits.hasPrefix(input) ? String(digits.dropFirst(input.count)) : digits
            input = replacement.isEmpty ? digits : String(replacement.prefix(limit))
        } else {
            input = digits
        }
        hasEditedInput = true
    }

    private func converted(_ value: Int, from: ProgressUpdateMode, to: ProgressUpdateMode) -> Int? {
        guard from != to else { return value }
        guard let totalPages, totalPages > 0 else { return nil }
        switch (from, to) {
        case (.pages, .percentage):
            return Int((Double(value) / Double(totalPages) * 100).rounded())
        case (.percentage, .pages):
            return Int((Double(value) / 100 * Double(totalPages)).rounded())
        default:
            return value
        }
    }

    private static func defaultMode(for title: ProgressSheetTitle, progress: ProgressState?) -> ProgressUpdateMode {
        guard title.ref.mediaType == "book" else { return .percentage }
        if let preferredMode = ProgressDisplayPreferences.mode(for: title.ref) {
            return preferredMode
        }
        if let progress {
            return progress.mode
        }
        return title.totalPages == nil ? .percentage : .pages
    }

    private static func intValue(_ value: Decimal?) -> Int? {
        guard let value else { return nil }
        return Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
    }
}

/// Update Progress for a book, a native sheet like the TV episode sheet: the book in the bar with Close, ⋯ and Save,
/// the page it's on against the last saved one, and Finished Reading. Pause, Did Not Finish, Restart and Delete
/// Journey sit in ⋯. Its choices close it; their writes apply behind it.
struct ProgressUpdateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProgressUpdateViewModel
    @State private var pendingJourneyAction: BookJourneyMenuAction?
    /// A save waiting for its answer, or for the page it writes to.
    @State private var isSubmitting = false
    /// A choice closed the sheet: a second tap while it slides away does nothing.
    @State private var isClosing = false

    let title: ProgressSheetTitle
    let isSaving: Bool
    let errorMessage: String?
    let journeyStatus: String?
    let onSave: (ProgressUpdateSaveRequest) async -> Bool
    /// Finished Reading, or progress saved at the last page: the completion log, which closes the sheet first.
    let onFinish: () -> Void
    /// "pause", "resume", "drop" or "restart".
    let onAction: (String) -> Void
    let onDelete: () -> Void

    init(
        title: ProgressSheetTitle,
        progress: ProgressState?,
        isSaving: Bool,
        errorMessage: String?,
        journeyStatus: String?,
        onSave: @escaping (ProgressUpdateSaveRequest) async -> Bool,
        onFinish: @escaping () -> Void,
        onAction: @escaping (String) -> Void,
        onDelete: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: ProgressUpdateViewModel(title: title, progress: progress))
        self.title = title
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.journeyStatus = journeyStatus
        self.onSave = onSave
        self.onFinish = onFinish
        self.onAction = onAction
        self.onDelete = onDelete
    }

    var body: some View {
        let warning = viewModel.validationMessage ?? errorMessage
        ProgressSheetForm(
            title: title.title,
            subtitle: journeyStatus == "Paused" ? "Paused" : nil,
            numberLabel: viewModel.mode == .pages ? "Page" : "Progress",
            number: Binding(get: { viewModel.keyboardInputText }, set: { viewModel.updateInput($0) }),
            placeholder: viewModel.lastValue.map(String.init) ?? "0",
            unit: viewModel.mode == .pages ? viewModel.totalPages.map { "of \($0)" } ?? "" : "%",
            footer: warning ?? viewModel.summaryText,
            footerIsWarning: warning != nil,
            finishTitle: "Finished Reading",
            canSave: viewModel.canSave && !isClosing,
            isSaving: isSaving || isSubmitting,
            onSave: save,
            onFinish: { perform(.finish) }
        ) {
            Picker("Track by", selection: Binding(get: { viewModel.mode }, set: { viewModel.selectMode($0) })) {
                ForEach(ProgressUpdateMode.allCases) { Text($0.title).tag($0) }
            }
        } moreActions: {
            menuButton(journeyStatus == "Paused" ? .resume : .pause)
            menuButton(.didNotFinish)
            Divider()
            menuButton(.restart)
            menuButton(.delete)
        }
        // The page's copy, once it has loaded behind a sheet opened from Home, knows the book's page count.
        .onChange(of: title) { _, title in viewModel.title = title }
        .confirmationDialog(
            pendingJourneyAction?.confirmationTitle ?? "",
            isPresented: Binding(
                get: { pendingJourneyAction != nil },
                set: { if !$0 { pendingJourneyAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let action = pendingJourneyAction {
                Button(action.confirmationButton, role: action.isDestructive ? .destructive : nil) {
                    perform(action)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(pendingJourneyAction?.confirmationMessage ?? "")
        }
    }

    private func menuButton(_ action: BookJourneyMenuAction) -> some View {
        Button(action.title, systemImage: action.systemImage, role: action == .delete ? .destructive : nil) {
            if action.requiresConfirmation {
                pendingJourneyAction = action
            } else {
                perform(action)
            }
        }
    }

    private func save() {
        guard let request = viewModel.saveRequest, !isSubmitting, !isClosing else { return }
        let finishes = viewModel.isFullProgress
        isSubmitting = true
        Task {
            let saved = await onSave(request)
            isSubmitting = false
            guard saved else { return }
            isClosing = true
            if finishes {
                onFinish()
            } else {
                dismiss()
            }
        }
    }

    private func perform(_ action: BookJourneyMenuAction) {
        pendingJourneyAction = nil
        guard !isClosing, !isSubmitting else { return }
        isClosing = true
        switch action {
        case .pause: onAction("pause")
        case .resume: onAction("resume")
        case .didNotFinish: onAction("drop")
        case .restart: onAction("restart")
        case .delete: onDelete()
        case .finish:
            // The completion log closes the sheet before it opens.
            onFinish()
            return
        }
        dismiss()
    }
}

/// A progress sheet's layout, for a book or a game, in the look of the TV episode sheet and the log sheet: the title
/// in the bar with Close, ⋯ and Save; one number against the last saved one, and how it's counted; and Finished. The
/// number pad rises with the sheet.
struct ProgressSheetForm<CountOptions: View, MoreActions: View>: View {
    @Environment(\.dismiss) private var dismiss
    @State private var focusRequest = 0

    let title: String
    let subtitle: String?
    let numberLabel: String
    @Binding var number: String
    let placeholder: String
    let unit: String
    /// Under the number: the last save, or what's wrong with this one.
    let footer: String?
    let footerIsWarning: Bool
    /// Finished Reading or Finished Playing; nil leaves it out.
    let finishTitle: String?
    let canSave: Bool
    let isSaving: Bool
    let onSave: () -> Void
    let onFinish: () -> Void
    let countOptions: CountOptions
    let moreActions: MoreActions

    init(
        title: String,
        subtitle: String?,
        numberLabel: String,
        number: Binding<String>,
        placeholder: String,
        unit: String,
        footer: String?,
        footerIsWarning: Bool,
        finishTitle: String?,
        canSave: Bool,
        isSaving: Bool,
        onSave: @escaping () -> Void,
        onFinish: @escaping () -> Void,
        @ViewBuilder countOptions: () -> CountOptions,
        @ViewBuilder moreActions: () -> MoreActions
    ) {
        self.title = title
        self.subtitle = subtitle
        self.numberLabel = numberLabel
        _number = number
        self.placeholder = placeholder
        self.unit = unit
        self.footer = footer
        self.footerIsWarning = footerIsWarning
        self.finishTitle = finishTitle
        self.canSave = canSave
        self.isSaving = isSaving
        self.onSave = onSave
        self.onFinish = onFinish
        self.countOptions = countOptions()
        self.moreActions = moreActions()
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 6) {
                        Text(numberLabel)
                        Spacer(minLength: 12)
                        AutofocusNumberField(
                            text: $number,
                            placeholder: placeholder,
                            accessibilityLabel: numberLabel,
                            focusRequest: focusRequest
                        )
                        if !unit.isEmpty {
                            Text(unit)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture { focusRequest += 1 }

                    countOptions
                } footer: {
                    if let footer {
                        Text(footer)
                            .foregroundStyle(footerIsWarning ? Color.red : Color.secondary)
                    }
                }
                .logRowBackground()

                if let finishTitle {
                    Section {
                        Button(finishTitle, systemImage: "checkmark.circle", action: onFinish)
                    }
                    .logRowBackground()
                }
            }
            .scrollContentBackground(.hidden)
            // The sheet rides on the number pad, so the keyboard takes no room inside it.
            .ignoresSafeArea(.keyboard)
            .fitsSheetToContent(aboveKeyboard: true)
            .disabled(isSaving)
            .navigationTitle(title)
            .navigationSubtitle(subtitle ?? "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                    }
                    .disabled(isSaving)
                }
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        moreActions
                    } label: {
                        Label("More", systemImage: "ellipsis")
                    }
                    .disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSaving {
                        ProgressView()
                    } else {
                        Button(role: .confirm, action: onSave) {
                            Label("Save", systemImage: "checkmark")
                        }
                        .disabled(!canSave)
                        .accessibilityLabel("Save Progress")
                    }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(isSaving)
    }
}

private enum BookJourneyMenuAction: String, CaseIterable, Identifiable {
    case pause
    case resume
    case didNotFinish
    case finish
    case restart
    case delete

    var id: String { rawValue }
    var title: String {
        switch self {
        case .pause: "Pause"
        case .resume: "Resume Reading"
        case .didNotFinish: "Mark as DNF"
        case .finish: "Finish & Log"
        case .restart: "Restart from Beginning"
        case .delete: "Delete Journey"
        }
    }
    var systemImage: String {
        switch self {
        case .pause: "pause.fill"
        case .resume: "play.fill"
        case .didNotFinish: "xmark"
        case .finish: "checkmark"
        case .restart: "arrow.counterclockwise"
        case .delete: "trash"
        }
    }
    var requiresConfirmation: Bool { self == .didNotFinish || self == .restart || self == .delete }
    var isDestructive: Bool { self == .delete || self == .restart }
    var confirmationTitle: String {
        switch self {
        case .didNotFinish: "Mark this book as Did Not Finish?"
        case .restart: "Restart from Beginning?"
        case .delete: "Delete this reading journey?"
        default: title
        }
    }
    var confirmationButton: String { title }
    var confirmationMessage: String {
        switch self {
        case .didNotFinish: "Your current progress will remain in reading history."
        case .restart: "The current journey will end and a new journey will begin today."
        case .delete: "This journey and its progress will be removed. Completion diary logs are managed separately."
        default: ""
        }
    }
}

extension MediaDetail {
    var progressTotalPages: Int? {
        for key in ["number_of_pages", "pages", "total_pages"] {
            if let value = details?[key]?.progressIntValue {
                return value
            }
        }
        return nil
    }
}

private extension JSONValue {
    var progressIntValue: Int? {
        switch self {
        case let .number(value):
            Int(value)
        case let .string(value):
            Int(value)
        default:
            nil
        }
    }
}
