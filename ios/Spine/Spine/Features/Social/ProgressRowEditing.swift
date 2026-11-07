import SwiftUI

/// Fixes or takes back a day of your own progress from its page (activity contract, AC-9). The server says what is
/// offered; this only sends it. Either one changes Stats, the Activity list, the profile and Home, so each posts
/// `MediaStateChange` and the page closes.
@MainActor
@Observable
final class ProgressRowEditor {
    private(set) var isDeleting = false
    /// Set once an edit or a delete went through: the page leaves when its sheet or dialog is gone.
    private(set) var didChange = false
    var deleteErrorMessage: String?

    private let progressId: Int
    private let media: MediaSummary
    private let activityRepository: ActivityRepository
    private let trackingRepository: TrackingRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        progressId: Int,
        media: MediaSummary,
        activityRepository: ActivityRepository,
        trackingRepository: TrackingRepository,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.progressId = progressId
        self.media = media
        self.activityRepository = activityRepository
        self.trackingRepository = trackingRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    /// Takes back the newest row. A 409 means a save since this page loaded made it an older row.
    func delete() async {
        guard !isDeleting else { return }
        isDeleting = true
        deleteErrorMessage = nil
        defer { isDeleting = false }
        do {
            try await activityRepository.deleteProgress(id: progressId)
            changed()
        } catch is CancellationError {
            return
        } catch {
            deleteErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    /// Saves the sheet's number and returns what to tell the person, or nil when it went through.
    func save(input: String, date: Date, edit: ProgressEdit) async -> String? {
        let presentation = ProgressRowEditPresentation(edit: edit, mediaType: media.ref.mediaType)
        guard let value = presentation.value(from: input) else { return "Enter a number." }
        do {
            if edit.isNumber {
                guard let request = presentation.patch(for: input) else { return "Enter a different number." }
                _ = try await activityRepository.fixProgress(id: progressId, request: request)
            } else {
                try await saveAgain(value: value, on: CalendarDateCodec.string(from: date), edit: edit)
            }
            changed()
            return nil
        } catch is CancellationError {
            return "Cancelled."
        } catch {
            handleUnauthorized(error)
            return error.localizedDescription
        }
    }

    /// The newest row has no route of its own: its number is saved again, on its day, from the normal progress calls.
    private func saveAgain(value: Decimal, on day: String, edit: ProgressEdit) async throws {
        let ref = media.ref
        if ref.mediaType == "game" {
            let detail = try await mediaRepository.detail(ref: ref)
            guard let playthrough = detail.userState?.game?.currentPlaythrough else {
                throw ProgressRowEditError.noPlaythrough
            }
            var request = GamePlaythroughWriteRequest(progressedOn: day)
            if edit.unit == "minute" {
                request.totalMinutes = NSDecimalNumber(decimal: value).intValue
                request.includesMinutes = true
            } else {
                request.percentage = value
                request.includesPercentage = true
            }
            _ = try await trackingRepository.updateGamePlaythrough(ref: ref, playthroughId: playthrough.id, request: request)
        } else if ref.isSeries {
            _ = try await trackingRepository.updateSeriesProgress(
                ref: ref,
                value: NSDecimalNumber(decimal: value).intValue,
                mutationId: UUID(),
                progressedOn: day
            )
        } else {
            let mode: ProgressUpdateMode = edit.kind == "percentage" ? .percentage : .pages
            _ = try await trackingRepository.updateBookProgress(
                source: ref.source,
                mediaId: ref.mediaId,
                progressType: mode.apiValue,
                value: value,
                notes: "",
                progressedOn: day
            )
        }
    }

    private func changed() {
        didChange = true
        MediaStateChange.post(ref: media.ref)
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

enum ProgressRowEditError: LocalizedError {
    case noPlaythrough

    var errorDescription: String? {
        "This game has no playthrough to save to."
    }
}

/// Edit on a progress page: the shared progress form with the row's number in it. The newest row also shows its day;
/// an older row keeps its own day and is held between the saves either side of it. No ⋯: the read's own actions
/// (pause, restart, delete) belong to the title's page.
struct ProgressRowEditSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var input: String
    @State private var date: Date
    @State private var isSaving = false
    @State private var errorMessage: String?

    let title: String
    let edit: ProgressEdit
    let mediaType: String
    let onSave: (String, Date) async -> String?

    init(
        title: String,
        edit: ProgressEdit,
        mediaType: String,
        onSave: @escaping (String, Date) async -> String?
    ) {
        self.title = title
        self.edit = edit
        self.mediaType = mediaType
        self.onSave = onSave
        _input = State(initialValue: ProgressRowEditPresentation(edit: edit, mediaType: mediaType).initialText)
        _date = State(initialValue: CalendarDateCodec.date(from: edit.date) ?? Date())
    }

    private var presentation: ProgressRowEditPresentation {
        ProgressRowEditPresentation(edit: edit, mediaType: mediaType)
    }

    var body: some View {
        let validation = presentation.validation(for: input)
        let warning = errorMessage ?? validation
        ProgressSheetForm(
            title: title,
            subtitle: "Edit",
            numberLabel: presentation.numberLabel,
            number: $input,
            placeholder: presentation.initialText,
            allowsDecimal: presentation.allowsDecimal,
            unit: presentation.unitText,
            footer: warning ?? presentation.footer,
            footerIsWarning: warning != nil,
            finishTitle: nil,
            canSave: presentation.canSave(input: input),
            isSaving: isSaving,
            date: edit.isSave ? $date : nil,
            showsMore: false,
            onSave: save,
            onFinish: {}
        ) {
        } moreActions: {
        }
        .onChange(of: input) { _, value in
            let text = presentation.sanitized(value)
            if text != value { input = text }
            errorMessage = nil
        }
    }

    private func save() {
        guard !isSaving else { return }
        isSaving = true
        errorMessage = nil
        Task {
            let failure = await onSave(input, date)
            isSaving = false
            if let failure {
                errorMessage = failure
            } else {
                dismiss()
            }
        }
    }
}
