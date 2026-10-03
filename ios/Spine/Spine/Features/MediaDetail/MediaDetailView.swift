import SwiftUI
import UIKit

enum MusicEnrichmentState: Equatable {
    case idle
    case loading
    case loaded
    case failed
}

@MainActor
@Observable
final class MediaDetailViewModel {
    var detail: MediaDetail?
    var reviews: [MediaReview] = []
    var reviewCount = 0
    var tracking: TrackingState?
    var isLoading = true
    var isLoadingReviews = false
    var isSavingQuickAction = false
    var isSavingProgress = false
    var isSavingLike = false
    var errorMessage: String?
    var reviewsErrorMessage: String?
    var quickActionErrorMessage: String?
    var progressErrorMessage: String?
    var likeErrorMessage: String?
    var musicEnrichmentState = MusicEnrichmentState.idle

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let onUnauthorized: () -> Void
    private let externalRatingPollInterval: Duration?
    private let externalRatingMaxPollAttempts: Int

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        onUnauthorized: @escaping () -> Void,
        externalRatingPollInterval: Duration? = nil,
        externalRatingMaxPollAttempts: Int = 30
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.onUnauthorized = onUnauthorized
        self.externalRatingPollInterval = externalRatingPollInterval
        self.externalRatingMaxPollAttempts = externalRatingMaxPollAttempts
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let loaded = try await mediaRepository.detail(ref: ref)
            detail = loaded
            reviews = loaded.reviews ?? []
            reviewCount = reviews.count
            async let enrichment: Void = loadMusicEnrichmentIfNeeded(for: loaded)
            async let tracking: Void = loadTrackingIfNeeded(for: loaded)
            async let reviewLoad: Void = loadReviews()
            _ = await (enrichment, tracking, reviewLoad)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func loadMusicEnrichmentIfNeeded(for loaded: MediaDetail) async {
        guard loaded.ref.mediaType == "music" else {
            musicEnrichmentState = .idle
            return
        }
        musicEnrichmentState = .loading
        do {
            let enriched = try await mediaRepository.enrichedMusicDetail(ref: ref)
            guard detail?.id == loaded.id else { return }
            detail = enriched
            musicEnrichmentState = .loaded
        } catch is CancellationError {
            musicEnrichmentState = .idle
        } catch {
            musicEnrichmentState = .failed
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    var externalRatingsPollingID: String {
        let state = detail?.externalRatingsPreparation?.state.rawValue ?? "none"
        return "\(detail?.id ?? ref.id):\(state)"
    }

    func pollExternalRatingsIfNeeded() async {
        guard detail?.externalRatingsPreparation?.state == .pending else { return }
        let expectedID = detail?.id

        for _ in 0..<externalRatingMaxPollAttempts {
            do {
                let seconds = detail?.externalRatingsPreparation?.retryAfterSeconds ?? 2
                try await Task.sleep(for: externalRatingPollInterval ?? .seconds(seconds))
                try Task.checkCancellation()
                let response = try await mediaRepository.externalRatings(ref: ref)
                guard detail?.id == expectedID else { return }
                detail = detail?.replacingExternalRatings(with: response)
                if response.externalRatingsPreparation.state != .pending {
                    return
                }
            } catch is CancellationError {
                return
            } catch {
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
                return
            }
        }
    }

    private func loadTrackingIfNeeded(for detail: MediaDetail) async {
        guard detail.userState?.isTracked == true || detail.userState?.status != nil else {
            tracking = nil
            return
        }
        if detail.ref.mediaType == "game" { tracking = nil }
        do {
            tracking = try await trackingRepository.detail(ref: detail.ref)
        } catch APIError.httpStatus(404, _) {
            tracking = nil
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func loadReviews() async {
        isLoadingReviews = true
        reviewsErrorMessage = nil
        defer { isLoadingReviews = false }

        do {
            let page = try await mediaRepository.reviewPage(ref: ref, page: nil)
            reviews = page.results
            reviewCount = page.count
        } catch {
            reviewsErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func toggleLike(for review: MediaReview) async {
        do {
            let state = try await diaryRepository.setLike(entryId: review.id, liked: !review.viewerHasLiked)
            guard let index = reviews.firstIndex(where: { $0.id == review.id }) else { return }
            reviews[index].viewerHasLiked = state.liked
            reviews[index].likeCount = state.likeCount
        } catch {
            reviewsErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func toggleMediaLike(for detail: MediaDetail) async -> Bool {
        guard !isSavingLike else { return false }
        isSavingLike = true
        likeErrorMessage = nil
        defer { isSavingLike = false }

        let next = !(detail.userState?.hasLiked ?? false)
        self.detail = detail.replacingHasLiked(next)
        do {
            let response = try await mediaRepository.setLiked(ref: detail.ref, liked: next)
            self.detail = self.detail?.replacingHasLiked(response.liked)
            MediaStateChange.post(ref: detail.ref)
            await load()
            return true
        } catch {
            self.detail = detail
            likeErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func markConsumed(for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            tracking = try await trackingRepository.consume(ref: detail.ref, consumedAt: nil)
            self.detail = detail.replacingIsTracked(true)
            MediaStateChange.post(ref: detail.ref)
            await load()
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func setCurrentRating(for detail: MediaDetail, halfSteps: Int) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            let rating = halfSteps > 0 ? Decimal(halfSteps) / 2 : nil
            tracking = try await trackingRepository.update(
                ref: detail.ref,
                request: TrackingWriteRequest(rating: rating, includesRating: true)
            )
            MediaStateChange.post(ref: detail.ref)
            await load()
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func performQuickAction(_ action: MediaDetailQuickAction, for detail: MediaDetail, completedAt: Date = Date()) async -> Bool {
        if detail.ref.mediaType == "game", action != .planning {
            let name: String
            switch action {
            case .currently: name = "start"
            case .paused: name = "pause"
            case .stopped: name = "drop"
            case .finished: name = "mark_completed"
            case .planning: name = "planning"
            }
            return await performGameAction(name, for: detail)
        }
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            let state: TrackingState
            switch action {
            case .planning:
                state = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(status: "Planning")
                )
            case .currently:
                if detail.ref.mediaType == "book", tracking?.status == "Paused" {
                    state = try await trackingRepository.performBookAction(
                        source: detail.ref.source,
                        mediaId: detail.ref.mediaId,
                        action: "resume",
                        request: BookActionRequest()
                    )
                } else {
                    state = try await trackingRepository.update(
                        ref: detail.ref,
                        request: TrackingWriteRequest(
                            status: "In progress",
                            startDate: detail.ref.mediaType == "book" ? CalendarDateCodec.string(from: completedAt) : nil,
                            mutationId: detail.ref.mediaType == "book" ? UUID() : nil
                        )
                    )
                }
            case .paused:
                if detail.ref.mediaType == "book", tracking?.book?.currentJourney != nil {
                    state = try await trackingRepository.performBookAction(
                        source: detail.ref.source,
                        mediaId: detail.ref.mediaId,
                        action: "pause",
                        request: BookActionRequest()
                    )
                } else {
                    state = try await trackingRepository.update(
                        ref: detail.ref,
                        request: TrackingWriteRequest(status: "Paused")
                    )
                }
            case .finished:
                if detail.ref.mediaType == "book" {
                    state = try await trackingRepository.completeBook(
                        source: detail.ref.source,
                        mediaId: detail.ref.mediaId,
                        completedAt: completedAt
                    )
                } else {
                    state = try await trackingRepository.consume(
                        ref: detail.ref,
                        consumedAt: detail.ref.isSingleWeight ? nil : completedAt
                    )
                }
            case .stopped:
                if detail.ref.mediaType == "book", tracking?.book?.currentJourney != nil {
                    state = try await trackingRepository.performBookAction(
                        source: detail.ref.source,
                        mediaId: detail.ref.mediaId,
                        action: "drop",
                        request: BookActionRequest(endDate: CalendarDateCodec.string(from: completedAt))
                    )
                } else {
                    state = try await trackingRepository.update(
                        ref: detail.ref,
                        request: TrackingWriteRequest(status: "Dropped")
                    )
                }
            }
            tracking = state
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func removeTracking(for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            try await trackingRepository.delete(ref: detail.ref)
            tracking = nil
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func watchEpisode(_ detail: MediaDetail, watchedAt: Date = Date()) async -> Bool {
        guard
            !isSavingQuickAction,
            let seasonNumber = detail.ref.seasonNumber,
            let episodeNumber = detail.ref.episodeNumber
        else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }

        do {
            _ = try await trackingRepository.watchEpisode(
                source: detail.ref.source,
                mediaId: detail.ref.mediaId,
                seasonNumber: seasonNumber,
                episodeNumber: episodeNumber,
                watchedAt: watchedAt
            )
            self.detail = detail.replacingIsTracked(true)

            do {
                let refreshed = try await mediaRepository.detail(ref: detail.ref)
                self.detail = refreshed.replacingIsTracked(true)
            } catch is CancellationError {
                // The watch mutation succeeded; cancellation only stops the best-effort refresh.
            } catch {
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
            }
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func saveProgress(_ request: ProgressUpdateSaveRequest, for detail: MediaDetail) async -> Bool {
        guard detail.ref.mediaType != "game" else {
            progressErrorMessage = "Update hours and percentage through the game's playthrough."
            return false
        }
        guard !isSavingProgress else { return false }
        isSavingProgress = true
        progressErrorMessage = nil
        defer { isSavingProgress = false }

        do {
            var state: TrackingState
            if detail.ref.mediaType == "book" {
                state = try await trackingRepository.updateBookProgress(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId,
                    progressType: request.mode.apiValue,
                    value: Decimal(request.value),
                    notes: ""
                )
                state = state.replacingProgress(progressState(for: request, detail: detail, fallback: state.progress))
            } else {
                state = try await trackingRepository.update(
                    ref: detail.ref,
                    request: TrackingWriteRequest(
                        status: "In progress",
                        progress: request.value
                    )
                )
                state = state.replacingProgress(progressState(for: request, detail: detail, fallback: state.progress))
            }
            ProgressDisplayPreferences.setMode(request.mode, for: detail.ref)
            tracking = state
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            progressErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return false
        }
    }

    func performBookAction(_ action: String, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        do {
            if action == "undo_read" {
                try await trackingRepository.undoBookRead(
                    source: detail.ref.source,
                    mediaId: detail.ref.mediaId
                )
                tracking = nil
                MediaStateChange.post(ref: detail.ref)
                await load()
                return true
            }
            tracking = try await trackingRepository.performBookAction(
                source: detail.ref.source,
                mediaId: detail.ref.mediaId,
                action: action,
                request: BookActionRequest(
                    startDate: action == "restart" || action == "resume" ? CalendarDateCodec.string(from: Date()) : nil,
                    endDate: action == "drop" || action == "restart" ? CalendarDateCodec.string(from: Date()) : nil
                )
            )
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    func updateBookJourney(_ journey: BookJourneyState, startDate: Date?, endDate: Date?, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        do {
            tracking = try await trackingRepository.updateBookJourney(
                source: detail.ref.source,
                mediaId: detail.ref.mediaId,
                journeyId: journey.id,
                request: BookJourneyWriteRequest(
                    startDate: startDate.map { CalendarDateCodec.string(from: $0) },
                    endDate: endDate.map { CalendarDateCodec.string(from: $0) }
                )
            )
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    func deleteBookJourney(_ journey: BookJourneyState, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        do {
            tracking = try await trackingRepository.deleteBookJourney(
                source: detail.ref.source,
                mediaId: detail.ref.mediaId,
                journeyId: journey.id
            )
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    private var gameActionMutationIds: [String: UUID] = [:]

    func performGameAction(_ action: String, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        let mutation = gameActionMutationIds[action] ?? UUID()
        gameActionMutationIds[action] = mutation
        do {
            try await trackingRepository.performGameAction(ref: detail.ref, action: action, request: BookActionRequest(
                mutationId: mutation,
                startDate: ["start", "restart", "resume"].contains(action) ? CalendarDateCodec.string(from: Date()) : nil,
                endDate: ["drop", "restart"].contains(action) ? CalendarDateCodec.string(from: Date()) : nil
            ))
            await load()
            MediaStateChange.post(ref: detail.ref)
            guard errorMessage == nil else {
                quickActionErrorMessage = "The change was saved, but refresh failed. Try again to reload it."
                return false
            }
            gameActionMutationIds[action] = nil
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    func updateGamePlaythrough(_ playthrough: GamePlaythroughState, request: GamePlaythroughWriteRequest, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        do {
            tracking = try await trackingRepository.updateGamePlaythrough(ref: detail.ref, playthroughId: playthrough.id, request: request)
            await load()
            MediaStateChange.post(ref: detail.ref)
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    func deleteGamePlaythrough(_ playthrough: GamePlaythroughState, for detail: MediaDetail) async -> Bool {
        guard !isSavingQuickAction else { return false }
        isSavingQuickAction = true
        quickActionErrorMessage = nil
        defer { isSavingQuickAction = false }
        do {
            try await trackingRepository.deleteGamePlaythrough(ref: detail.ref, playthroughId: playthrough.id)
            await load()
            MediaStateChange.post(ref: detail.ref)
            guard errorMessage == nil else {
                quickActionErrorMessage = "The playthrough was deleted, but refresh failed. Try again to reload it."
                return false
            }
            return true
        } catch {
            quickActionErrorMessage = error.localizedDescription
            if case APIError.unauthorized = error { onUnauthorized() }
            return false
        }
    }

    private func progressState(
        for request: ProgressUpdateSaveRequest,
        detail: MediaDetail,
        fallback: ProgressState?
    ) -> ProgressState {
        let max: Decimal?
        switch request.mode {
        case .percentage:
            max = Decimal(100)
        case .pages:
            max = detail.progressTotalPages.map { Decimal($0) } ?? fallback?.max
        }
        return ProgressState(
            kind: request.mode.apiValue,
            value: Decimal(request.value),
            max: max,
            unit: request.mode.unit
        )
    }

    func applyPosterSave(_ response: PosterSaveResponse) {
        guard let ref = detail?.ref else { return }
        detail = detail?.replacingPoster(with: response)
        MediaStateChange.post(ref: ref)
    }

    func applyBackdropSave(_ response: BackdropSaveResponse) {
        detail = detail?.replacingBackdrop(with: response)
    }

    func applyLogoSave(_ response: LogoSaveResponse) {
        detail = detail?.replacingLogo(with: response)
    }
}

enum MediaDetailQuickAction {
    case planning
    case currently
    case paused
    case finished
    case stopped
}

struct MediaRatingPickerState: Equatable {
    private(set) var isPresented = false
    var draftHalfSteps = 0
    private(set) var confirmedHalfSteps = 0
    private(set) var hasLocallyWatched = false

    var showsConfirm: Bool { draftHalfSteps != confirmedHalfSteps }

    mutating func open(markLocallyWatched: Bool = true) {
        draftHalfSteps = confirmedHalfSteps
        if markLocallyWatched { hasLocallyWatched = true }
        isPresented = true
    }

    mutating func dismiss() {
        draftHalfSteps = confirmedHalfSteps
        isPresented = false
    }

    mutating func confirm() {
        confirmedHalfSteps = draftHalfSteps
        isPresented = false
    }

    mutating func syncConfirmed(_ halfSteps: Int) {
        confirmedHalfSteps = halfSteps
        if !isPresented {
            draftHalfSteps = halfSteps
        }
    }

    mutating func rollbackWatch() {
        hasLocallyWatched = false
    }

    mutating func resetAfterUnwatch() {
        self = MediaRatingPickerState()
    }
}

private struct GameCompletionPresentation: Identifiable {
    let id = UUID()
    let playthroughId: Int?
    let liked: Bool?
    let ratingSteps: Int?
}

private enum MediaDetailSheet: Identifiable {
    case posterMenu
    case bookGameActions
    case addToList

    var id: String {
        switch self {
        case .posterMenu: "posterMenu"
        case .bookGameActions: "bookGameActions"
        case .addToList: "addToList"
        }
    }
}

private struct PresentedDiaryEntry: Identifiable {
    let id: Int
}

private struct PresentedMediaDiary: Identifiable {
    let detail: MediaDetail

    var id: String { detail.ref.id }
    var title: String { "\(detail.displayTitle) Logs" }
}

struct MediaPersonCredit: Hashable {
    let name: String
    let personRef: PersonRef?
}

struct MediaCreditPresentation: Hashable {
    let label: String
    let people: [MediaPersonCredit]

    var heroPeople: [MediaPersonCredit] {
        Array(people.prefix(2))
    }

    var heroMoreCount: Int {
        max(0, people.count - heroPeople.count)
    }

    static func make(for detail: MediaDetail) -> MediaCreditPresentation? {
        if detail.ref.source == "musicbrainz", detail.ref.mediaType == "music" {
            return musicArtists(detail.music?.artistCredit ?? [])
        }
        if detail.ref.mediaType == "manga" {
            var seen = Set<String>()
            let authors = detail.details?["authors"]?.displayStrings ?? []
            let names = authors.isEmpty ? (detail.crew ?? []).prefix(1).map(\.name) : authors
            let creatorsByName = (detail.crew ?? []).reduce(into: [String: CreditPerson]()) {
                $0[$1.name.lowercased()] = $0[$1.name.lowercased()] ?? $1
            }
            let people = names.compactMap { name -> MediaPersonCredit? in
                let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty, seen.insert(name.lowercased()).inserted else { return nil }
                return MediaPersonCredit(
                    name: name,
                    personRef: creatorsByName[name.lowercased()]?.personRef
                )
            }
            guard !people.isEmpty else { return nil }
            return MediaCreditPresentation(
                label: people.count == 1 ? "Author" : "Authors",
                people: people
            )
        }
        guard detail.ref.source == "tmdb" else { return nil }

        let configuration: (pluralKey: String, singularKey: String, singularIDKey: String, singularLabel: String, pluralLabel: String)
        switch detail.ref.mediaType {
        case "movie":
            configuration = ("directors", "director", "director_id", "Director", "Directors")
        case "tv":
            configuration = ("creators", "creator", "creator_id", "Creator", "Creators")
        default:
            return nil
        }

        let people = pluralPeople(in: detail.details?[configuration.pluralKey])
        let resolvedPeople = people.isEmpty
            ? legacyPerson(
                name: detail.details?[configuration.singularKey]?.stringValue,
                id: identifier(in: detail.details?[configuration.singularIDKey])
            ).map { [$0] } ?? []
            : people
        guard !resolvedPeople.isEmpty else { return nil }

        return MediaCreditPresentation(
            label: resolvedPeople.count == 1 ? configuration.singularLabel : configuration.pluralLabel,
            people: resolvedPeople
        )
    }

    static func musicArtists(_ credits: [MusicArtistCredit]) -> MediaCreditPresentation? {
        var seen = Set<String>()
        let people = credits.compactMap { credit -> MediaPersonCredit? in
            let name = credit.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            let key = credit.personRef.map { "id:\($0.id)" } ?? "name:\(name.lowercased())"
            guard seen.insert(key).inserted else { return nil }
            return MediaPersonCredit(name: name, personRef: credit.personRef)
        }
        guard !people.isEmpty else { return nil }
        return MediaCreditPresentation(
            label: people.count == 1 ? "Artist" : "Artists",
            people: people
        )
    }

    private static func pluralPeople(in value: JSONValue?) -> [MediaPersonCredit] {
        guard case let .array(values) = value else { return [] }
        var seen = Set<String>()
        return values.compactMap { value in
            guard case let .object(person) = value,
                  let name = person["name"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !name.isEmpty
            else { return nil }
            let id = identifier(in: person["id"])?.trimmingCharacters(in: .whitespacesAndNewlines)
            let key = id.map { "id:\($0)" } ?? "name:\(name.lowercased())"
            guard seen.insert(key).inserted else { return nil }
            return MediaPersonCredit(
                name: name,
                personRef: id.flatMap { $0.isEmpty ? nil : PersonRef(source: "tmdb", id: $0) }
            )
        }
    }

    private static func legacyPerson(name: String?, id: String?) -> MediaPersonCredit? {
        guard let name = name?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty else { return nil }
        let id = id?.trimmingCharacters(in: .whitespacesAndNewlines)
        return MediaPersonCredit(
            name: name,
            personRef: id.flatMap { $0.isEmpty ? nil : PersonRef(source: "tmdb", id: $0) }
        )
    }

    private static func identifier(in value: JSONValue?) -> String? {
        switch value {
        case let .string(id):
            id
        case let .number(id) where id.rounded() == id:
            String(Int(id))
        default:
            nil
        }
    }
}

private struct MediaDetailChip: Hashable, Identifiable {
    let label: String
    let discoverRequest: MediaDiscoverRequest?

    var id: String {
        [label, discoverRequest?.id ?? "_"].joined(separator: ":")
    }
}

enum MediaArtworkCustomization {
    static func supportsPoster(source: String, mediaType: String) -> Bool {
        if source == "mal", mediaType == "anime" {
            return true
        }
        if mediaType == "manga", ["mal", "mangaupdates"].contains(source) {
            return true
        }
        if source == "tmdb", ["movie", "tv", "season"].contains(mediaType) {
            return true
        }
        if source == "musicbrainz", mediaType == "music" {
            return true
        }
        if source == "igdb", mediaType == "game" {
            return true
        }
        return mediaType == "book" && ["openlibrary", "hardcover"].contains(source)
    }

    static func supportsBackdrop(source: String, mediaType: String) -> Bool {
        if source == "mal", mediaType == "anime" {
            return true
        }
        if mediaType == "manga", ["mal", "mangaupdates"].contains(source) {
            return true
        }
        if source == "tmdb", ["movie", "tv", "season", "episode"].contains(mediaType) {
            return true
        }
        return source == "igdb" && mediaType == "game"
    }

    static func supportsLogo(source: String, mediaType: String) -> Bool {
        if source == "tmdb", ["movie", "tv"].contains(mediaType) {
            return true
        }
        return source == "igdb" && mediaType == "game"
    }
}

enum MediaExternalRatingPresentation {
    static func includes(source: String, mediaType: String) -> Bool {
        let normalizedSource = source.lowercased()
        if mediaType == "season" {
            return normalizedSource == "tmdb"
        }
        if mediaType == "music" || ["spine", "google books", "igdb"].contains(normalizedSource) {
            return false
        }
        if mediaType == "manga", normalizedSource == "mangaupdates" {
            return false
        }
        if normalizedSource == "tmdb", ["movie", "tv"].contains(mediaType) {
            return false
        }
        return true
    }

    static func order(for source: String) -> Int {
        switch source.lowercased() {
        case "letterboxd": 0
        case "rotten tomatoes": 1
        case "imdb": 2
        case "metacritic": 3
        case "steam": 4
        case "mal", "myanimelist": 5
        case "anilist": 6
        default: 7
        }
    }

    static func showsExternalRatingPlaceholder(
        mediaType: String,
        ratings: [ExternalRating],
        preparation: MediaExternalRatingsPreparation?
    ) -> Bool {
        mediaType != "music"
            && preparation?.state == .pending
            && !ratings.contains {
                !$0.value.isEmpty && includes(source: $0.source, mediaType: mediaType)
            }
    }
}

enum MangaYearPresentation {
    static func value(startDate: String?, endDate: String?, status: String?) -> String? {
        guard let startYear = startDate?.yearPrefix else { return nil }
        let normalizedStatus = status?.lowercased() ?? ""
        if ["publishing", "hiatus", "releasing", "ongoing"].contains(where: normalizedStatus.contains) {
            return "\(startYear)–"
        }
        guard let endYear = endDate?.yearPrefix else { return startYear }
        return endYear == startYear ? startYear : "\(startYear)–\(endYear)"
    }
}

struct MusicStreamingDestination: Hashable, Identifiable {
    let label: String
    let url: URL

    var id: String { url.absoluteString }
}

enum MusicAlbumPresentation {
    static func artistCreditText(_ credits: [MusicArtistCredit]) -> String? {
        let value = credits
            .map { "\($0.name)\($0.joinPhrase)" }
            .joined()
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    static func releaseType(_ music: MusicDetail) -> String? {
        ([music.primaryType] + music.secondaryTypes.map(Optional.some))
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty
    }

    static func duration(_ milliseconds: Int?) -> String? {
        guard let milliseconds, milliseconds >= 0 else { return nil }
        let seconds = milliseconds / 1_000
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    static func countryName(_ code: String?, locale: Locale = .current) -> String? {
        guard let code = code?.nilIfEmpty else { return nil }
        if code.caseInsensitiveCompare("XW") == .orderedSame {
            return "Worldwide"
        }
        return locale.localizedString(forRegionCode: code.uppercased()) ?? code
    }

    static func format(_ release: MusicRepresentativeRelease) -> String? {
        if let format = release.format?.nilIfEmpty {
            return format
        }
        var seen = Set<String>()
        return release.media
            .compactMap { $0.format?.nilIfEmpty }
            .filter { seen.insert($0.lowercased()).inserted }
            .joined(separator: ", ")
            .nilIfEmpty
    }

    static func differingArtistCredit(
        track: MusicTrack,
        albumCredits: [MusicArtistCredit]
    ) -> String? {
        guard let trackArtist = artistCreditText(track.artistCredit) else { return nil }
        guard let albumArtist = artistCreditText(albumCredits) else { return trackArtist }
        return trackArtist.caseInsensitiveCompare(albumArtist) == .orderedSame ? nil : trackArtist
    }

    static func trackAccessibilityLabel(track: MusicTrack, albumCredits: [MusicArtistCredit]) -> String {
        var values = ["Disc \(track.discNumber), track \(track.number)", track.title]
        if let artist = artistCreditText(track.artistCredit) ?? artistCreditText(albumCredits) {
            values.append("by \(artist)")
        }
        if let duration = duration(track.lengthMs) {
            values.append(duration)
        }
        return values.joined(separator: ", ")
    }

    static func streamingDestinations(_ links: [MusicStreamingLink]) -> [MusicStreamingDestination] {
        var seen = Set<String>()
        return links.compactMap { streamingDestination($0) }.filter { seen.insert($0.id).inserted }
    }

    static func musicBrainzURL(releaseGroupMbid: String) -> URL {
        URL(string: "https://musicbrainz.org")!
            .appending(path: "release-group")
            .appending(path: releaseGroupMbid)
    }

    private static func streamingDestination(_ link: MusicStreamingLink) -> MusicStreamingDestination? {
        guard
            let url = URL(string: link.url),
            ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
            let rawHost = url.host?.lowercased(),
            !rawHost.isEmpty
        else { return nil }
        let host = rawHost.hasPrefix("www.") ? String(rawHost.dropFirst(4)) : rawHost

        let label: String
        if host == "music.apple.com" {
            label = "Apple Music"
        } else if host == "open.spotify.com" {
            label = "Spotify"
        } else if host == "qobuz.com" || host.hasSuffix(".qobuz.com") {
            label = "Qobuz"
        } else {
            return nil
        }
        return MusicStreamingDestination(label: label, url: url)
    }
}

private struct TopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

struct MediaDetailView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var posterTransitionNamespace
    @State private var selectedID: MediaRef.ID?
    @State private var presentedPoster: PosterViewerItem?

    private let ref: MediaRef
    private let browsingContext: MediaBrowsingContext?
    private let mediaRepository: MediaRepository
    private let musicRepository: MusicRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let companyRepository: CompanyRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let onReturnToOriginSeason: (() -> Void)?

    init(
        ref: MediaRef,
        browsingContext: MediaBrowsingContext? = nil,
        mediaRepository: MediaRepository,
        musicRepository: MusicRepository = AppRepositories.current().music,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        companyRepository: CompanyRepository = AppRepositories.current().companies,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {},
        onReturnToOriginSeason: (() -> Void)? = nil
    ) {
        self.ref = ref
        self.browsingContext = browsingContext
        self.mediaRepository = mediaRepository
        self.musicRepository = musicRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.companyRepository = companyRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.onReturnToOriginSeason = onReturnToOriginSeason
        _selectedID = State(initialValue: browsingContext?.selectedID ?? ref.id)
    }

    var body: some View {
        ZStack {
            GeometryReader { safeAreaProxy in
                Group {
                    if let browsingContext, browsingContext.refs.count > 1 {
                        ScrollViewReader { scrollProxy in
                            ScrollView(.horizontal) {
                                LazyHStack(spacing: 0) {
                                    ForEach(Array(browsingContext.refs.enumerated()), id: \.element.id) { index, pageRef in
                                        detailPage(
                                            ref: pageRef,
                                            shouldLoad: abs(index - selectedIndex(in: browsingContext)) <= 1,
                                            topSafeAreaInset: safeAreaProxy.safeAreaInsets.top
                                        )
                                        .containerRelativeFrame(.horizontal)
                                        .accessibilityHidden(pageRef.id != selectedID)
                                        .id(pageRef.id)
                                    }
                                }
                                .scrollTargetLayout()
                            }
                            .scrollIndicators(.hidden)
                            .scrollTargetBehavior(.paging)
                            .scrollPosition(id: $selectedID, anchor: .center)
                            .task(id: browsingContext.selectedID) {
                                await Task.yield()
                                scrollProxy.scrollTo(browsingContext.selectedID, anchor: .center)
                            }
                            .ignoresSafeArea(edges: .top)
                        }
                    } else {
                        detailPage(
                            ref: ref,
                            shouldLoad: true,
                            topSafeAreaInset: safeAreaProxy.safeAreaInsets.top
                        )
                    }
                }
                .ignoresSafeArea(edges: .top)
                .scrollDisabled(presentedPoster != nil)
                .accessibilityHidden(presentedPoster != nil)
            }

            if let presentedPoster {
                PosterViewer(
                    item: presentedPoster,
                    namespace: posterTransitionNamespace,
                    onDismiss: dismissPoster
                )
                .zIndex(100)
            }
        }
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
    }

    private func selectedIndex(in context: MediaBrowsingContext) -> Int {
        context.refs.firstIndex(where: { $0.id == selectedID }) ?? 0
    }

    private func detailPage(
        ref: MediaRef,
        shouldLoad: Bool,
        topSafeAreaInset: CGFloat? = nil
    ) -> some View {
        MediaDetailPageView(
            ref: ref,
            shouldLoad: shouldLoad,
            topSafeAreaInset: topSafeAreaInset,
            mediaRepository: mediaRepository,
            musicRepository: musicRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            listRepository: listRepository,
            peopleRepository: peopleRepository,
            companyRepository: companyRepository,
            currentUserId: currentUserId,
            selectedTab: selectedTab,
            posterTransitionNamespace: posterTransitionNamespace,
            presentedPosterID: presentedPoster?.id,
            onOpenPoster: presentPoster,
            onSelectTab: onSelectTab,
            onUnauthorized: onUnauthorized,
            onReturnToOriginSeason: onReturnToOriginSeason
        )
    }

    private func presentPoster(_ poster: PosterViewerItem) {
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.88)) {
            presentedPoster = poster
        }
    }

    private func dismissPoster() {
        withAnimation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.9)) {
            presentedPoster = nil
        }
    }
}

// Keep content evaluation outside the page's presentation-modifier stack.
// Calling a computed view here eagerly overflowed the 1 MB iPhone main-thread stack.
private struct MediaDetailScrollContent<Hero: View, Content: View>: View {
    let viewModel: MediaDetailViewModel
    let mediaType: String
    let topSafeAreaInset: CGFloat
    let hero: (MediaDetail) -> Hero
    let content: (MediaDetail) -> Content

    var body: some View {
        Group {
            if viewModel.isLoading, viewModel.detail == nil {
                if mediaType == "episode" {
                    EpisodeDetailLoadingView()
                } else {
                    ProgressView()
                        .tint(.white)
                        .frame(maxWidth: .infinity, minHeight: 520)
                }
            } else if let detail = viewModel.detail {
                VStack(spacing: 0) {
                    hero(detail)
                        .padding(.top, -topSafeAreaInset)
                    content(detail)
                }
            } else if let error = viewModel.errorMessage, viewModel.detail == nil {
                VStack(spacing: 18) {
                    ContentUnavailableView("Could not load media", systemImage: "exclamationmark.triangle", description: Text(error))
                        .foregroundStyle(.white)
                    Button("Try Again") {
                        Task { await viewModel.load() }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.white.opacity(0.16))
                }
                .padding()
                .frame(maxWidth: .infinity, minHeight: 520)
            }
        }
        .padding(.bottom, 116)
    }
}

private struct MediaDetailPageView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var viewModel: MediaDetailViewModel
    @State private var presentedSheet: MediaDetailSheet?
    @State private var presentedRef: MediaRef?
    @State private var presentedMediaSelection: MediaBrowsingSelection?
    @State private var presentedDiaryEntry: PresentedDiaryEntry?
    @State private var presentedMediaDiary: PresentedMediaDiary?
    @State private var presentedDiscover: MediaDiscoverRequest?
    @State private var presentedSeries: SeriesRef?
    @State private var presentedPerson: PersonRef?
    @State private var presentedCompany: CompanyRef?
    @State private var presentedSong: MusicSongSelection?
    @State private var isPosterPickerPresented = false
    @State private var isBackdropPickerPresented = false
    @State private var isLogoPickerPresented = false
    @State private var isReviewsPresented = false
    @State private var isAniListReviewsPresented = false
    @State private var pendingPosterSave: PosterSaveResponse?
    @State private var pendingBackdropSave: BackdropSaveResponse?
    @State private var pendingLogoSave: LogoSaveResponse?
    @State private var isLogPresented = false
    @State private var completionJourneyId: Int?
    @State private var completionLiked: Bool?
    @State private var completionRatingSteps: Int?
    @State private var progressUpdateDetail: MediaDetail?
    @State private var editingBookJourney: BookJourneyState?
    @State private var editingGamePlaythrough: GamePlaythroughState?
    @State private var pendingGameProgress: GamePlaythroughState?
    @State private var pendingGameCompletion: GameCompletionPresentation?
    @State private var gameCompletionPresentation: GameCompletionPresentation?
    @State private var isQuickActionAlertPresented = false
    @State private var isLikeAlertPresented = false
    @State private var showsTitleLogo = true
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var edgeDragOffset: CGFloat = 0
    @State private var ratingPicker = MediaRatingPickerState()

    private let mediaRepository: MediaRepository
    private let musicRepository: MusicRepository
    private let trackingRepository: TrackingRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let companyRepository: CompanyRepository
    private let currentUserId: Int?
    private let selectedTab: AppTab
    private let posterTransitionNamespace: Namespace.ID
    private let presentedPosterID: PosterViewerItem.ID?
    private let onOpenPoster: (PosterViewerItem) -> Void
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let onReturnToOriginSeason: (() -> Void)?
    private let ref: MediaRef
    private let shouldLoad: Bool
    private let topSafeAreaInsetOverride: CGFloat?

    init(
        ref: MediaRef,
        shouldLoad: Bool,
        topSafeAreaInset: CGFloat? = nil,
        mediaRepository: MediaRepository,
        musicRepository: MusicRepository = AppRepositories.current().music,
        trackingRepository: TrackingRepository,
        diaryRepository: DiaryRepository,
        listRepository: ListRepository = AppRepositories.current().lists,
        peopleRepository: PeopleRepository = AppRepositories.current().people,
        companyRepository: CompanyRepository = AppRepositories.current().companies,
        currentUserId: Int? = nil,
        selectedTab: AppTab = .home,
        posterTransitionNamespace: Namespace.ID,
        presentedPosterID: PosterViewerItem.ID?,
        onOpenPoster: @escaping (PosterViewerItem) -> Void,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        onUnauthorized: @escaping () -> Void = {},
        onReturnToOriginSeason: (() -> Void)? = nil
    ) {
        self.ref = ref
        self.shouldLoad = shouldLoad
        topSafeAreaInsetOverride = topSafeAreaInset
        self.mediaRepository = mediaRepository
        self.musicRepository = musicRepository
        self.trackingRepository = trackingRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.companyRepository = companyRepository
        self.currentUserId = currentUserId
        self.selectedTab = selectedTab
        self.posterTransitionNamespace = posterTransitionNamespace
        self.presentedPosterID = presentedPosterID
        self.onOpenPoster = onOpenPoster
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.onReturnToOriginSeason = onReturnToOriginSeason
        _viewModel = State(initialValue: MediaDetailViewModel(
            ref: ref,
            mediaRepository: mediaRepository,
            trackingRepository: trackingRepository,
            diaryRepository: diaryRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                MediaDetailScrollContent(
                    viewModel: viewModel,
                    mediaType: ref.mediaType,
                    topSafeAreaInset: resolvedTopSafeAreaInset,
                    hero: hero,
                    content: content
                )
                    .spineContentTransition(value: contentPhase)
            }
            .onScrollPhaseChange { _, phase in
                if phase != .idle {
                    dismissRatingPicker()
                }
            }
            .simultaneousGesture(
                TapGesture().onEnded { dismissRatingPicker() }
            )
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)

            topButtons
                .padding(.horizontal, 16)
                .padding(.top, resolvedTopSafeAreaInset + 6)

            if progressUpdateDetail == nil, let detail = viewModel.detail {
                bottomActionRail(detail)
                .padding(.bottom, 8)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            }

            if progressUpdateDetail == nil {
                ExplorationHomeButton(glass: .clear.interactive())
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            }
        }
        .navigationBarBackButtonHidden()
        .offset(x: edgeDragOffset)
        .overlay(alignment: .leading) {
            if presentedPosterID == nil {
                Color.clear
                    .frame(width: 28)
                    .contentShape(Rectangle())
                    .gesture(edgeSwipeBackGesture)
            }
        }
        .overlay {
            progressUpdateOverlay
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: TopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(TopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
        .sheet(item: $presentedSheet, onDismiss: presentPendingGameSheet) { sheet in
            switch sheet {
            case .posterMenu:
                PosterMenuSheet(
                    posterLabel: "Customize Poster",
                    showsSeasonOption: parentSeasonRef(viewModel.detail) != nil,
                    showsTVShowOption: parentTVRef(viewModel.detail) != nil,
                    showsPosterOption: canCustomizePoster(viewModel.detail),
                    showsBackdropOption: canCustomizeBackdrop(viewModel.detail),
                    showsLogoOption: canCustomizeLogo(viewModel.detail),
                    onViewSeason: {
                        guard let ref = parentSeasonRef(viewModel.detail) else { return }
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            navigateToParent(ref)
                        }
                    },
                    onViewTVShow: {
                        guard let ref = parentTVRef(viewModel.detail) else { return }
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            presentedRef = ref
                        }
                    },
                    onAddToList: {
                        presentedSheet = .addToList
                    },
                    onCustomizePoster: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isPosterPickerPresented = true
                        }
                    },
                    onCustomizeBackdrop: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isBackdropPickerPresented = true
                        }
                    },
                    onCustomizeLogo: {
                        presentedSheet = nil
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            isLogoPickerPresented = true
                        }
                    }
                )
                .presentationDetents([.height(posterMenuHeight(for: viewModel.detail))])
                .presentationDragIndicator(.visible)
            case .bookGameActions:
                if let detail = viewModel.detail {
                    if detail.ref.mediaType == "game" {
                        GameActionSheet(status: currentStatus(detail), game: gameState(detail), isSaving: viewModel.isSavingQuickAction, errorMessage: viewModel.quickActionErrorMessage,
                            onAction: { await performQuickAction($0, for: detail, dismissSheet: true) },
                            onLog: { openBookCompletion(for: detail) },
                            onProgress: { openProgressUpdate(for: detail) },
                            onRemove: { if await viewModel.removeTracking(for: detail) { presentedSheet = nil; await viewModel.load() } })
                            .presentationDetents([.medium, .large])
                    } else {
                    BookGameActionSheet(
                        mediaType: detail.ref.mediaType,
                        status: currentStatus(detail),
                        bookState: bookState(detail),
                        isSaving: viewModel.isSavingQuickAction,
                        errorMessage: viewModel.quickActionErrorMessage,
                        onAction: { action in
                            await performQuickAction(action, for: detail, dismissSheet: true)
                        },
                        onRemove: {
                            if await viewModel.removeTracking(for: detail) {
                                presentedSheet = nil
                                await viewModel.load()
                            }
                        },
                        onUpdateProgress: {
                            openProgressUpdate(for: detail)
                        },
                        onLog: {
                            presentedSheet = nil
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                                if detail.ref.mediaType == "book" {
                                    openBookCompletion(for: detail)
                                } else {
                                    isLogPresented = true
                                }
                            }
                        }
                    )
                    .presentationDetents(detail.ref.mediaType == "book" ? [.medium, .large] : [.height(detail.ref.mediaType == "music" ? 292 : 224)])
                    .presentationDragIndicator(.visible)
                    }
                }
            case .addToList:
                if let detail = viewModel.detail {
                    AddToListSheet(
                        target: .media(detail.ref),
                        initialMedia: detail.listSummary,
                        listRepository: listRepository,
                        mediaRepository: mediaRepository,
                        peopleRepository: peopleRepository,
                        onUnauthorized: onUnauthorized
                    )
                }
            }
        }
        .sheet(item: $editingGamePlaythrough, onDismiss: presentPendingGameSheet) { playthrough in
            if let detail = viewModel.detail {
                GamePlaythroughEditor(playthrough: playthrough,
                    onSave: { await viewModel.updateGamePlaythrough(playthrough, request: $0, for: detail) },
                    onAction: { await viewModel.performGameAction($0, for: detail) },
                    onDelete: { await viewModel.deleteGamePlaythrough(playthrough, for: detail) },
                    onFinish: { openBookCompletion(for: detail) },
                    errorMessage: { viewModel.quickActionErrorMessage })
            }
        }
        .sheet(item: $editingBookJourney) { journey in
            if let detail = viewModel.detail {
                BookJourneyDateEditor(
                    journey: journey,
                    isSaving: viewModel.isSavingQuickAction,
                    errorMessage: viewModel.quickActionErrorMessage,
                    onSave: { startDate, endDate in
                        if await viewModel.updateBookJourney(
                            journey,
                            startDate: startDate,
                            endDate: endDate,
                            for: detail
                        ) {
                            editingBookJourney = nil
                            return true
                        }
                        return false
                    }
                )
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
            }
        }
        .alert("Tracking Update Failed", isPresented: $isQuickActionAlertPresented) {
            Button("OK") {
                viewModel.quickActionErrorMessage = nil
            }
        } message: {
            Text(viewModel.quickActionErrorMessage ?? "")
        }
        .alert("Like Update Failed", isPresented: $isLikeAlertPresented) {
            Button("OK") {
                viewModel.likeErrorMessage = nil
            }
        } message: {
            Text(viewModel.likeErrorMessage ?? "")
        }
        .fullScreenCover(isPresented: $isBackdropPickerPresented, onDismiss: applyPendingBackdropSave) {
            if let detail = viewModel.detail {
                BackdropPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    initialBackdropURL: detail.ref.mediaType == "manga"
                        ? detail.displayBackdropURL
                        : nil,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingBackdropSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(isPresented: $isLogoPickerPresented, onDismiss: applyPendingLogoSave) {
            if let detail = viewModel.detail {
                LogoPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingLogoSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(item: $gameCompletionPresentation, onDismiss: restoreCanonicalGameRating) { presentation in
            if let detail = viewModel.detail {
                MediaLogView(detail: detail, trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository, tracking: viewModel.tracking,
                    completionJourneyId: presentation.playthroughId,
                    preselectedLiked: presentation.liked,
                    preselectedRatingSteps: presentation.ratingSteps,
                    onUnauthorized: onUnauthorized) {
                        Task { await viewModel.load() }
                    }
            }
        }
        .fullScreenCover(isPresented: $isLogPresented) {
            if let detail = viewModel.detail {
                MediaLogView(
                    detail: detail,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    tracking: viewModel.tracking,
                    completionJourneyId: completionJourneyId,
                    preselectedLiked: completionLiked,
                    preselectedRatingSteps: completionRatingSteps,
                    onUnauthorized: onUnauthorized
                ) {
                    Task {
                        await viewModel.load()
                    }
                }
            }
        }
        .fullScreenCover(isPresented: $isPosterPickerPresented, onDismiss: applyPendingPosterSave) {
            if let detail = viewModel.detail {
                PosterPickerView(
                    ref: detail.ref,
                    mediaRepository: mediaRepository,
                    title: "Customize Poster",
                    showsLanguageFilter: !["book", "game", "music"].contains(detail.ref.mediaType),
                    contentMode: isBook(detail) ? .fit : .fill,
                    onUnauthorized: onUnauthorized
                ) { response in
                    pendingPosterSave = response
                    presentedSheet = nil
                }
            }
        }
        .fullScreenCover(isPresented: $isReviewsPresented) {
            if let detail = viewModel.detail {
                MediaReviewsView(
                    detail: detail,
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .fullScreenCover(isPresented: $isAniListReviewsPresented) {
            if let detail = viewModel.detail {
                AniListReviewsView(
                    detail: detail,
                    mediaRepository: mediaRepository
                )
            }
        }
        .fullScreenCover(item: $presentedRef) { ref in
            MediaDetailView(
                ref: ref,
                mediaRepository: mediaRepository,
                musicRepository: musicRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedMediaSelection) { selection in
            MediaDetailView(
                ref: selection.ref,
                browsingContext: selection.context,
                mediaRepository: mediaRepository,
                musicRepository: musicRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized,
                onReturnToOriginSeason: {
                    presentedMediaSelection = nil
                }
            )
        }
        .fullScreenCover(item: $presentedPerson) { person in
            PersonDetailView(
                ref: person,
                peopleRepository: peopleRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedCompany) { company in
            CompanyDetailView(
                ref: company,
                companyRepository: companyRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedDiaryEntry) { entry in
            DiaryLogDetailNavigationCover(
                entryId: entry.id,
                diaryRepository: diaryRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedMediaDiary) { diary in
            if let itemId = diary.detail.ref.itemId {
                MediaDiaryView(
                    title: diary.title,
                    itemId: itemId,
                    posterURL: diary.detail.displayPosterURL,
                    posterOrientation: diary.detail.posterOrientation,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .fullScreenCover(item: $presentedDiscover) { request in
            MediaDiscoverView(
                request: request,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedSeries) { series in
            SeriesDetailView(
                ref: series,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .fullScreenCover(item: $presentedSong) { selection in
            SongDetailView(
                selection: selection,
                musicRepository: musicRepository,
                mediaRepository: mediaRepository,
                trackingRepository: trackingRepository,
                diaryRepository: diaryRepository,
                listRepository: listRepository,
                peopleRepository: peopleRepository,
                companyRepository: companyRepository,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: onSelectTab,
                onUnauthorized: onUnauthorized
            )
        }
        .task(id: shouldLoad) {
            if shouldLoad, viewModel.detail == nil {
                await viewModel.load()
            }
        }
        .task(id: viewModel.externalRatingsPollingID) {
            await viewModel.pollExternalRatingsIfNeeded()
        }
        .onChange(of: viewModel.detail?.id) {
            showsTitleLogo = true
        }
        .onChange(of: viewModel.detail?.userState?.rating) { _, rating in
            ratingPicker.syncConfirmed(ratingHalfSteps(rating))
        }
        .onReceive(NotificationCenter.default.publisher(for: .diaryEntriesDidChange)) { _ in
            Swift.Task<Void, Never> { await viewModel.load() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { notification in
            guard let changedRef = notification.userInfo?["ref"] as? MediaRef,
                  let detail = viewModel.detail,
                  completionMayHaveChanged(in: detail, for: changedRef)
            else { return }
            Swift.Task<Void, Never> { await viewModel.load() }
        }
    }

    private func completionMayHaveChanged(in detail: MediaDetail, for changedRef: MediaRef) -> Bool {
        if changedRef.id == ref.id {
            return true
        }
        if ["tv", "season"].contains(detail.ref.mediaType),
           detail.ref.source == changedRef.source,
           detail.ref.mediaId == changedRef.mediaId {
            return true
        }
        return false
    }

    @ViewBuilder
    private var progressUpdateOverlay: some View {
        if let detail = progressUpdateDetail {
            ProgressUpdateSheet(
                detail: detail,
                progress: currentProgress(detail),
                isSaving: viewModel.isSavingProgress,
                errorMessage: viewModel.progressErrorMessage,
                journeyStatus: viewModel.tracking?.book?.currentJourney?.status,
                onSave: { request in
                    await viewModel.saveProgress(request, for: detail)
                },
                onDismiss: {
                    progressUpdateDetail = nil
                },
                onLogFinished: {
                    progressUpdateDetail = nil
                    openBookCompletion(for: detail)
                },
                onPause: {
                    await viewModel.performBookAction("pause", for: detail)
                },
                onDrop: {
                    await viewModel.performBookAction("drop", for: detail)
                },
                onRestart: {
                    await viewModel.performBookAction("restart", for: detail)
                },
                onDelete: {
                    guard let journey = viewModel.tracking?.book?.currentJourney else { return false }
                    return await viewModel.deleteBookJourney(journey, for: detail)
                }
            )
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.detail != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private func applyPendingPosterSave() {
        guard let response = pendingPosterSave else { return }
        pendingPosterSave = nil
        viewModel.applyPosterSave(response)
    }

    private func applyPendingBackdropSave() {
        guard let response = pendingBackdropSave else { return }
        pendingBackdropSave = nil
        viewModel.applyBackdropSave(response)
    }

    private func applyPendingLogoSave() {
        guard let response = pendingLogoSave else { return }
        pendingLogoSave = nil
        viewModel.applyLogoSave(response)
        showsTitleLogo = true
    }

    private var edgeSwipeBackGesture: some Gesture {
        DragGesture(minimumDistance: 12, coordinateSpace: .global)
            .onChanged { value in
                guard value.translation.width > 0 else { return }
                edgeDragOffset = value.translation.width
            }
            .onEnded { value in
                if value.translation.width > 90 {
                    dismiss()
                } else {
                    withAnimation(reduceMotion ? nil : .spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }

    private var topButtons: some View {
        HStack {
            CircleIconButton(systemName: "chevron.left", label: "Back") {
                dismissRatingPicker()
                dismiss()
            }
            Spacer()
            if viewModel.detail != nil {
                CircleIconButton(systemName: "ellipsis", label: "More") {
                    dismissRatingPicker()
                    presentedSheet = .posterMenu
                }
            }
        }
    }

    private func dismissRatingPicker() {
        guard ratingPicker.isPresented else { return }
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            ratingPicker.dismiss()
        }
    }

    private var resolvedTopSafeAreaInset: CGFloat {
        topSafeAreaInsetOverride ?? topSafeAreaInset
    }

    private func posterMenuHeight(for detail: MediaDetail?) -> CGFloat {
        let optionalRows = [
            parentSeasonRef(detail) != nil,
            parentTVRef(detail) != nil,
            canCustomizePoster(detail),
            canCustomizeBackdrop(detail),
            canCustomizeLogo(detail),
        ].filter { $0 }.count
        return 80 + CGFloat(optionalRows * 68)
    }

    private func canCustomizePoster(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsPoster(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func canCustomizeBackdrop(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsBackdrop(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func canCustomizeLogo(_ detail: MediaDetail?) -> Bool {
        guard let detail else { return false }
        return MediaArtworkCustomization.supportsLogo(
            source: detail.ref.source,
            mediaType: detail.ref.mediaType
        )
    }

    private func isBook(_ detail: MediaDetail) -> Bool {
        detail.ref.mediaType == "book"
    }

    private func parentTVRef(_ detail: MediaDetail?) -> MediaRef? {
        guard let detail, ["season", "episode"].contains(detail.ref.mediaType) else { return nil }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "tv",
            mediaId: detail.ref.mediaId,
            seasonNumber: nil,
            episodeNumber: nil
        )
    }

    private func parentSeasonRef(_ detail: MediaDetail?) -> MediaRef? {
        guard
            let detail,
            detail.ref.mediaType == "episode",
            let seasonNumber = detail.ref.seasonNumber
        else { return nil }
        return MediaRef(
            itemId: nil,
            source: detail.ref.source,
            mediaType: "season",
            mediaId: detail.ref.mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: nil
        )
    }

    private func navigateToParent(_ ref: MediaRef) {
        if ref.mediaType == "season", let onReturnToOriginSeason {
            onReturnToOriginSeason()
        } else {
            presentedRef = ref
        }
    }

    private func usesBookGameActions(_ detail: MediaDetail) -> Bool {
        ["book", "game"].contains(detail.ref.mediaType)
    }

    private func trackAction(for detail: MediaDetail) {
        if usesBookGameActions(detail) {
            presentedSheet = .bookGameActions
        } else {
            isLogPresented = true
        }
    }

    private func eyeAction(for detail: MediaDetail) {
        if detail.ref.isSingleWeight {
            Task {
                let succeeded: Bool
                if isEyeCompleted(detail) {
                    succeeded = await viewModel.removeTracking(for: detail)
                    if succeeded {
                        ratingPicker.resetAfterUnwatch()
                        await viewModel.load()
                    }
                } else {
                    succeeded = await viewModel.markConsumed(for: detail)
                }
                if !succeeded {
                    ratingPicker.rollbackWatch()
                    isQuickActionAlertPresented = true
                }
            }
            return
        }
        if detail.ref.mediaType == "game" {
            if gameState(detail)?.hasLivePlaythrough == true {
                openBookCompletion(for: detail)
            } else {
                let action = isEyeCompleted(detail) ? "undo_completed" : "mark_completed"
                Task {
                    if await viewModel.performGameAction(action, for: detail) {
                        if action == "undo_completed" { ratingPicker.resetAfterUnwatch() }
                    } else { isQuickActionAlertPresented = true }
                }
            }
            return
        }
        if detail.ref.mediaType == "book" {
            if bookState(detail)?.hasLiveJourney == true {
                openBookCompletion(for: detail)
            } else if isEyeCompleted(detail) {
                if bookState(detail)?.supports("undo_read") == true {
                    Task {
                        if await viewModel.performBookAction("undo_read", for: detail) {
                            ratingPicker.resetAfterUnwatch()
                        } else {
                            isQuickActionAlertPresented = true
                        }
                    }
                } else {
                    viewModel.quickActionErrorMessage = bookState(detail)?.actionReasons["undo_read"]
                        ?? "Delete the completion log to mark this book unread."
                    isQuickActionAlertPresented = true
                }
            } else {
                Task {
                    if !(await viewModel.performBookAction("mark_read", for: detail)) {
                        isQuickActionAlertPresented = true
                    }
                }
            }
            return
        }
        guard !isEyeCompleted(detail) else { return }
        if detail.ref.mediaType == "episode" {
            Task {
                let succeeded = await viewModel.watchEpisode(detail)
                if !succeeded {
                    isQuickActionAlertPresented = true
                }
            }
            return
        }
        guard usesBookGameActions(detail) else { return }
        Task {
            await performQuickAction(.finished, for: detail, dismissSheet: false)
        }
    }

    private func isEyeCompleted(_ detail: MediaDetail) -> Bool {
        if detail.ref.mediaType == "episode" {
            return detail.userState?.isTracked == true
        }
        return currentStatus(detail)?.caseInsensitiveCompare("Completed") == .orderedSame
    }

    private func performQuickAction(_ action: MediaDetailQuickAction, for detail: MediaDetail, dismissSheet: Bool) async {
        if await viewModel.performQuickAction(action, for: detail) {
            if dismissSheet {
                presentedSheet = nil
            }
            await viewModel.load()
        } else if !dismissSheet {
            isQuickActionAlertPresented = true
        }
    }

    private func likeAction(for detail: MediaDetail) {
        if ["book", "game"].contains(detail.ref.mediaType),
           detail.userState?.hasLiked != true,
           (bookState(detail)?.hasLiveJourney == true || gameState(detail)?.hasLivePlaythrough == true) {
            openBookCompletion(for: detail, liked: true)
            return
        }
        Task {
            let succeeded = await viewModel.toggleMediaLike(for: detail)
            if !succeeded {
                ratingPicker.rollbackWatch()
                isLikeAlertPresented = true
            }
        }
    }

    private func ratingAction(for detail: MediaDetail, halfSteps: Int) {
        guard detail.ref.usesFiveStarRatingScale else { return }
        if halfSteps > 0, bookState(detail)?.hasLiveJourney == true || (halfSteps > 0 && gameState(detail)?.hasLivePlaythrough == true) {
            ratingPicker.syncConfirmed(ratingHalfSteps(detail.userState?.rating))
            ratingPicker.rollbackWatch()
            openBookCompletion(for: detail, ratingSteps: halfSteps)
            return
        }
        Task {
            if !(await viewModel.setCurrentRating(for: detail, halfSteps: halfSteps)) {
                ratingPicker.syncConfirmed(ratingHalfSteps(detail.userState?.rating))
                isQuickActionAlertPresented = true
            }
        }
    }

    private func ratingHalfSteps(_ rating: String?) -> Int {
        guard let rating, let value = Decimal(string: rating) else { return 0 }
        let steps = viewModel.detail?.ref.usesFiveStarRatingScale == true ? value * 2 : value
        return NSDecimalNumber(decimal: steps).intValue
    }

    private func gameState(_ detail: MediaDetail) -> GameTrackingState? {
        viewModel.tracking?.game ?? detail.userState?.game
    }

    private func bookState(_ detail: MediaDetail) -> BookTrackingState? {
        viewModel.tracking?.book ?? detail.userState?.book
    }

    private func openBookCompletion(for detail: MediaDetail, liked: Bool? = nil, ratingSteps: Int? = nil) {
        if detail.ref.mediaType == "game" {
            let presentation = GameCompletionPresentation(
                playthroughId: gameState(detail)?.hasLivePlaythrough == true ? gameState(detail)?.currentPlaythrough?.id : nil,
                liked: liked, ratingSteps: ratingSteps)
            if presentedSheet != nil || editingGamePlaythrough != nil {
                pendingGameCompletion = presentation
                presentedSheet = nil
                editingGamePlaythrough = nil
            } else { gameCompletionPresentation = presentation }
            return
        }
        completionJourneyId = bookState(detail)?.currentJourney?.id
        completionLiked = liked
        completionRatingSteps = ratingSteps
        presentedSheet = nil
        progressUpdateDetail = nil
        isLogPresented = true
    }

    private func openProgressUpdate(for detail: MediaDetail) {
        viewModel.progressErrorMessage = nil
        if detail.ref.mediaType == "game" {
            let playthrough = gameState(detail)?.currentPlaythrough
            if presentedSheet != nil {
                pendingGameProgress = playthrough
                presentedSheet = nil
            } else { editingGamePlaythrough = playthrough }
        } else {
            presentedSheet = nil
            progressUpdateDetail = detail
        }
    }

    private func restoreCanonicalGameRating() {
        ratingPicker.dismiss()
        ratingPicker.syncConfirmed(ratingHalfSteps(viewModel.detail?.userState?.rating))
        ratingPicker.rollbackWatch()
    }

    private func presentPendingGameSheet() {
        if let playthrough = pendingGameProgress {
            pendingGameProgress = nil
            editingGamePlaythrough = playthrough
        } else if let presentation = pendingGameCompletion {
            pendingGameCompletion = nil
            gameCompletionPresentation = presentation
        }
    }

    private func openPosterPicker(for detail: MediaDetail) {
        guard canCustomizePoster(detail) else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isPosterPickerPresented = true
    }

    private func openBackdropPicker(for detail: MediaDetail) {
        guard canCustomizeBackdrop(detail) else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isBackdropPickerPresented = true
    }

    private func openLogoPicker(for detail: MediaDetail) {
        guard canCustomizeLogo(detail), detail.displayLogoURL != nil else { return }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        isLogoPickerPresented = true
    }

    private func isShowingTitleLogo(_ detail: MediaDetail) -> Bool {
        showsTitleLogo && supportsTitleLogo(detail)
    }

    private func bottomActionRail(_ detail: MediaDetail) -> some View {
        let isEpisode = detail.ref.mediaType == "episode"
        let isWatched = detail.userState?.isTracked == true
        let usesQuickActions = usesBookGameActions(detail)

        return ActionRail(
            ratingPicker: $ratingPicker,
            isTracked: isEpisode ? isWatched : currentStatus(detail) != nil,
            isLiked: detail.userState?.hasLiked ?? false,
            showsEye: true,
            showsRating: (detail.ref.isSingleWeight && isEyeCompleted(detail)) || ["book", "game"].contains(detail.ref.mediaType),
            offersRatingAfterBaseAction: detail.ref.isSingleWeight
                || (detail.ref.mediaType == "book" && bookState(detail)?.hasLiveJourney != true)
                || (detail.ref.mediaType == "game" && gameState(detail)?.hasLivePlaythrough != true),
            trackLabel: isEpisode ? "Log episode" : (usesQuickActions ? "Track" : nil),
            eyeLabel: isEpisode
                ? (isWatched ? "Episode watched" : "Mark episode watched")
                : detail.ref.isSingleWeight
                    ? (detail.ref.mediaType == "music"
                        ? (isEyeCompleted(detail) ? "Listened" : "Mark as listened")
                        : (isEyeCompleted(detail) ? "Watched" : "Mark as watched"))
                    : (usesQuickActions ? bookGameCopy(for: detail.ref.mediaType).finished : nil),
            isEyeSelected: isEyeCompleted(detail),
            isEyeLoading: (isEpisode || usesQuickActions || detail.ref.isSingleWeight)
                && viewModel.isSavingQuickAction,
            isLikeLoading: viewModel.isSavingLike,
            onTrack: { trackAction(for: detail) },
            onLike: { likeAction(for: detail) },
            onEye: { eyeAction(for: detail) },
            onRating: { ratingAction(for: detail, halfSteps: $0) }
        )
    }

    @ViewBuilder
    private func hero(_ detail: MediaDetail) -> some View {
        if detail.ref.mediaType == "music" {
            musicHero(detail)
        } else if detail.ref.mediaType == "episode" {
            episodeHero(detail)
        } else {
            heroHeader(detail)
                .padding(.horizontal, 14)
                .padding(.bottom, 8)
                .padding(.top, resolvedTopSafeAreaInset + heroPosterTopOffset(for: detail))
                .background {
                    ZStack(alignment: .top) {
                        HeroArtwork(detail: detail)
                        if let backdropURL = backdropURLString(for: detail) {
                            GeometryReader { proxy in
                                BackdropArtwork(urlString: backdropURL)
                                    .frame(height: min(
                                        resolvedTopSafeAreaInset + MediaDetailLayout.backdropHeight,
                                        proxy.size.width / MediaDetailLayout.backdropCropAspectRatio
                                    ))
                                    .onLongPressGesture {
                                        openBackdropPicker(for: detail)
                                    }
                            }
                        }
                    }
                    .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                }
        }
    }

    private func musicHero(_ detail: MediaDetail) -> some View {
        let artworkScale: CGFloat = 1.28
        let artworkSize = MediaDetailLayout.heroPosterWidth * artworkScale

        return VStack(spacing: 0) {
            VStack(spacing: 0) {
                heroPoster(detail)
                    .scaleEffect(artworkScale)
                    .frame(width: artworkSize, height: artworkSize)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 33, weight: .heavy),
                        lineLimit: nil,
                        minimumScaleFactor: 0.66,
                        maxLogoHeight: 48
                    )

                    if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(credits, textAlignment: .leading)
                    } else if let artist = musicArtist(detail) {
                        bylineText(artist, lineLimit: 1, alignment: .leading)
                    }

                    musicHeroChips(detail)
                    RatingChipRow(chips: ratingChips(detail), stacked: false)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 14)
            .padding(.top, resolvedTopSafeAreaInset + MediaDetailLayout.heroPosterTopOffset)
            .padding(.bottom, 18)
            .background {
                HeroArtwork(detail: detail)
                    .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
            }

            MusicStreamingButtons(
                links: detail.music?.representativeRelease?.streamingLinks ?? []
            )
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.top, 12)
            .padding(.bottom, 28)
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func musicHeroChips(_ detail: MediaDetail) -> some View {
        let genres = detailArray(detail, "genres").map { genre in
            MediaDetailChip(
                label: genre,
                discoverRequest: discoverRequest(detail, filter: .genre(genre))
            )
        }
        let release = [
            detail.music.flatMap { MusicAlbumPresentation.releaseType($0) },
            formattedDate(detail.music?.firstReleaseDate),
        ].compactMap { $0?.nilIfEmpty }.map { value in
            MediaDetailChip(label: value, discoverRequest: nil)
        }

        VStack(spacing: 8) {
            musicHeroChipRow(genres)
            musicHeroChipRow(release)
        }
    }

    @ViewBuilder
    private func musicHeroChipRow(_ chips: [MediaDetailChip]) -> some View {
        if !chips.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    ForEach(chips) { chip in
                        genreChip(chip)
                    }
                }
                .fixedSize(horizontal: true, vertical: false)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .contentMargins(.horizontal, 2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func musicArtist(_ detail: MediaDetail) -> String? {
        guard let music = detail.music else { return detail.subtitle?.nilIfEmpty }
        return MusicAlbumPresentation.artistCreditText(music.artistCredit)
            ?? detail.subtitle?.nilIfEmpty
            ?? detailString(detail, "artist")
    }

    private func musicProviderAttribution(_ detail: MediaDetail) -> String {
        guard let music = detail.music, !music.coverArt.fallbackUsed else {
            return "Metadata by MusicBrainz"
        }
        return "Metadata by MusicBrainz · Cover art by Cover Art Archive"
    }

    @ViewBuilder
    private func episodeHero(_ detail: MediaDetail) -> some View {
        if dynamicTypeSize.isAccessibilitySize {
            VStack(alignment: .leading, spacing: 0) {
                EpisodeHeroArtwork(
                    urlString: detail.episodeStillURL,
                    title: detail.title
                )
                .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeAccessibilityArtworkHeight)
                .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                .onLongPressGesture {
                    openBackdropPicker(for: detail)
                }

                episodeHeroContent(detail, overlaysArtwork: false)
                    .padding(.horizontal, 16)
                    .padding(.top, 18)
                    .padding(.bottom, 24)
            }
        } else {
            ZStack(alignment: .bottomLeading) {
                EpisodeHeroArtwork(
                    urlString: detail.episodeStillURL,
                    title: detail.title
                )
                .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeHeroHeight)
                .offset(y: BackdropLayout.safeAreaCompensation(for: resolvedTopSafeAreaInset))
                .onLongPressGesture {
                    openBackdropPicker(for: detail)
                }

                episodeHeroContent(detail, overlaysArtwork: true)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                    .offset(y: MediaDetailLayout.backdropTopSpacing)
            }
            .frame(height: resolvedTopSafeAreaInset + MediaDetailLayout.episodeHeroHeight)
            .padding(.bottom, MediaDetailLayout.backdropTopSpacing)
        }
    }

    private func episodeHeroContent(_ detail: MediaDetail, overlaysArtwork: Bool) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(episodeEyebrow(detail))
                .font(.caption.weight(.heavy))
                .foregroundStyle(.white.opacity(0.7))
                .textCase(.uppercase)
                .tracking(0.7)

            Text(detail.title)
                .font(.largeTitle.weight(.black))
                .foregroundStyle(.white)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 3)
                .minimumScaleFactor(0.72)
                .accessibilityAddTraits(.isHeader)

            if let context = episodeParentContext(detail) {
                Group {
                    if let parent = parentSeasonRef(detail) ?? parentTVRef(detail) {
                        Button {
                            navigateToParent(parent)
                        } label: {
                            episodeParentLabel(context)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(context)")
                    } else {
                        episodeParentLabel(context)
                    }
                }
            }

            episodeMetadata(detail)
            episodeRatings(detail)
        }
        .padding(.top, overlaysArtwork ? 56 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func episodeParentLabel(_ context: String) -> some View {
        HStack(spacing: 6) {
            Text(context)
                .font(.subheadline.weight(.semibold))
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.bold))
        }
        .foregroundStyle(.white.opacity(0.78))
    }

    @ViewBuilder
    private func episodeMetadata(_ detail: MediaDetail) -> some View {
        let values = [
            formattedDate(detailString(detail, "air_date") ?? detail.releaseDate),
            detailString(detail, "runtime"),
        ].compactMap { $0?.nilIfEmpty }

        if !values.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 7) {
                    ForEach(Array(values.enumerated()), id: \.offset) { index, value in
                        if index > 0 {
                            Circle()
                                .fill(.white.opacity(0.42))
                                .frame(width: 3, height: 3)
                        }
                        Text(value)
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    ForEach(values, id: \.self) { value in
                        Text(value)
                    }
                }
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.66))
        }
    }

    @ViewBuilder
    private func episodeRatings(_ detail: MediaDetail) -> some View {
        let chips = ratingChips(detail)
        let hasIMDbRating = (detail.externalRatings ?? []).contains {
            $0.source.caseInsensitiveCompare("IMDb") == .orderedSame && !$0.value.isEmpty
        }

        VStack(alignment: .leading, spacing: 8) {
            RatingChipRow(chips: chips, stacked: true)
            if !hasIMDbRating, let destination = episodeIMDbURL(detail) {
                IMDbExternalLinkPill(destination: destination)
            }
        }
    }

    private func episodeEyebrow(_ detail: MediaDetail) -> String {
        let season = detail.ref.seasonNumber.map { "S\($0)" }
        let episode = detail.ref.episodeNumber.map { "E\($0)" }
        return [season, episode].compactMap { $0 }.joined(separator: " · ")
    }

    private func episodeParentContext(_ detail: MediaDetail) -> String? {
        let showTitle = detailString(detail, "show_title")
            ?? detailString(detail, "series_title")
            ?? detailString(detail, "parent_title")
            ?? detail.subtitle
        let seasonTitle = detailString(detail, "season_title")
            ?? detail.ref.seasonNumber.map { "Season \($0)" }
        return [showTitle, seasonTitle]
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
            .nilIfEmpty
    }

    private func episodeIMDbURL(_ detail: MediaDetail) -> URL? {
        if let destination = detail.externalRatings?.first(where: {
            $0.source.caseInsensitiveCompare("IMDb") == .orderedSame
        })?.destinationURL {
            return destination
        }
        for key in ["imdb_url", "imdb_link"] {
            if let value = detailString(detail, key), let destination = URL(string: value) {
                return destination
            }
        }
        return nil
    }

    private func titleDisplay(
        detail: MediaDetail,
        title: String,
        showsLogo: Binding<Bool>,
        font: Font,
        lineLimit: Int?,
        minimumScaleFactor: CGFloat,
        maxLogoHeight: CGFloat
    ) -> some View {
        MediaTitleDisplay(
            detail: detail,
            title: title,
            showsLogo: showsLogo,
            font: font,
            lineLimit: lineLimit,
            minimumScaleFactor: minimumScaleFactor,
            maxLogoHeight: maxLogoHeight,
            onTap: parentTVRef(detail).map { ref in
                { presentedRef = ref }
            },
            onLongPress: canCustomizeLogo(detail) && detail.displayLogoURL != nil
                ? { openLogoPicker(for: detail) }
                : nil
        )
    }

    @ViewBuilder
    private func heroHeader(_ detail: MediaDetail) -> some View {
        if backdropURLString(for: detail) != nil {
            HStack(alignment: .top, spacing: 14) {
                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 32, weight: .black),
                        lineLimit: 3,
                        minimumScaleFactor: 0.72,
                        maxLogoHeight: 44
                    )

                    if let credits = heroCompanyCredits(detail) {
                        companyCreditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "anime" ? .leading : nil
                        )
                    } else if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "manga" ? .leading : nil
                        )
                    } else if let byline = byline(detail) {
                        bylineView(byline, detail: detail, lineLimit: 2)
                    }

                    genreChips(detail, wrapsAfterThird: true)
                    RatingChipRow(chips: ratingChips(detail), stacked: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 10)

                heroPoster(detail)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
        } else {
            VStack(spacing: 0) {
                heroPoster(detail)
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 16)

                VStack(alignment: .leading, spacing: 11) {
                    titleDisplay(
                        detail: detail,
                        title: detail.displayTitle,
                        showsLogo: $showsTitleLogo,
                        font: .system(size: 33, weight: .heavy),
                        lineLimit: nil,
                        minimumScaleFactor: 0.66,
                        maxLogoHeight: 48
                    )

                    if let credits = heroCompanyCredits(detail) {
                        companyCreditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "anime" ? .leading : nil
                        )
                    } else if let credits = MediaCreditPresentation.make(for: detail) {
                        creditBylineView(
                            credits,
                            textAlignment: detail.ref.mediaType == "manga" ? .leading : nil
                        )
                    } else if let byline = byline(detail) {
                        bylineView(byline, detail: detail, lineLimit: 1)
                    }

                    genreChips(detail, wrapsAfterThird: false)
                    RatingChipRow(chips: ratingChips(detail), stacked: false)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    @ViewBuilder
    private func heroPoster(_ detail: MediaDetail) -> some View {
        let poster = PosterViewerItem(detail: detail)
        let posterSize = PosterSlot.hero.artworkSize(
            mediaType: detail.ref.mediaType,
            orientation: detail.posterOrientation
        )
        let artwork = MediaArtwork(
            url: detail.displayPosterURL,
            title: detail.title,
            slot: .hero,
            mediaType: detail.ref.mediaType,
            orientation: detail.posterOrientation
        )
        .shadow(color: .black.opacity(0.48), radius: 22, y: 12)

        if presentedPosterID == detail.ref.id {
            Color.clear
                .frame(width: posterSize.width, height: posterSize.height)
        } else if let poster {
            artwork
                .matchedGeometryEffect(
                    id: detail.ref.id,
                    in: posterTransitionNamespace,
                    isSource: true
                )
                .onTapGesture {
                    onOpenPoster(poster)
                }
                .onLongPressGesture {
                    openPosterPicker(for: detail)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("View poster for \(detail.displayTitle)")
                .accessibilityHint("Opens the poster full screen")
                .accessibilityAddTraits(.isButton)
                .accessibilityAction {
                    onOpenPoster(poster)
                }
                .accessibilityIdentifier("media-detail.poster")
        } else {
            artwork
                .onLongPressGesture {
                    openPosterPicker(for: detail)
                }
        }
    }

    private func bylineView(_ byline: String, detail: MediaDetail, lineLimit: Int) -> some View {
        let alignment: Alignment = isShowingTitleLogo(detail) ? .center : .leading
        let textAlignment: TextAlignment = isShowingTitleLogo(detail) ? .center : .leading

        return Group {
            if let personRef = bylinePersonRef(detail) {
                Button {
                    presentedPerson = personRef
                } label: {
                    bylineText(byline, lineLimit: lineLimit, alignment: textAlignment)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(byline)")
            } else {
                bylineText(byline, lineLimit: lineLimit, alignment: textAlignment)
            }
        }
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    private func bylineText(_ byline: String, lineLimit: Int, alignment: TextAlignment) -> some View {
        Text(byline)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white.opacity(0.62))
            .lineLimit(lineLimit)
            .multilineTextAlignment(alignment)
    }

    private func creditBylineView(
        _ credits: MediaCreditPresentation,
        textAlignment: TextAlignment? = nil
    ) -> some View {
        let textAlignment = textAlignment ?? (showsTitleLogo ? .center : .leading)
        let alignment: Alignment = textAlignment == .center ? .center : .leading

        return VStack(alignment: textAlignment == .center ? .center : .leading, spacing: 2) {
            ForEach(credits.heroPeople, id: \.self) { person in
                personCreditText(person, alignment: textAlignment)
            }
            if credits.heroMoreCount > 0 {
                Text("+\(credits.heroMoreCount) more")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.44))
            }
        }
        .multilineTextAlignment(textAlignment)
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    @ViewBuilder
    private func personCreditText(_ person: MediaPersonCredit, alignment: TextAlignment) -> some View {
        if let personRef = person.personRef {
            Button {
                presentedPerson = personRef
            } label: {
                bylineText(person.name, lineLimit: 1, alignment: alignment)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("View \(person.name)")
        } else {
            bylineText(person.name, lineLimit: 1, alignment: alignment)
        }
    }

    private func companyCreditBylineView(
        _ credits: [MediaCompanyCredit],
        textAlignment: TextAlignment? = nil
    ) -> some View {
        let textAlignment = textAlignment ?? (showsTitleLogo ? .center : .leading)
        let alignment: Alignment = textAlignment == .center ? .center : .leading
        let visibleCredits = Array(credits.prefix(2))
        let moreCount = max(0, credits.count - visibleCredits.count)

        return VStack(alignment: textAlignment == .center ? .center : .leading, spacing: 2) {
            ForEach(visibleCredits) { credit in
                Button {
                    presentedCompany = credit.ref
                } label: {
                    bylineText(credit.name, lineLimit: 1, alignment: textAlignment)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("View \(credit.name)")
            }
            if moreCount > 0 {
                Text("+\(moreCount) more")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.44))
            }
        }
        .multilineTextAlignment(textAlignment)
        .frame(maxWidth: .infinity, alignment: alignment)
    }

    private func backdropURLString(for detail: MediaDetail) -> String? {
        return detail.displayBackdropURL
    }

    private func heroPosterTopOffset(for detail: MediaDetail) -> CGFloat {
        MediaDetailLayout.heroPosterTopOffset + (backdropURLString(for: detail) == nil ? 0 : MediaDetailLayout.backdropTopSpacing)
    }

    @ViewBuilder
    private func content(_ detail: MediaDetail) -> some View {
        if detail.ref.mediaType == "music" {
            musicContent(detail)
        } else {
            VStack(alignment: .leading, spacing: 28) {
                SynopsisText(text: synopsisPreview(detail))
                trackingSummarySection(detail)
                if detail.ref.mediaType == "game", let game = gameState(detail) {
                    GamePlayHistorySection(game: game,
                        onEdit: { editingGamePlaythrough = $0 },
                        onDeleteUndated: { Task { if !(await viewModel.performGameAction("delete_undated_completion", for: detail)) { isQuickActionAlertPresented = true } } })
                }
                if detail.ref.mediaType == "book", let book = bookState(detail) {
                    BookReadingHistorySection(
                        book: book,
                        isSaving: viewModel.isSavingQuickAction,
                        onEdit: { editingBookJourney = $0 },
                        onDelete: { journey in
                            Task { _ = await viewModel.deleteBookJourney(journey, for: detail) }
                        },
                        onDeleteUndated: {
                            Task { _ = await viewModel.performBookAction("delete_undated_read", for: detail) }
                        },
                        onOpenDiary: { entryId in
                            presentedDiaryEntry = PresentedDiaryEntry(id: entryId)
                        }
                    )
                }
                if detail.ref.mediaType != "episode"
                    || (detail.community?.ratingCount ?? 0) > 0
                    || viewModel.reviewCount > 0 {
                    SpineRatingDistributionSection(
                        community: detail.community,
                        mediaType: detail.ref.mediaType,
                        reviewCount: viewModel.reviewCount
                    ) {
                        isReviewsPresented = true
                    }
                }
                if let rating = AniListRatingSummary(detail: detail) {
                    AniListRatingCard(summary: rating) {
                        isAniListReviewsPresented = true
                    }
                }

                if detail.ref.mediaType == "tv" {
                    seasonsSection(detail)
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                } else if detail.ref.mediaType == "anime",
                          let series = relatedSections(detail).first(where: { $0.id == "series" }) {
                    RecommendationsSection(
                        sections: [series],
                        onSelectSection: { _ in
                            presentedSeries = seriesRef(detail)
                        }
                    ) { item in
                        presentedMediaSelection = MediaBrowsingSelection(
                            ref: item.ref,
                            within: series.items.map(\.ref)
                        )
                    }
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                } else {
                    CreditSection(title: creditTitle(detail), cast: castCredits(detail), crew: crewCredits(detail)) { person in
                        presentedPerson = person
                    }
                    if detail.ref.mediaType != "episode" {
                        seasonsSection(detail)
                    }
                }

                MediaFactsSection(
                    rows: detailRows(detail),
                    onPersonSelected: { person in
                        presentedPerson = person
                    },
                    onCompanySelected: { company in
                        presentedCompany = company
                    }
                )
                if detail.ref.mediaType == "season" {
                    EpisodesSection(
                        episodes: detail.episodes ?? [],
                        completion: detail.completion
                    ) { episode in
                        presentEpisode(episode, from: detail)
                    }
                }
                RecommendationsSection(
                    sections: relatedSections(detail).filter {
                        detail.ref.mediaType != "anime" || $0.id != "series"
                    },
                    onSelectSection: { section in
                        if section.id == "series" || section.id == "collection" {
                            presentedSeries = seriesRef(detail)
                        }
                    }
                ) { item in
                    presentedRef = item.ref
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)
        }
    }

    private func musicContent(_ detail: MediaDetail) -> some View {
        VStack(alignment: .leading, spacing: 28) {
            if let annotation = detail.music?.annotation?.nilIfEmpty {
                SynopsisText(text: annotation)
            }
            trackingSummarySection(detail)
            SpineRatingDistributionSection(
                community: detail.community,
                mediaType: detail.ref.mediaType,
                reviewCount: viewModel.reviewCount
            ) {
                isReviewsPresented = true
            }
            MusicAlbumTracklistSection(
                release: detail.music?.representativeRelease,
                albumCredits: detail.music?.artistCredit ?? [],
                enrichmentState: viewModel.musicEnrichmentState,
                onSelectTrack: { track in
                    presentedSong = MusicSongSelection(
                        album: detail.ref,
                        track: track,
                        artworkURL: detail.displayPosterURL
                    )
                }
            )
            MediaFactsSection(
                rows: detailRows(detail),
                onPersonSelected: { person in
                    presentedPerson = person
                },
                onCompanySelected: { company in
                    presentedCompany = company
                }
            )
            RecommendationsSection(sections: relatedSections(detail)) { item in
                presentedRef = item.ref
            }
            Text(musicProviderAttribution(detail))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.46))
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
    }

    private func presentEpisode(_ episode: EpisodeSummary, from season: MediaDetail) {
        guard let seasonNumber = season.ref.seasonNumber else { return }
        let refs = (season.episodes ?? []).map {
            MediaRef(
                itemId: nil,
                source: season.ref.source,
                mediaType: "episode",
                mediaId: season.ref.mediaId,
                seasonNumber: seasonNumber,
                episodeNumber: $0.episodeNumber
            )
        }
        let selected = MediaRef(
            itemId: nil,
            source: season.ref.source,
            mediaType: "episode",
            mediaId: season.ref.mediaId,
            seasonNumber: seasonNumber,
            episodeNumber: episode.episodeNumber
        )
        presentedMediaSelection = MediaBrowsingSelection(ref: selected, within: refs)
    }

    @ViewBuilder
    private func trackingSummarySection(_ detail: MediaDetail) -> some View {
        TrackingSummarySection(
            detail: detail,
            tracking: viewModel.tracking,
            userState: detail.userState,
            onOpenDiaryEntry: {
                Task {
                    await openTrackingDiary(for: detail)
                }
            },
            onUpdateProgress: {
                openProgressUpdate(for: detail)
            }
        )
    }

    private func openTrackingDiary(for detail: MediaDetail) async {
        if isMultipleLogMedia(detail), detail.ref.itemId != nil {
            presentedMediaDiary = PresentedMediaDiary(detail: detail)
            return
        }

        if let entryId = detail.userState?.diaryEntryId {
            presentedDiaryEntry = PresentedDiaryEntry(id: entryId)
            return
        }

        do {
            if let entry = try await diaryRepository.list().first(where: { matches($0.media.ref, detail.ref) }) {
                presentedDiaryEntry = PresentedDiaryEntry(id: entry.id)
            }
        } catch {
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    private func matches(_ lhs: MediaRef, _ rhs: MediaRef) -> Bool {
        lhs.source == rhs.source
            && lhs.mediaType == rhs.mediaType
            && lhs.mediaId == rhs.mediaId
            && lhs.seasonNumber == rhs.seasonNumber
            && lhs.episodeNumber == rhs.episodeNumber
    }

    private func isMultipleLogMedia(_ detail: MediaDetail) -> Bool {
        (detail.userState?.diaryCount ?? 0) > 1
    }

    private func seasonsSection(_ detail: MediaDetail) -> some View {
        SeasonsSection(
            seasons: detail.seasons ?? []
        ) { season in
            presentedRef = MediaRef(
                itemId: nil,
                source: detail.ref.source,
                mediaType: "season",
                mediaId: detail.ref.mediaId,
                seasonNumber: season.seasonNumber,
                episodeNumber: nil
            )
        }
    }

    private func genreChips(_ detail: MediaDetail, wrapsAfterThird: Bool) -> some View {
        let chips = primaryChips(detail)
        let wrapsAfterThird = wrapsAfterThird || detail.ref.mediaType == "manga"

        if !wrapsAfterThird {
            return AnyView(
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(chips.prefix(6))) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.88),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }
            )
        }

        return AnyView(
            VStack(alignment: .leading, spacing: 8) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(chips.prefix(3))) { chip in
                            genreChip(chip)
                        }
                    }
                }
                .mask(alignment: .trailing) {
                    LinearGradient(
                        stops: [
                            .init(color: .black, location: 0),
                            .init(color: .black, location: 0.88),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                }

                if chips.count > 3 {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(Array(chips.dropFirst(3))) { chip in
                                genreChip(chip)
                            }
                        }
                    }
                    .mask(alignment: .trailing) {
                        LinearGradient(
                            stops: [
                                .init(color: .black, location: 0),
                                .init(color: .black, location: 0.88),
                                .init(color: .clear, location: 1),
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    }
                }
            }
        )
    }

    private func genreChip(_ chip: MediaDetailChip) -> some View {
        Group {
            if let request = chip.discoverRequest {
                Button {
                    presentedDiscover = request
                } label: {
                    chipLabel(chip.label)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Browse \(chip.label)")
            } else {
                chipLabel(chip.label)
            }
        }
    }

    private func chipLabel(_ label: String) -> some View {
        Text(label)
            .font(.system(size: 11, weight: .bold))
            .foregroundStyle(.white.opacity(0.82))
            .lineLimit(1)
            .padding(.horizontal, 11)
            .frame(height: MediaDetailLayout.genrePillHeight)
            .background(.white.opacity(0.12), in: Capsule())
    }

    private func primaryChips(_ detail: MediaDetail) -> [MediaDetailChip] {
        var chips = [MediaDetailChip(label: mediaTypeChipLabel(detail.ref.mediaType), discoverRequest: nil)]
        if detail.ref.mediaType == "anime", let season = detailString(detail, "season")?.nilIfEmpty {
            chips.append(MediaDetailChip(label: season, discoverRequest: nil))
        } else if let year = year(detail) {
            chips.append(MediaDetailChip(label: year, discoverRequest: discoverRequest(detail, filter: .year(String(year.prefix(4))))))
        }
        if let seasonLabel = seasonChipLabel(detail) {
            chips.append(MediaDetailChip(label: seasonLabel, discoverRequest: nil))
        }
        if ["movie", "tv"].contains(detail.ref.mediaType), let runtime = detailString(detail, "runtime"), !runtime.isEmpty {
            chips.append(MediaDetailChip(label: runtime, discoverRequest: nil))
        }
        if let contentRating = contentRating(detail) {
            chips.append(MediaDetailChip(label: contentRating, discoverRequest: nil))
        }
        if ["tv", "anime"].contains(detail.ref.mediaType), let episodes = detailString(detail, "episodes") {
            chips.append(MediaDetailChip(label: "\(episodes) episodes", discoverRequest: nil))
        }
        if detail.ref.mediaType == "book", let pages = detailString(detail, "number_of_pages") ?? detailString(detail, "pages") {
            chips.append(MediaDetailChip(label: "\(pages) pages", discoverRequest: nil))
        }
        if detail.ref.mediaType == "manga", let chapters = detailString(detail, "number_of_chapters") {
            chips.append(MediaDetailChip(label: "\(chapters) chapters", discoverRequest: nil))
        }
        if detail.ref.mediaType == "anime", let format = detailString(detail, "format") {
            chips.append(MediaDetailChip(label: format, discoverRequest: nil))
        }
        if detail.ref.mediaType == "game", let platform = detailArray(detail, "platforms").first {
            chips.append(MediaDetailChip(label: platform, discoverRequest: discoverRequest(detail, filter: .platform(platform))))
        }
        for genre in detailArray(detail, "genres") {
            chips.append(MediaDetailChip(label: genre, discoverRequest: discoverRequest(detail, filter: .genre(genre))))
        }

        var seen = Set<String>()
        let uniqueChips = chips.filter { chip in
            seen.insert(chip.label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()).inserted
        }
        return uniqueChips
    }

    private func discoverRequest(_ detail: MediaDetail, filter: MediaDiscoverRequest.Filter) -> MediaDiscoverRequest? {
        MediaDiscoverRequest.detailPillRequest(ref: detail.ref, filter: filter)
    }

    private func contentRating(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "game" {
            return detailString(detail, "age_rating") ?? detailArray(detail, "age_ratings").first
        }
        guard ["movie", "tv"].contains(detail.ref.mediaType) else { return nil }
        guard let rating = detailString(detail, "rating"), !rating.isEmpty else { return nil }
        return rating
    }

    private func mediaTypeChipLabel(_ mediaType: String) -> String {
        switch mediaType {
        case "tv":
            "TV"
        case "season":
            "TV"
        default:
            mediaType.capitalized
        }
    }

    private func seasonChipLabel(_ detail: MediaDetail) -> String? {
        guard let seasonNumber = detail.ref.seasonNumber, detail.ref.mediaType == "season" else { return nil }
        return "Season \(seasonNumber)"
    }

    private func currentStatus(_ detail: MediaDetail) -> String? {
        viewModel.tracking?.status ?? detail.userState?.status
    }

    private func currentProgress(_ detail: MediaDetail) -> ProgressState? {
        viewModel.tracking?.progress ?? detail.userState?.progress
    }

    private func ratingChips(_ detail: MediaDetail) -> [RatingChip] {
        var chips: [RatingChip] = []
        if let rating = detail.community?.averageRating, !rating.isEmpty {
            chips.append(RatingChip(
                source: "SP",
                value: "\(rating.starRatingValue(mediaType: detail.ref.mediaType))/5",
                assetName: nil,
                providerName: "Spine",
                voteCount: detail.community?.ratingCount,
                voteCountLabel: "ratings"
            ))
        }
        for rating in sortedExternalRatings(detail.externalRatings ?? []) where !rating.value.isEmpty {
            if !MediaExternalRatingPresentation.includes(
                source: rating.source,
                mediaType: detail.ref.mediaType
            ) {
                continue
            }
            chips.append(RatingChip(
                source: rating.source.ratingAbbreviation,
                value: rating.displayValue,
                assetName: rating.ratingAssetName,
                providerName: rating.source,
                destination: rating.destinationURL,
                voteCount: rating.voteCount,
                voteCountLabel: rating.source.ratingCountLabel
            ))
        }
        if MediaExternalRatingPresentation.showsExternalRatingPlaceholder(
            mediaType: detail.ref.mediaType,
            ratings: detail.externalRatings ?? [],
            preparation: detail.externalRatingsPreparation
        ) {
            chips.append(RatingChip(
                source: "",
                value: "",
                assetName: nil,
                providerName: "External ratings",
                isLoading: true
            ))
        }
        return chips
    }

    private func sortedExternalRatings(_ ratings: [ExternalRating]) -> [ExternalRating] {
        ratings.enumerated().sorted { lhs, rhs in
            let lhsOrder = MediaExternalRatingPresentation.order(for: lhs.element.source)
            let rhsOrder = MediaExternalRatingPresentation.order(for: rhs.element.source)
            return lhsOrder == rhsOrder ? lhs.offset < rhs.offset : lhsOrder < rhsOrder
        }.map {
            $0.element
        }
    }

    private func byline(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "anime", let studio = detailArray(detail, "studios").first {
            return studio
        }
        if let author = authors(detail).first {
            return author
        }
        for key in ["director", "creator", "developer", "publisher"] {
            if let value = detailString(detail, key) {
                return value
            }
        }
        if let person = detailArray(detail, "people").first {
            return person
        }
        return detail.subtitle
    }

    private func bylinePersonRef(_ detail: MediaDetail) -> PersonRef? {
        if detail.ref.mediaType == "book" {
            return bookAuthorCredits(detail).first?.personRef
        }
        if detail.ref.mediaType == "manga" {
            return detail.crew?.first?.personRef
        }
        guard detail.ref.source == "tmdb" else { return nil }
        let key = detail.ref.mediaType == "movie" ? "director_id" : detail.ref.mediaType == "tv" ? "creator_id" : nil
        guard let key,
              detailString(detail, key.replacingOccurrences(of: "_id", with: "")) != nil,
              let id = detailString(detail, key),
              !id.isEmpty
        else { return nil }
        return PersonRef(source: "tmdb", id: id)
    }

    private func synopsisPreview(_ detail: MediaDetail) -> String {
        detail.displaySynopsis ?? "No synopsis available yet."
    }

    private func detailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        let mediaType = detail.ref.mediaType
        if mediaType == "music" {
            return musicDetailRows(detail)
        }
        var rows: [DetailFactRow] = [
            DetailFactRow(label: "Status", value: detailString(detail, "status")),
            DetailFactRow(label: "Format", value: detailString(detail, "format")),
        ]

        switch mediaType {
        case "movie":
            rows += [
                DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                creditDetailRow(MediaCreditPresentation.make(for: detail)),
                DetailFactRow(label: "Box Office", value: moneyString(detail, "revenue")),
            ]
        case "tv", "season":
            rows += [
                DetailFactRow(label: "First Aired", value: formattedDate(detailString(detail, "first_air_date") ?? detail.releaseDate)),
                DetailFactRow(label: "Last Aired", value: formattedDate(detailString(detail, "last_air_date"))),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Certification", value: detailString(detail, "rating")),
                DetailFactRow(label: "Seasons", value: detailString(detail, "seasons")),
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                creditDetailRow(MediaCreditPresentation.make(for: detail)),
            ]
        case "episode":
            rows += [
                DetailFactRow(label: "Aired", value: formattedDate(detailString(detail, "air_date") ?? detail.releaseDate)),
                DetailFactRow(label: "Runtime", value: detailString(detail, "runtime")),
                DetailFactRow(label: "Season", value: detailString(detail, "season_title") ?? detail.ref.seasonNumber.map { "Season \($0)" }),
                DetailFactRow(label: "Episode", value: detail.ref.episodeNumber.map(String.init)),
                DetailFactRow(label: "Production Code", value: detailString(detail, "production_code")),
            ]
        case "anime":
            rows += [
                DetailFactRow(label: "Episodes", value: detailString(detail, "episodes")),
                DetailFactRow(label: "Aired", value: detailString(detail, "season")),
                DetailFactRow(label: "Broadcast", value: detailString(detail, "broadcast")),
                DetailFactRow(label: "Source", value: detailString(detail, "source")),
                companyDetailRow(label: "Studios", detail: detail, role: .studio),
            ]
        case "manga":
            rows += [
                DetailFactRow(label: "Chapters", value: detailString(detail, "number_of_chapters")),
                DetailFactRow(label: "Latest Chapter", value: detailString(detail, "latest_chapter_translated")),
                DetailFactRow(label: "Year", value: detailString(detail, "year")),
            ]
        case "game":
            rows += [
                DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)),
                DetailFactRow(label: "Age Rating", value: detailString(detail, "age_rating") ?? detailArray(detail, "age_ratings").joinedOrNil),
                DetailFactRow(label: "Collection", value: detailString(detail, "collection")),
                DetailFactRow(label: "Franchise", value: detailString(detail, "franchise") ?? detailArray(detail, "franchises").joinedOrNil),
                companyDetailRow(label: "Developer", detail: detail, role: .developed),
                companyDetailRow(label: "Publisher", detail: detail, role: .published),
                DetailFactRow(label: "Themes", value: detailArray(detail, "themes").joinedOrNil),
                DetailFactRow(label: "Time to Beat", value: timeToBeatString(detail)),
            ]
        case "comic":
            rows += [
                DetailFactRow(label: "Publisher", value: detailString(detail, "publisher")),
                DetailFactRow(label: "Issues", value: detailString(detail, "issues_count")),
                DetailFactRow(label: "Last Issue", value: lastIssueString(detail)),
            ]
        case "book":
            rows += [
                DetailFactRow(label: "Pages", value: detailString(detail, "number_of_pages") ?? detailString(detail, "pages")),
                DetailFactRow(label: "Publish Date", value: formattedDate(detailString(detail, "publish_date") ?? detailString(detail, "published_date") ?? detailString(detail, "release_date"))),
                DetailFactRow(label: "Physical Format", value: detailString(detail, "physical_format")),
                DetailFactRow(label: "Series", value: bookSeries(detail)),
                DetailFactRow(label: "Maturity Rating", value: detailString(detail, "maturity_rating")),
                DetailFactRow(label: "Google Books Price", value: googleBooksPrice(detail)),
            ]
        default:
            rows.append(DetailFactRow(label: "Release Date", value: formattedReleaseDate(detail)))
        }

        for (label, key) in [
            ("Authors", "authors"),
            ("Genres", "genres"),
            ("Studios", "studios"),
            ("Country", "country"),
            ("Languages", "languages"),
            ("Platforms", "platforms"),
            ("Companies", "companies"),
            ("Publishers", "publishers"),
            ("ISBN", "isbn"),
        ] {
            if mediaType == "book", key == "authors" {
                continue
            }
            if mediaType == "anime", key == "studios" {
                continue
            }
            let values = detailArray(detail, key)
            if !values.isEmpty {
                rows.append(DetailFactRow(label: label, value: values.joined(separator: ", ")))
            }
        }
        return rows.filter { !$0.isEmpty }
    }

    private func bookSeries(_ detail: MediaDetail) -> String? {
        guard let name = detailString(detail, "series_name")?.nilIfEmpty else { return nil }
        guard let position = detailString(detail, "series_position")?.nilIfEmpty else { return name }
        return "\(name) · Book \(position)"
    }

    private func seriesRef(_ detail: MediaDetail) -> SeriesRef? {
        guard ["book", "movie", "game", "anime"].contains(detail.ref.mediaType),
              let id = detailString(detail, "series_id")?.nilIfEmpty
        else { return nil }
        return SeriesRef(
            source: detailString(detail, "series_source")?.nilIfEmpty ?? detail.ref.source,
            id: id,
            mediaType: detailString(detail, "series_media_type")?.nilIfEmpty ?? detail.ref.mediaType
        )
    }

    private func googleBooksPrice(_ detail: MediaDetail) -> String? {
        guard
            let amount = detailString(detail, "google_books_price_amount")?.nilIfEmpty,
            let currency = detailString(detail, "google_books_price_currency")?.nilIfEmpty,
            let country = detailString(detail, "google_books_price_country")?.nilIfEmpty
        else {
            return nil
        }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currency
        let formatted = Decimal(string: amount).flatMap {
            formatter.string(from: $0 as NSDecimalNumber)
        }
        return "\(formatted ?? amount) \(currency) · \(country)"
    }

    private func musicDetailRows(_ detail: MediaDetail) -> [DetailFactRow] {
        guard let music = detail.music else { return [] }
        let release = music.representativeRelease
        return [
            DetailFactRow(label: "Artist", value: musicArtist(detail)),
            DetailFactRow(label: "Release Date", value: formattedDate(music.firstReleaseDate)),
            DetailFactRow(label: "Release Type", value: MusicAlbumPresentation.releaseType(music)),
            DetailFactRow(label: "Track Count", value: release.map { String($0.trackCount) }),
        ].filter { !$0.isEmpty }
    }

    private func creditDetailRow(_ credits: MediaCreditPresentation?) -> DetailFactRow {
        DetailFactRow(label: credits?.label ?? "", people: credits?.people ?? [])
    }

    @MainActor
    private func heroCompanyCredits(_ detail: MediaDetail) -> [MediaCompanyCredit]? {
        let role: CompanyCatalogRole
        switch detail.ref.mediaType {
        case "anime":
            role = .studio
        case "game":
            role = .developed
        default:
            return nil
        }
        let credits = companyCredits(detail, role: role)
        guard !credits.isEmpty else { return nil }
        return detail.ref.mediaType == "anime"
            ? Array(credits.prefix(1))
            : credits
    }

    @MainActor
    private func companyDetailRow(label: String, detail: MediaDetail, role: CompanyCatalogRole) -> DetailFactRow {
        let credits = companyCredits(detail, role: role)
        if !credits.isEmpty {
            return DetailFactRow(label: label, companies: credits)
        }
        let legacyValue: String?
        switch role {
        case .developed:
            legacyValue = detailString(detail, "developer")
        case .published:
            legacyValue = detailString(detail, "publisher")
        case .studio:
            legacyValue = detailArray(detail, "studios").joinedOrNil
        }
        return DetailFactRow(label: label, value: legacyValue)
    }

    @MainActor
    private func companyCredits(_ detail: MediaDetail, role: CompanyCatalogRole? = nil) -> [MediaCompanyCredit] {
        let credits = (detail.details?["company_credits"]?.arrayValue ?? []).compactMap(MediaCompanyCredit.init(json:))
        guard let role else { return credits }
        return credits.filter { $0.hasRole(role) }
    }

    private func creditTitle(_ detail: MediaDetail) -> String {
        switch detail.ref.mediaType {
        case "book":
            "Authors"
        case "comic":
            "People"
        case "anime":
            "Cast & Characters"
        case "manga":
            "Characters & Creators"
        default:
            "Cast & Crew"
        }
    }

    private func castCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "book" {
            return bookAuthorCredits(detail)
        }
        if detail.ref.mediaType == "manga" {
            return (detail.characters ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: nil
                )
            }
        }
        let supportsPeoplePages = detail.ref.source == "tmdb" && ["movie", "tv", "episode"].contains(detail.ref.mediaType)
        let credits = (detail.cast ?? []).map {
            CreditDisplay(
                name: $0.name,
                subtitle: $0.character,
                imageUrl: $0.imageUrl,
                personRef: $0.personRef
                    ?? (supportsPeoplePages && !$0.id.isEmpty
                        ? PersonRef(source: "tmdb", id: $0.id)
                        : nil)
            )
        }
        if credits.isEmpty, detail.ref.mediaType == "comic" {
            return detailArray(detail, "people").map { CreditDisplay(name: $0, subtitle: nil, imageUrl: nil, personRef: nil) }
        }
        return credits
    }

    private func crewCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        if detail.ref.mediaType == "anime" {
            return (detail.characters ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: nil
                )
            }
        }
        if detail.ref.mediaType == "manga" {
            return (detail.crew ?? []).map {
                CreditDisplay(
                    name: $0.name,
                    subtitle: $0.role,
                    imageUrl: $0.imageUrl,
                    personRef: $0.personRef
                )
            }
        }
        let supportsPeoplePages = detail.ref.source == "tmdb" && ["movie", "tv", "episode"].contains(detail.ref.mediaType)
        return (detail.crew ?? []).map {
            CreditDisplay(
                name: $0.name,
                subtitle: $0.role,
                imageUrl: $0.imageUrl,
                personRef: supportsPeoplePages && !$0.id.isEmpty ? PersonRef(source: "tmdb", id: $0.id) : nil
            )
        }
    }

    private func year(_ detail: MediaDetail) -> String? {
        if detail.ref.mediaType == "manga" {
            return MangaYearPresentation.value(
                startDate: detailString(detail, "start_date") ?? detail.releaseDate,
                endDate: detailString(detail, "end_date"),
                status: detailString(detail, "status") ?? detailString(detail, "status_in_country_of_origin")
            )
        }
        if detail.ref.mediaType == "tv" {
            let start = detailString(detail, "first_air_date") ?? detail.releaseDate
            let end = detailString(detail, "last_air_date")
            guard let startYear = start?.yearPrefix else { return nil }
            let status = detailString(detail, "status")?.lowercased()
            if let status, ["ended", "canceled", "cancelled"].contains(status), let endYear = end?.yearPrefix, endYear != startYear {
                return "\(startYear)-\(endYear)"
            }
            return startYear
        }
        let value = detail.releaseDate ?? detailString(detail, "release_date") ?? detailString(detail, "first_air_date") ?? detailString(detail, "start_date") ?? detailString(detail, "publish_date")
        guard let value, value.count >= 4 else { return nil }
        return String(value.prefix(4))
    }

    private func authors(_ detail: MediaDetail) -> [String] {
        detailArray(detail, "authors")
    }

    private func bookAuthorCredits(_ detail: MediaDetail) -> [CreditDisplay] {
        guard detail.ref.mediaType == "book" else { return [] }
        let rawAuthors = detail.details?["authors"]?.arrayValue ?? []
        let credits = rawAuthors.compactMap { value -> CreditDisplay? in
            if let object = value.objectValue {
                guard let name = object["name"]?.displayString, !name.isEmpty else { return nil }
                let source = object["source"]?.displayString ?? bookAuthorSourceFallback(detail)
                let personId = object["person_id"]?.displayString ?? object["id"]?.displayString
                let ref = bookAuthorRef(source: source, personId: personId ?? name)
                return CreditDisplay(name: name, subtitle: "Author", imageUrl: bookAuthorImageURL(object), personRef: ref)
            }
            guard let name = value.displayString, !name.isEmpty else { return nil }
            return CreditDisplay(
                name: name,
                subtitle: "Author",
                imageUrl: nil,
                personRef: bookAuthorRef(source: bookAuthorSourceFallback(detail), personId: name)
            )
        }
        return credits.isEmpty ? authors(detail).map {
            CreditDisplay(
                name: $0,
                subtitle: "Author",
                imageUrl: nil,
                personRef: bookAuthorRef(source: bookAuthorSourceFallback(detail), personId: $0)
            )
        } : credits
    }

    private func bookAuthorSourceFallback(_ detail: MediaDetail) -> String? {
        ["hardcover", "openlibrary"].contains(detail.ref.source) ? detail.ref.source : nil
    }

    private func bookAuthorRef(source: String?, personId: String?) -> PersonRef? {
        guard let source, let personId, !source.isEmpty, !personId.isEmpty else { return nil }
        return PersonRef(source: source, id: personId)
    }

    private func bookAuthorImageURL(_ object: [String: JSONValue]) -> String? {
        object["image_url"]?.displayString
            ?? object["image"]?.displayString
            ?? object["profile_url"]?.displayString
            ?? object["cached_image"]?.displayString
    }

    private func releaseDate(_ detail: MediaDetail) -> String? {
        detail.releaseDate
            ?? detailString(detail, "release_date")
            ?? detailString(detail, "first_air_date")
            ?? detailString(detail, "start_date")
            ?? detailString(detail, "publish_date")
            ?? detailString(detail, "published_date")
    }

    private func formattedReleaseDate(_ detail: MediaDetail) -> String? {
        formattedDate(releaseDate(detail))
    }

    private func formattedDate(_ raw: String?) -> String? {
        guard let raw else { return nil }
        return Self.longDateFormatter.string(from: raw) ?? raw
    }

    private func moneyString(_ detail: MediaDetail, _ key: String) -> String? {
        guard let value = detail.details?[key]?.numberValue, value > 0 else { return detailString(detail, key) }
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "USD"
        formatter.maximumFractionDigits = 0
        formatter.usesGroupingSeparator = true
        return formatter.string(from: NSNumber(value: value))
    }

    private func timeToBeatString(_ detail: MediaDetail) -> String? {
        guard let object = detail.details?["time_to_beat"]?.objectValue else { return detailString(detail, "time_to_beat") }
        let keys = [
            ("normally", "Main"),
            ("hastily", "Rush"),
            ("completely", "Complete"),
        ]
        let parts = keys.compactMap { key, label -> String? in
            guard let value = object[key]?.numberValue, value > 0 else { return nil }
            return "\(label) \(Int(value / 3600))h"
        }
        return parts.joinedOrNil
    }

    private func lastIssueString(_ detail: MediaDetail) -> String? {
        let name = detailString(detail, "last_issue_name")
        let number = detailString(detail, "last_issue_number")
        return [name, number.map { "#\($0)" }].compactMap { $0 }.joined(separator: " ").nilIfEmpty
    }

    private static let longDateFormatter: LongDateFormatter = LongDateFormatter()

    private func detailString(_ detail: MediaDetail, _ key: String) -> String? {
        detail.details?[key]?.displayString
    }

    private func detailArray(_ detail: MediaDetail, _ key: String) -> [String] {
        detail.details?[key]?.displayStrings ?? []
    }

    private func relatedSections(_ detail: MediaDetail) -> [RelatedMediaSection] {
        let sections: [RelatedMediaSection]
        if let relatedSections = detail.relatedSections, !relatedSections.isEmpty {
            sections = relatedSections
        } else if let related = detail.related {
            sections = related.compactMap { key, value in
                guard key != "seasons", let values = value.arrayValue else { return nil }
                let items = values.compactMap { rawRelatedSummary($0, parent: detail) }
                guard !items.isEmpty else { return nil }
                let isCollection = detail.ref.mediaType == "movie" && key != "recommendations"
                let id = isCollection ? "collection" : key
                let title = isCollection ? "Collection" : key.replacingOccurrences(of: "_", with: " ").capitalized
                return RelatedMediaSection(id: id, title: title, items: items)
            }
        } else {
            return []
        }
        let normalized: [RelatedMediaSection] = sections.compactMap { section -> RelatedMediaSection? in
            if section.id == "all_related" {
                return nil
            }
            return section
        }
        return normalized.sorted { lhs, rhs in
            if lhs.id == "collection", rhs.id != "collection" { return true }
            if rhs.id == "collection", lhs.id != "collection" { return false }
            return false
        }
    }

    private func rawRelatedSummary(_ value: JSONValue, parent: MediaDetail) -> MediaSummary? {
        guard let object = value.objectValue else { return nil }
        let source = object["source"]?.displayString ?? parent.ref.source
        let mediaType = object["media_type"]?.displayString ?? parent.ref.mediaType
        guard let mediaId = object["media_id"]?.displayString ?? object["id"]?.displayString,
              let title = object["title"]?.displayString ?? object["name"]?.displayString else { return nil }
        return MediaSummary(
            ref: MediaRef(
                itemId: nil,
                source: source,
                mediaType: mediaType,
                mediaId: mediaId,
                seasonNumber: object["season_number"]?.intValue,
                episodeNumber: object["episode_number"]?.intValue
            ),
            title: title,
            preferredTitle: object["display_title"]?.displayString,
            relation: object["relation"]?.displayString,
            subtitle: object["year"]?.displayString,
            overview: object["overview"]?.displayString,
            imageUrl: object["image_url"]?.displayString ?? object["image"]?.displayString,
            posterUrl: object["poster_url"]?.displayString,
            customPosterUrl: object["custom_poster_url"]?.displayString,
            posterOrientation: PosterOrientation(rawValue: object["poster_orientation"]?.displayString ?? "") ?? .unknown,
            posterAccentColor: object["poster_accent_color"]?.displayString,
            releaseDate: object["release_date"]?.displayString ?? object["first_air_date"]?.displayString,
            defaultSource: source,
            userState: nil
        )
    }
}

private enum MediaDetailLayout {
    static let heroPosterWidth: CGFloat = 191
    static let episodeHeroHeight: CGFloat = 520
    static let episodeAccessibilityArtworkHeight: CGFloat = 280
    static let heroPosterTopOffset: CGFloat = 108
    static let backdropTopSpacing: CGFloat = 137.5
    static let backdropHeight: CGFloat = 352.34375
    static let backdropCropAspectRatio: CGFloat = 1.5
    static let genrePillHeight: CGFloat = 31
    static let ratingBadgeSize: CGFloat = 24
    static let ratingPillVerticalPadding: CGFloat = 6
    static var ratingPillHeight: CGFloat { ratingBadgeSize + ratingPillVerticalPadding * 2 }
    static let recommendationPosterSize = CGSize(width: 100, height: 150)
    static let recommendationCardHeight: CGFloat = 190
    static let seasonPosterSize = CGSize(width: 90, height: 135)
}

enum SpinePalette {
    static let pageBackground = Color(red: 0.07, green: 0.07, blue: 0.065)
}

struct SpinePageBackground: View {
    var body: some View {
        SpinePalette.pageBackground
            .ignoresSafeArea()
    }
}

private struct CircleIconButton: View {
    let systemName: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 17, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 44, height: 44)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

func bookGameCopy(for mediaType: String) -> (currently: String, finished: String, stopped: String) {
    switch mediaType {
    case "book":
        return ("Currently Reading", "Finished Reading", "Stopped Reading")
    case "music":
        return ("Start Listening", "Mark Listened", "Stopped")
    default:
        return ("Currently Playing", "Finished Playing", "Stopped Playing")
    }
}

private struct PosterMenuSheet: View {
    let posterLabel: String
    let showsSeasonOption: Bool
    let showsTVShowOption: Bool
    let showsPosterOption: Bool
    let showsBackdropOption: Bool
    let showsLogoOption: Bool
    let onViewSeason: () -> Void
    let onViewTVShow: () -> Void
    let onAddToList: () -> Void
    let onCustomizePoster: () -> Void
    let onCustomizeBackdrop: () -> Void
    let onCustomizeLogo: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            if showsSeasonOption {
                Button(action: onViewSeason) {
                    Label("View Season", systemImage: "rectangle.stack")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsTVShowOption {
                Button(action: onViewTVShow) {
                    Label("View TV Show", systemImage: "tv")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            Button(action: onAddToList) {
                Label("Add to List", systemImage: "list.bullet.rectangle")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .frame(height: 54)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 16)

            if showsPosterOption {
                Button(action: onCustomizePoster) {
                    Label(posterLabel, systemImage: "photo.on.rectangle.angled")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsBackdropOption {
                Button(action: onCustomizeBackdrop) {
                    Label("Customize Backdrop", systemImage: "photo.on.rectangle")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }

            if showsLogoOption {
                Button(action: onCustomizeLogo) {
                    Label("Customize Logo", systemImage: "textformat")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 18)
                        .frame(height: 54)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
            }
        }
        .presentationBackground(.regularMaterial)
    }
}

private struct BookGameActionSheet: View {
    @State private var isRemoveConfirmationPresented = false
    @State private var isDNFConfirmationPresented = false

    let mediaType: String
    let status: String?
    let bookState: BookTrackingState?
    let isSaving: Bool
    let errorMessage: String?
    let onAction: (MediaDetailQuickAction) async -> Void
    let onRemove: () async -> Void
    let onUpdateProgress: () -> Void
    let onLog: () -> Void

    private var isInProgress: Bool {
        status == "In progress"
    }

    @ViewBuilder
    var body: some View {
        if mediaType == "book" {
            bookActions
        } else {
            let copy = bookGameCopy(for: mediaType)
            VStack(spacing: 16) {
                HStack(spacing: 12) {
                    if mediaType == "music" && isInProgress {
                        actionButton(title: "Pause", systemName: "pause.fill", action: .paused)
                    } else if isInProgress {
                        progressButton
                    } else {
                        actionButton(
                            title: mediaType == "music" && (status == "Paused" || status == "Dropped") ? "Resume Listening" : copy.currently,
                            systemName: "play.fill",
                            action: .currently
                        )
                    }
                    actionButton(title: copy.finished, systemName: "checkmark", action: .finished)
                    actionButton(title: copy.stopped, systemName: "xmark", action: .stopped)
                    logButton
                }
                .padding(.horizontal, 16)
                .padding(.top, 36)

                if mediaType == "music" {
                    HStack(spacing: 12) {
                        Button {
                            Task { await onAction(.planning) }
                        } label: {
                            Label(status == "Planning" ? "Planning" : "Add to Planning", systemImage: "bookmark")
                        }
                        .disabled(isSaving || status == "Planning")

                        if status != nil {
                            Button(role: .destructive) {
                                isRemoveConfirmationPresented = true
                            } label: {
                                Label("Remove Tracking", systemImage: "trash")
                            }
                            .disabled(isSaving)
                        }
                    }
                    .font(.system(size: 13, weight: .bold))
                    .buttonStyle(.bordered)
                    .tint(.white.opacity(0.82))
                    .padding(.horizontal, 16)
                }

                errorView
            }
            .presentationBackground(.regularMaterial)
            .confirmationDialog("Remove album tracking?", isPresented: $isRemoveConfirmationPresented, titleVisibility: .visible) {
                Button("Remove Tracking", role: .destructive) {
                    Task { await onRemove() }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Diary logs are kept.")
            }
        }
    }

    private var bookActions: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("Track this book")
                    .font(.title3.weight(.bold))

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                    bookActionButton(
                        status == "Planning" ? "To Read" : "Add to To Read",
                        systemImage: "bookmark",
                        disabled: status == "Planning" || bookState?.hasLiveJourney == true
                    ) { await onAction(.planning) }
                    bookActionButton(
                        status == "Paused" ? "Resume Reading" : "Currently Reading",
                        systemImage: "book.pages",
                        disabled: status == "In progress"
                    ) { await onAction(.currently) }

                    if bookState?.hasLiveJourney == true {
                        bookActionButton("Update Progress", systemImage: "slider.horizontal.3") {
                            onUpdateProgress()
                        }
                        bookActionButton("Pause", systemImage: "pause.fill", disabled: status == "Paused") {
                            await onAction(.paused)
                        }
                        bookActionButton("Mark as DNF", systemImage: "xmark") {
                            isDNFConfirmationPresented = true
                        }
                    } else {
                        bookActionButton("Paused", systemImage: "pause.fill", disabled: status == "Paused") {
                            await onAction(.paused)
                        }
                        bookActionButton("Did Not Finish", systemImage: "xmark", disabled: status == "Dropped") {
                            await onAction(.stopped)
                        }
                    }

                    bookActionButton("Log Read", systemImage: "square.and.pencil") {
                        onLog()
                    }
                }

                if bookState?.hasLiveJourney == true {
                    Text(bookState?.actionReasons["planning"] ?? "Pause or finish the active journey before moving this book to To Read.")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                }

                if status != nil {
                    Button(role: .destructive) {
                        if bookState?.canRemoveTracking != false {
                            isRemoveConfirmationPresented = true
                        }
                    } label: {
                        Label("Remove Tracking", systemImage: "trash")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isSaving || bookState?.canRemoveTracking == false)

                    if bookState?.canRemoveTracking == false, let reason = bookState?.removeTrackingReason {
                        Text(reason)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                }

                errorView
            }
            .padding(18)
        }
        .presentationBackground(.regularMaterial)
        .confirmationDialog("Mark this book as Did Not Finish?", isPresented: $isDNFConfirmationPresented, titleVisibility: .visible) {
            Button("Mark as DNF", role: .destructive) {
                Task { await onAction(.stopped) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Your current progress will remain in reading history.")
        }
        .confirmationDialog("Remove book tracking?", isPresented: $isRemoveConfirmationPresented, titleVisibility: .visible) {
            Button("Remove Tracking", role: .destructive) {
                Task { await onRemove() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Diary logs are kept. Reading journeys must be deleted first.")
        }
    }

    private func bookActionButton(
        _ title: String,
        systemImage: String,
        disabled: Bool = false,
        action: @escaping () async -> Void
    ) -> some View {
        Button {
            Task { await action() }
        } label: {
            Label(title, systemImage: systemImage)
                .font(.subheadline.weight(.bold))
                .frame(maxWidth: .infinity, minHeight: 46)
        }
        .buttonStyle(.bordered)
        .tint(.white.opacity(0.84))
        .disabled(isSaving || disabled)
    }

    @ViewBuilder
    private var errorView: some View {
        if let errorMessage {
            Text(errorMessage)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.red.opacity(0.92))
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func actionButton(title: String, systemName: String, action: MediaDetailQuickAction) -> some View {
        Button {
            Task {
                await onAction(action)
            }
        } label: {
            actionLabel(title: title, systemName: systemName)
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private var progressButton: some View {
        Button(action: onUpdateProgress) {
            actionLabel(title: "Update Progress", systemName: "slider.horizontal.3")
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private var logButton: some View {
        Button(action: onLog) {
            actionLabel(title: mediaType == "music" ? "Log Album" : "Log", systemName: "square.and.pencil")
        }
        .buttonStyle(.plain)
        .disabled(isSaving)
    }

    private func actionLabel(title: String, systemName: String) -> some View {
        VStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(.white.opacity(0.055))
                    .overlay {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(.white.opacity(0.13), lineWidth: 1.25)
                    }
                Group {
                    if isSaving {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: systemName)
                            .font(.system(size: 30, weight: .semibold))
                            .foregroundStyle(.white.opacity(0.94))
                    }
                }
                .spineContentTransition(value: isSaving)
            }
            .frame(height: 86)
            .shadow(color: .black.opacity(0.18), radius: 10, y: 6)

            Text(title)
                .font(.system(size: 14, weight: .heavy))
                .foregroundStyle(.white.opacity(0.92))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.82)
                .frame(height: 36, alignment: .top)
        }
        .frame(maxWidth: .infinity)
    }
}

private struct EpisodeDetailLoadingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            RoundedRectangle(cornerRadius: 0)
                .fill(.white.opacity(0.08))
                .frame(height: 330)

            VStack(alignment: .leading, spacing: 10) {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(.white.opacity(0.1))
                    .frame(width: 74, height: 12)
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.12))
                    .frame(maxWidth: .infinity)
                    .frame(height: 34)
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(.white.opacity(0.08))
                    .frame(width: 210, height: 16)
                HStack(spacing: 10) {
                    Capsule().fill(.white.opacity(0.1)).frame(width: 112, height: 38)
                    Spacer()
                    ForEach(0 ..< 3, id: \.self) { _ in
                        Circle().fill(.white.opacity(0.1)).frame(width: 42, height: 42)
                    }
                }
            }
            .padding(.horizontal, 16)
        }
        .redacted(reason: .placeholder)
        .accessibilityLabel("Loading episode details")
        .frame(maxWidth: .infinity, minHeight: 520, alignment: .top)
    }
}

private struct EpisodeHeroArtwork: View {
    let urlString: String?
    let title: String

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                placeholder

                if let urlString, let url = URL(string: urlString) {
                    SpineAsyncImage(url: url) { phase in
                        if case let .success(image) = phase {
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .clipped()
                        }
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.5), location: 0),
                        .init(color: .black.opacity(0.12), location: 0.3),
                        .init(color: .clear, location: 0.48),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0.25),
                        .init(color: .black.opacity(0.2), location: 0.5),
                        .init(color: SpinePalette.pageBackground.opacity(0.72), location: 0.72),
                        .init(color: SpinePalette.pageBackground, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                LinearGradient(
                    colors: [.black.opacity(0.28), .clear, .black.opacity(0.08)],
                    startPoint: .leading,
                    endPoint: .trailing
                )
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .clipped()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Episode still for \(title)")
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: "episode")
        return LinearGradient(
            colors: theme.gradientColors.map { $0.opacity(0.52) } + [SpinePalette.pageBackground],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

private struct IMDbExternalLinkPill: View {
    let destination: URL

    var body: some View {
        Link(destination: destination) {
            HStack(spacing: 6) {
                Image("RatingIMDb")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 24, height: 24)
                    .clipShape(Circle())
                Text("IMDb")
                    .font(.caption.weight(.heavy))
                Image(systemName: "arrow.up.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.white.opacity(0.5))
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 9)
            .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
            .background(.white.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View this episode on IMDb")
    }
}

struct HeroArtwork: View {
    private let artworkURL: URL?
    private let usesPosterFallback: Bool
    private let accentColor: Color

    init(detail: MediaDetail) {
        artworkURL = URL(string: detail.displayBackdropURL ?? detail.displayPosterURL ?? "")
        usesPosterFallback = detail.displayBackdropURL == nil && detail.displayPosterURL != nil
        accentColor = Color(hex: detail.posterAccentColor) ?? SpinePalette.pageBackground
    }

    init(artworkURL: URL?, accentColor: Color = SpinePalette.pageBackground) {
        self.artworkURL = artworkURL
        usesPosterFallback = artworkURL != nil
        self.accentColor = accentColor
    }

    var body: some View {
        ZStack {
            SpinePalette.pageBackground

            GeometryReader { proxy in
                SpineAsyncImage(url: artworkURL) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        accentColor
                            .frame(width: proxy.size.width, height: proxy.size.height)
                    }
                }
                .blur(radius: blurRadius, opaque: true)
                .scaleEffect(scale)
                .brightness(0.02)
                .saturation(usesPosterFallback ? 1.18 : 1.34)
                .frame(width: proxy.size.width, height: proxy.size.height)
            }

            accentColor
                .opacity(0.07)
                .blendMode(.softLight)

            LinearGradient(
                stops: [
                    .init(color: .black.opacity(0.22), location: 0),
                    .init(color: .black.opacity(0.08), location: 0.14),
                    .init(color: .clear, location: 0.38),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            LinearGradient(
                stops: [
                    .init(color: .clear, location: 0.48),
                    .init(color: SpinePalette.pageBackground.opacity(0.12), location: 0.62),
                    .init(color: SpinePalette.pageBackground.opacity(0.45), location: 0.78),
                    .init(color: SpinePalette.pageBackground.opacity(0.82), location: 0.9),
                    .init(color: SpinePalette.pageBackground, location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            RadialGradient(
                colors: [.clear, .black.opacity(0.1)],
                center: .center,
                startRadius: 60,
                endRadius: 360
            )
            .blendMode(.multiply)
        }
        .clipped()
    }

    private var blurRadius: CGFloat {
        usesPosterFallback ? 30 : 22
    }

    private var scale: CGFloat {
        usesPosterFallback ? 1.28 : 1.2
    }

}

struct BackdropArtwork: View {
    let urlString: String

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                SpineAsyncImage(url: URL(string: urlString)) { phase in
                    switch phase {
                    case let .success(image):
                        image
                            .resizable()
                            .scaledToFill()
                            .frame(width: proxy.size.width, height: proxy.size.height)
                            .clipped()
                    default:
                        Color.clear
                    }
                }

                LinearGradient(
                    stops: [
                        .init(color: .black.opacity(0.42), location: 0),
                        .init(color: .black.opacity(0.18), location: 0.36),
                        .init(color: .clear, location: 0.72),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .mask(
                LinearGradient(
                    stops: [
                        .init(color: .white, location: 0),
                        .init(color: .white, location: 0.52),
                        .init(color: .white.opacity(0.35), location: 0.78),
                        .init(color: .clear, location: 1),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        .clipped()
    }
}

private struct ActionRail: View {
    private static let buttonSize: CGFloat = 48

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPressing = false
    @State private var pressHaptics = UIImpactFeedbackGenerator(style: .light)
    @State private var actionHaptics = UISelectionFeedbackGenerator()

    @Binding var ratingPicker: MediaRatingPickerState
    let isTracked: Bool
    let isLiked: Bool
    var showsEye = true
    var showsRating = false
    var offersRatingAfterBaseAction = false
    var trackLabel: String?
    var eyeLabel: String?
    var isEyeSelected = false
    var isEyeLoading = false
    var isLikeLoading = false
    let onTrack: () -> Void
    let onLike: () -> Void
    var onEye: () -> Void = {}
    var onRating: (Int) -> Void = { _ in }

    var body: some View {
        VStack(spacing: -10) {
            if showsEye, ratingPicker.isPresented {
                ratingComposer
                    .transition(.opacity)
            }

            rail
        }
        .frame(width: surfaceWidth)
        .glassEffect(.regular.interactive(), in: glassShape)
        .onAppear {
            pressHaptics.prepare()
            actionHaptics.prepare()
        }
    }

    private var surfaceWidth: CGFloat {
        let buttonCount = 2 + (showsEye ? 1 : 0) + (showsRating ? 1 : 0)
        let railWidth = CGFloat(buttonCount) * Self.buttonSize + 10
        return ratingPicker.isPresented ? max(railWidth, 183) : railWidth
    }

    private var glassShape: RoundedRectangle {
        RoundedRectangle(
            cornerRadius: ratingPicker.isPresented ? 26 : 29,
            style: .continuous
        )
    }

    private var rail: some View {
        HStack(spacing: 0) {
            railButton(
                systemName: "plus",
                label: trackLabel ?? (isTracked ? "Edit tracking" : "Log"),
                usesLargePlus: true,
                action: handleTrack
            )
            if showsEye {
                railButton(
                    systemName: isEyeSelected || ratingPicker.hasLocallyWatched ? "eye.slash.fill" : "eye",
                    label: eyeLabel ?? "Mark as watched",
                    usesLargePlus: false,
                    isLoading: isEyeLoading && !ratingPicker.isPresented,
                    action: handleEye
                )
                .accessibilityValue(ratingPicker.isPresented ? "Expanded" : "Collapsed")
                .accessibilityHint(ratingPicker.isPresented ? "Closes the rating picker" : "Opens the rating picker")
            }
            if showsRating {
                railButton(
                    systemName: ratingPicker.confirmedHalfSteps > 0 ? "star.fill" : "star",
                    label: ratingPicker.confirmedHalfSteps > 0 ? "Edit rating" : "Rate",
                    usesLargePlus: false,
                    action: handleRating
                )
            }
            railButton(
                systemName: isLiked ? "heart.fill" : "heart",
                label: isLiked ? "Unlike" : "Like",
                usesLargePlus: false,
                isLoading: isLikeLoading,
                action: handleLike
            )
        }
        .padding(5)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !isPressing else { return }
                    isPressing = true
                    pressHaptics.impactOccurred()
                    pressHaptics.prepare()
                }
                .onEnded { _ in isPressing = false }
        )
        .accessibilityIdentifier("media-detail.actions")
    }

    private var ratingComposer: some View {
        ZStack {
            StarRatingPill(halfSteps: $ratingPicker.draftHalfSteps)

            if ratingPicker.showsConfirm {
                Button(action: confirmRating) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white.opacity(0.9))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isEyeLoading || isLikeLoading)
                .offset(x: 86)
                .transition(.offset(x: 23.75).combined(with: .opacity))
                .accessibilityLabel("Confirm rating")
                .accessibilityIdentifier("media-detail.rating-confirm")
            }
        }
        .frame(width: 280, height: 60)
        .offset(x: ratingPicker.showsConfirm ? -9.75 : 0)
        .animation(
            reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84),
            value: ratingPicker.showsConfirm
        )
        .accessibilityIdentifier("media-detail.rating-picker")
    }

    private func handleTrack() {
        dismissRatingPickerIfNeeded()
        onTrack()
    }

    private func handleEye() {
        guard offersRatingAfterBaseAction else {
            dismissRatingPickerIfNeeded()
            onEye()
            return
        }
        if isEyeSelected {
            dismissRatingPickerIfNeeded()
            onEye()
        } else if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            let shouldTrack = !isEyeSelected && !ratingPicker.hasLocallyWatched
            animate { ratingPicker.open() }
            if shouldTrack {
                onEye()
            }
        }
    }

    private func handleLike() {
        if !isLiked, offersRatingAfterBaseAction, !ratingPicker.isPresented {
            animate { ratingPicker.open() }
        } else {
            dismissRatingPickerIfNeeded()
        }
        onLike()
    }

    private func handleRating() {
        if ratingPicker.isPresented {
            animate { ratingPicker.dismiss() }
        } else {
            animate { ratingPicker.open(markLocallyWatched: false) }
        }
    }

    private func confirmRating() {
        let halfSteps = ratingPicker.draftHalfSteps
        actionHaptics.selectionChanged()
        actionHaptics.prepare()
        animate { ratingPicker.confirm() }
        onRating(halfSteps)
    }

    private func dismissRatingPickerIfNeeded() {
        guard ratingPicker.isPresented else { return }
        animate { ratingPicker.dismiss() }
    }

    private func animate(_ changes: () -> Void) {
        withAnimation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.84)) {
            changes()
        }
    }

    private func railButton(
        systemName: String,
        label: String,
        usesLargePlus: Bool,
        isLoading: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button {
            actionHaptics.selectionChanged()
            actionHaptics.prepare()
            action()
        } label: {
            ZStack {
                Group {
                    if isLoading {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: systemName)
                            .font(.system(size: usesLargePlus ? 22 : 16, weight: .semibold))
                            .foregroundStyle(railIconColor(systemName: systemName))
                    }
                }
                .spineContentTransition(value: isLoading)
            }
            .frame(width: Self.buttonSize, height: Self.buttonSize)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(isLoading)
        .accessibilityLabel(label)
    }

    private func railIconColor(systemName: String) -> Color {
        if systemName == "heart.fill" { return .pink }
        if systemName == "star.fill" { return .yellow }
        return .white.opacity(0.84)
    }
}

private struct StarRatingPill: View {
    @Binding var halfSteps: Int
    @State private var haptics = UISelectionFeedbackGenerator()

    private let duneGold = Color(red: 0.94, green: 0.64, blue: 0.24)

    var body: some View {
        GeometryReader { proxy in
            HStack(spacing: 1.25) {
                ForEach(1...5, id: \.self) { value in
                    Image(systemName: starSymbol(for: value))
                        .font(.system(size: 17.5, weight: .medium))
                        .foregroundStyle(halfSteps >= value * 2 - 1 ? duneGold : .white.opacity(0.28))
                        .frame(width: 22.5, height: 30)
                }
            }
            .accessibilityHidden(true)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Capsule())
            .simultaneousGesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { gesture in
                        let clamped = min(max(gesture.location.x, 0), proxy.size.width)
                        let nextValue = min(
                            max(Int(ceil((clamped / proxy.size.width) * 10)), 0),
                            10
                        )
                        setRating(nextValue)
                    }
            )
        }
        .frame(width: 127.5, height: 30)
        .padding(.horizontal, 18)
        .padding(.vertical, 15)
        .onAppear { haptics.prepare() }
        .accessibilityRepresentation {
            Slider(value: Binding(get: { Double(halfSteps) }, set: { setRating(Int($0)) }), in: 0...10, step: 1) {
                Text("Your rating")
            }
            .accessibilityValue(Text(verbatim: halfSteps == 0 ? "Not rated" : "\(Double(halfSteps) / 2) out of 5"))
            .accessibilityIdentifier("media-detail.star-rating")
        }
    }

    private func starSymbol(for value: Int) -> String {
        if halfSteps >= value * 2 { return "star.fill" }
        if halfSteps == value * 2 - 1 { return "star.leadinghalf.filled" }
        return "star"
    }

    private func setRating(_ newValue: Int) {
        guard newValue != halfSteps else { return }
        halfSteps = newValue
        haptics.selectionChanged()
        haptics.prepare()
    }
}

private struct SectionLabel: View {
    let title: String

    var body: some View {
        Text(title.uppercased())
            .font(.system(size: 11, weight: .heavy))
            .foregroundStyle(.white.opacity(0.62))
            .tracking(0)
    }
}

struct RatingChip: Hashable {
    static let loadingID = "external-rating-loading"

    let source: String
    let value: String
    let assetName: String?
    let providerName: String
    let destination: URL?
    let voteCount: Int?
    let voteCountLabel: String?
    let isAttributionOnly: Bool
    let isLoading: Bool

    init(
        source: String,
        value: String,
        assetName: String?,
        providerName: String? = nil,
        destination: URL? = nil,
        voteCount: Int? = nil,
        voteCountLabel: String? = nil,
        isAttributionOnly: Bool = false,
        isLoading: Bool = false
    ) {
        self.source = source
        self.value = value
        self.assetName = assetName
        self.providerName = providerName ?? source
        self.destination = destination
        self.voteCount = voteCount
        self.voteCountLabel = voteCountLabel
        self.isAttributionOnly = isAttributionOnly
        self.isLoading = isLoading
    }

    var id: String {
        isLoading ? Self.loadingID : "rating:\(providerName.lowercased())"
    }

    var accessibilityLabel: String {
        var components = [
            isAttributionOnly ? "\(providerName) \(value)" : "\(providerName) rating \(value)"
        ]
        if let voteCount, voteCount > 0 {
            components.append("\(voteCount.formatted()) \(voteCountLabel ?? "votes")")
        }
        return components.joined(separator: ", ")
    }
}

private struct RatingChipRow: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let chips: [RatingChip]
    let stacked: Bool

    private var chipIDs: [String] {
        chips.map(\.id)
    }

    private var chipTransition: AnyTransition {
        reduceMotion ? .opacity : .opacity.combined(with: .scale(scale: 0.96))
    }

    var body: some View {
        if !chips.isEmpty {
            if stacked {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(chips, id: \.id) { chip in
                        ratingChip(chip)
                            .transition(chipTransition)
                    }
                }
                .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: chipIDs)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(chips, id: \.id) { chip in
                            ratingChip(chip)
                                .transition(chipTransition)
                        }
                    }
                    .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: chipIDs)
                }
            }
        }
    }

    @ViewBuilder
    private func ratingChip(_ chip: RatingChip) -> some View {
        if chip.isLoading {
            ExternalRatingLoadingChip()
        } else if let destination = chip.destination {
            Link(destination: destination) {
                ratingChipContent(chip)
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(chip.accessibilityLabel)
            .accessibilityHint("Opens the \(chip.providerName) media page")
        } else {
            ratingChipContent(chip)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(chip.accessibilityLabel)
        }
    }

    private func ratingChipContent(_ chip: RatingChip) -> some View {
        HStack(spacing: 6) {
            RatingSourceBadge(chip: chip)
            VStack(alignment: .leading, spacing: 1) {
                Text(chip.value)
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white)
                if let voteCount = chip.voteCount, voteCount > 0 {
                    Text("\(voteCount.formatted(.number.notation(.compactName))) \(chip.voteCountLabel ?? "votes")")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
        .background(.white.opacity(0.12), in: Capsule())
    }
}

private struct ExternalRatingLoadingChip: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var isPulsing = false

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(.white.opacity(isPulsing ? 0.20 : 0.10))
                .frame(
                    width: MediaDetailLayout.ratingBadgeSize,
                    height: MediaDetailLayout.ratingBadgeSize
                )
            VStack(alignment: .leading, spacing: 4) {
                Capsule()
                    .fill(.white.opacity(isPulsing ? 0.24 : 0.12))
                    .frame(width: 34, height: 8)
                Capsule()
                    .fill(.white.opacity(isPulsing ? 0.16 : 0.08))
                    .frame(width: 48, height: 6)
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, MediaDetailLayout.ratingPillVerticalPadding)
        .background(.white.opacity(isPulsing ? 0.09 : 0.05), in: Capsule())
        .overlay {
            Capsule()
                .stroke(.white.opacity(isPulsing ? 0.2 : 0.09), lineWidth: 1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("External ratings loading")
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
                isPulsing = true
            }
        }
    }
}

private struct RatingSourceBadge: View {
    let chip: RatingChip

    var body: some View {
        if chip.providerName.caseInsensitiveCompare("Google Books") == .orderedSame {
            Text("Google Books")
                .font(.system(size: 10, weight: .heavy))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)
        } else {
            Group {
                if let assetName = chip.assetName {
                    ratingLogo(assetName: assetName)
                } else {
                    Text(chip.source)
                        .font(.system(size: 10, weight: .black))
                        .foregroundStyle(.black)
                }
            }
            .frame(width: MediaDetailLayout.ratingBadgeSize, height: MediaDetailLayout.ratingBadgeSize)
            .background(["RatingMAL", "RatingAniList", "RatingMetacritic", "RatingSteam"].contains(chip.assetName) ? .clear : .white, in: Circle())
            .clipShape(Circle())
        }
    }

    @ViewBuilder
    private func ratingLogo(assetName: String) -> some View {
        switch assetName {
        case "RatingIMDb":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.15)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingLetterboxd":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.1)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingMAL", "RatingAniList":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingMetacritic", "RatingSteam":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingRottenTomatoesCertifiedFresh":
            Image(assetName)
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .scaleEffect(1.045)
                .frame(width: 24, height: 24)
                .clipped()
        case "RatingHardcover":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .offset(y: 3)
                .clipped()
        case "RatingIGDB":
            Image(assetName)
                .resizable()
                .scaledToFill()
                .frame(width: 24, height: 24)
                .scaleEffect(1.55)
                .frame(width: 24, height: 24)
                .clipped()
        default:
            Image(assetName)
                .resizable()
                .scaledToFit()
                .padding(3)
        }
    }
}

private struct BookReadingHistorySection: View {
    @State private var pendingDeleteJourney: BookJourneyState?
    @State private var isUndatedDeletePresented = false

    let book: BookTrackingState
    let isSaving: Bool
    let onEdit: (BookJourneyState) -> Void
    let onDelete: (BookJourneyState) -> Void
    let onDeleteUndated: () -> Void
    let onOpenDiary: (Int) -> Void

    var body: some View {
        if !dnfJourneys.isEmpty || book.undatedRead != nil {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    SectionLabel(title: "Reading History")
                    Spacer()
                    if book.lifetimeReadCount > 0 {
                        Text("\(book.lifetimeReadCount) lifetime read\(book.lifetimeReadCount == 1 ? "" : "s")")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.52))
                    }
                }

                ForEach(dnfJourneys) { journey in
                    historyRow(journey)
                }

                if book.undatedRead != nil {
                    HStack(spacing: 12) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Read")
                                .font(.subheadline.weight(.bold))
                            Text("Date unknown")
                                .font(.caption.weight(.medium))
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(role: .destructive) {
                            isUndatedDeletePresented = true
                        } label: {
                            Image(systemName: "trash")
                        }
                        .disabled(isSaving)
                        .accessibilityLabel("Delete undated read")
                    }
                    .padding(12)
                    .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 14))
                }
            }
            .confirmationDialog("Delete this reading journey?", isPresented: Binding(
                get: { pendingDeleteJourney != nil },
                set: { if !$0 { pendingDeleteJourney = nil } }
            ), titleVisibility: .visible) {
                Button("Delete Journey", role: .destructive) {
                    if let journey = pendingDeleteJourney { onDelete(journey) }
                    pendingDeleteJourney = nil
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("The Did Not Finish journey and its progress will be removed.")
            }
            .confirmationDialog("Delete undated Read?", isPresented: $isUndatedDeletePresented, titleVisibility: .visible) {
                Button("Delete Undated Read", role: .destructive, action: onDeleteUndated)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This removes only the date-unknown Read state.")
            }
        }
    }

    private var dnfJourneys: [BookJourneyState] {
        book.readingHistory.filter { $0.status == "Dropped" }
    }

    private func historyRow(_ journey: BookJourneyState) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: journey.status == "Completed" ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(journey.status == "Completed" ? .green : .orange)

            VStack(alignment: .leading, spacing: 4) {
                Text(journey.status == "Completed" ? "Read" : "Did Not Finish")
                    .font(.subheadline.weight(.bold))
                Text(journeyDateLine(journey))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                if let progressText = journey.progress?.detailDisplayText(preferredMode: nil) {
                    Text(progressText)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.58))
                }
            }

            Spacer()

            Menu {
                Button {
                    onEdit(journey)
                } label: {
                    Label("Edit Dates", systemImage: "calendar")
                }
                if let entryId = journey.completionDiaryEntryId {
                    Button {
                        onOpenDiary(entryId)
                    } label: {
                        Label("Open Log", systemImage: "square.and.pencil")
                    }
                } else {
                    Button(role: .destructive) {
                        pendingDeleteJourney = journey
                    } label: {
                        Label("Delete Journey", systemImage: "trash")
                    }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.title3)
            }
            .disabled(isSaving)
            .accessibilityLabel("Reading history actions")
        }
        .padding(12)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 14))
    }

    private func journeyDateLine(_ journey: BookJourneyState) -> String {
        switch (journey.startDate, journey.endDate) {
        case let (start?, end?): "\(start.shortDateLabel) – \(end.shortDateLabel)"
        case let (start?, nil): "Started \(start.shortDateLabel)"
        case let (nil, end?): "Ended \(end.shortDateLabel)"
        case (nil, nil): "Dates unknown"
        }
    }
}

private struct BookJourneyDateEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var includesStartDate: Bool
    @State private var includesEndDate: Bool
    @State private var startDate: Date
    @State private var endDate: Date

    let journey: BookJourneyState
    let isSaving: Bool
    let errorMessage: String?
    let onSave: (Date?, Date?) async -> Bool

    init(
        journey: BookJourneyState,
        isSaving: Bool,
        errorMessage: String?,
        onSave: @escaping (Date?, Date?) async -> Bool
    ) {
        self.journey = journey
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSave = onSave
        let parsedStart = CalendarDateCodec.date(from: journey.startDate)
        let parsedEnd = CalendarDateCodec.date(from: journey.endDate)
        _includesStartDate = State(initialValue: parsedStart != nil)
        _includesEndDate = State(initialValue: parsedEnd != nil)
        _startDate = State(initialValue: parsedStart ?? parsedEnd ?? Date())
        _endDate = State(initialValue: parsedEnd ?? Date())
    }

    var body: some View {
        NavigationStack {
            Form {
                Toggle("Started on", isOn: $includesStartDate)
                if includesStartDate {
                    DatePicker("Start date", selection: $startDate, in: ...Date(), displayedComponents: .date)
                }
                Toggle("Ended on", isOn: $includesEndDate)
                if includesEndDate {
                    DatePicker("End date", selection: $endDate, in: ...Date(), displayedComponents: .date)
                }
                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.red)
                }
            }
            .navigationTitle("Edit Reading Dates")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            _ = await onSave(
                                includesStartDate ? startDate : nil,
                                includesEndDate ? endDate : nil
                            )
                        }
                    }
                    .disabled(isSaving)
                }
            }
        }
    }
}

private struct TrackingSummarySection: View {
    let detail: MediaDetail
    let tracking: TrackingState?
    let userState: UserMediaState?
    let onOpenDiaryEntry: () -> Void
    let onUpdateProgress: () -> Void

    var body: some View {
        if hasState {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: detail.ref.mediaType == "game" ? "Your Progress" : "Your Tracking")
                HStack(alignment: .top, spacing: 10) {
                    if detail.ref.mediaType != "episode" {
                        MediaArtwork(
                            url: detail.displayPosterURL,
                            title: detail.title,
                            slot: .libraryRow,
                            mediaType: detail.ref.mediaType,
                            orientation: detail.posterOrientation
                        )
                        .onTapGesture { if hasLogs { onOpenDiaryEntry() } }
                        .allowsHitTesting(hasLogs)
                        .accessibilityLabel(hasLogs ? "View diary log for \(detail.title)" : "\(detail.title) cover")
                        .accessibilityAddTraits(hasLogs ? .isButton : [])
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        if let status {
                            Text(detail.ref.trackingStatusLabel(status))
                                .font(.system(size: 14, weight: .heavy))
                                .foregroundStyle(.white)
                                .onTapGesture {
                                    if hasMultipleLogs {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(hasMultipleLogs ? .isButton : [])
                            if showsUpdateProgressButton {
                                Button(action: onUpdateProgress) {
                                    Text("Update Progress")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundStyle(.white.opacity(0.82))
                                        .padding(.horizontal, 11)
                                        .frame(height: 24)
                                        .background(.white.opacity(0.12), in: Capsule())
                                }
                                .buttonStyle(.plain)
                                .padding(.top, 3)
                            }
                        }
                        ForEach(lines, id: \.self) { line in
                            Text(line)
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(.white.opacity(0.76))
                                .onTapGesture {
                                    if line == logLine {
                                        onOpenDiaryEntry()
                                    }
                                }
                                .accessibilityAddTraits(line == logLine ? .isButton : [])
                        }
                    }

                    Spacer()
                }
            }
        }
    }

    private var status: String? {
        tracking?.status
            ?? userState?.status
            ?? (detail.ref.isEpisode && userState?.isTracked == true ? "Watched" : nil)
    }
    private var hasState: Bool { status != nil || !lines.isEmpty }
    private var showsUpdateProgressButton: Bool {
        if detail.ref.mediaType == "game" { return (tracking?.game ?? userState?.game)?.canUpdateProgress == true }
        return (status == "In progress" || (detail.ref.mediaType == "book" && status == "Paused"))
            && ["book", "game"].contains(detail.ref.mediaType)
    }
    private var hasLogs: Bool { (userState?.diaryCount ?? 0) > 0 || userState?.diaryEntryId != nil }
    private var hasMultipleLogs: Bool {
        (userState?.diaryCount ?? 0) > 1
    }
    private var logLine: String? {
        guard hasMultipleLogs, let diaryCount = userState?.diaryCount else { return nil }
        return "\(diaryCount) logs"
    }

    private var lines: [String] {
        var values: [String] = []
        if detail.ref.mediaType == "game", let game = tracking?.game ?? userState?.game,
           let text = game.currentPlaythrough?.progress.summary, !text.isEmpty {
            values.append(text)
        }
        if detail.ref.mediaType != "game", (status == "In progress" || (detail.ref.mediaType == "book" && status == "Paused")),
           detail.ref.mediaType != "movie",
            let progressText = (tracking?.progress ?? userState?.progress)?.detailDisplayText(preferredMode: ProgressDisplayPreferences.mode(for: detail.ref)) {
            values.append(progressText)
        }
        if let logLine {
            values.append(logLine)
        }
        if let rating = tracking?.rating ?? userState?.rating {
            values.append("Rated \(rating.starRatingLabel(mediaType: detail.ref.mediaType))")
        }
        if !hasMultipleLogs, let consumedAt = userState?.diaryConsumedAt {
            values.append("Logged \(consumedAt.shortDateLabel)")
        }
        if detail.ref.mediaType != "movie" {
            if let startDate = tracking?.startDate {
                values.append("Started \(startDate.longDateLabel)")
            }
        }
        return values
    }

}

struct SynopsisText: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let text: String
    @State private var isExpanded = false

    var body: some View {
        Button {
            withAnimation(reduceMotion ? nil : .smooth(duration: 0.3)) {
                isExpanded.toggle()
            }
        } label: {
            synopsisCopy
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isExpanded ? "Collapse synopsis" : "Expand synopsis")
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(reduceMotion ? nil : .smooth(duration: 0.3), value: isExpanded)
    }

    private var synopsisCopy: some View {
        Text(text)
            .font(synopsisFont)
            .foregroundStyle(.white.opacity(0.88))
            .lineSpacing(3)
            .lineLimit(isExpanded ? nil : 3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
    }

    private var synopsisFont: Font {
        .system(size: 15, weight: .medium)
    }
}

private struct DetailFactRow: Identifiable {
    let label: String
    var value: String?
    var people: [MediaPersonCredit]
    var companies: [MediaCompanyCredit]
    var destination: URL?

    var id: String { label }
    var isEmpty: Bool { label.isEmpty || (value?.isEmpty != false && people.isEmpty && companies.isEmpty) }

    init(label: String, value: String?, destination: URL? = nil) {
        self.label = label
        self.value = value
        people = []
        companies = []
        self.destination = destination
    }

    init(label: String, people: [MediaPersonCredit]) {
        self.label = label
        value = nil
        self.people = people
        companies = []
        destination = nil
    }

    init(label: String, companies: [MediaCompanyCredit]) {
        self.label = label
        value = nil
        people = []
        self.companies = companies
        destination = nil
    }
}

private struct MediaFactsSection: View {
    let rows: [DetailFactRow]
    let onPersonSelected: (PersonRef) -> Void
    let onCompanySelected: (CompanyRef) -> Void

    var body: some View {
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 13) {
                SectionLabel(title: "Details")

                VStack(spacing: 0) {
                    ForEach(rows) { row in
                        DetailFactRowView(
                            row: row,
                            onPersonSelected: onPersonSelected,
                            onCompanySelected: onCompanySelected
                        )

                        if row.id != rows.last?.id {
                            Divider().overlay(.white.opacity(0.045))
                        }
                    }
                }
                .mediaDetailSurface(cornerRadius: 14)
            }
        }
    }
}

private struct DetailFactRowView: View {
    let row: DetailFactRow
    let onPersonSelected: (PersonRef) -> Void
    let onCompanySelected: (CompanyRef) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Text(row.label)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.white.opacity(0.44))
                .lineLimit(1)
                .frame(width: 98, alignment: .leading)

            if row.people.isEmpty && row.companies.isEmpty {
                if let destination = row.destination {
                    Link(destination: destination) {
                        HStack(spacing: 5) {
                            detailText(row.value ?? "")
                            Image(systemName: "arrow.up.right")
                                .font(.caption2.weight(.bold))
                        }
                        .foregroundStyle(.white.opacity(0.84))
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens in the system browser")
                } else {
                    detailText(row.value ?? "")
                        .lineLimit(2)
                        .minimumScaleFactor(0.86)
                }
            } else if !row.people.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(row.people, id: \.self) { person in
                        if let personRef = person.personRef {
                            Button {
                                onPersonSelected(personRef)
                            } label: {
                                personName(person.name)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("View \(person.name)")
                        } else {
                            personName(person.name)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(row.companies) { company in
                        Button {
                            onCompanySelected(company.ref)
                        } label: {
                            detailText(company.name)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(company.name)")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private func personName(_ name: String) -> some View {
        detailText(name)
    }

    private func detailText(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.white.opacity(0.84))
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MusicAlbumTracklistSection: View {
    let release: MusicRepresentativeRelease?
    let albumCredits: [MusicArtistCredit]
    let enrichmentState: MusicEnrichmentState
    let onSelectTrack: (MusicTrack) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(title: "Tracklist")

            if enrichmentState == .loading {
                LazyVStack(spacing: 0) {
                    ForEach(1...4, id: \.self) { position in
                        HStack(spacing: 12) {
                            Text("\(position)")
                                .frame(width: 28, alignment: .leading)
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Loading track title")
                                Text("Loading artist")
                                    .font(.caption)
                            }
                            Spacer()
                        }
                        .padding(.vertical, 10)
                    }
                }
                .redacted(reason: .placeholder)
                .foregroundStyle(.white.opacity(0.7))
                .accessibilityHidden(true)
                .accessibilityIdentifier("music.tracklist.loading")
            } else if let release, hasTracks(release) {
                LazyVStack(spacing: 0) {
                    ForEach(release.media, id: \.position) { medium in
                        if release.discCount > 1 || release.media.count > 1 {
                            discHeader(medium)
                        }
                        ForEach(medium.tracks) { track in
                            MusicAlbumTrackRow(
                                track: track,
                                albumCredits: albumCredits,
                                onSelect: { onSelectTrack(track) }
                            )
                            if track.id != medium.tracks.last?.id {
                                Divider()
                                    .overlay(.white.opacity(0.12))
                                    .padding(.leading, 40)
                            }
                        }
                    }
                }
            } else {
                ContentUnavailableView(
                    "Tracklist unavailable",
                    systemImage: "music.note.list",
                    description: Text("Spine could not resolve a complete album edition.")
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .mediaDetailSurface(cornerRadius: 14)
                .accessibilityIdentifier("music.tracklist.unavailable")
            }
        }
    }

    private func hasTracks(_ release: MusicRepresentativeRelease) -> Bool {
        release.media.contains { !$0.tracks.isEmpty }
    }

    private func discHeader(_ medium: MusicMedium) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Disc \(medium.position)")
                .font(.caption.weight(.semibold))
            if let title = medium.title?.nilIfEmpty {
                Text(title)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .foregroundStyle(.white.opacity(0.62))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 14)
        .padding(.bottom, 6)
    }
}

private struct MusicAlbumTrackRow: View {
    @ScaledMetric(relativeTo: .caption) private var numberWidth = 28

    let track: MusicTrack
    let albumCredits: [MusicArtistCredit]
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(track.number)
                    .font(.system(size: 16, weight: .regular).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.5))
                    .frame(width: numberWidth, alignment: .leading)

                VStack(alignment: .leading, spacing: 3) {
                    Text(track.title)
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(.white.opacity(0.94))
                        .fixedSize(horizontal: false, vertical: true)
                    if let artist = MusicAlbumPresentation.differingArtistCredit(
                        track: track,
                        albumCredits: albumCredits
                    ) {
                        Text(artist)
                            .font(.system(size: 14, weight: .regular))
                            .foregroundStyle(.white.opacity(0.52))
                    }
                }

                Spacer(minLength: 10)

                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.42))
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, 6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(MusicAlbumPresentation.trackAccessibilityLabel(track: track, albumCredits: albumCredits))
        .accessibilityHint("Opens song details")
        .accessibilityIdentifier("music.track.\(track.recording.recordingMbid)")
    }
}

private struct MusicStreamingButtons: View {
    let links: [MusicStreamingLink]

    private var destinations: [MusicStreamingDestination] {
        var seen = Set<String>()
        return MusicAlbumPresentation.streamingDestinations(links).filter {
            ($0.label == "Apple Music" || $0.label == "Spotify")
                && seen.insert($0.label).inserted
        }
    }

    var body: some View {
        if !destinations.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    ForEach(destinations) { destination in
                        MusicStreamingButton(destination: destination)
                    }
                }
                .fixedSize(horizontal: true, vertical: false)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(destinations) { destination in
                            MusicStreamingButton(destination: destination)
                        }
                    }
                }
            }
        }
    }
}

private struct MusicStreamingButton: View {
    let destination: MusicStreamingDestination

    var body: some View {
        Link(destination: destination.url) {
            HStack(spacing: 8) {
                serviceLogo

                Image(systemName: "play.fill")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(accentColor)
            }
            .padding(.horizontal, 12)
            .frame(height: 40)
            .background(.white.opacity(0.07), in: Capsule())
            .glassEffect(
                .regular.tint(accentColor.opacity(0.08)).interactive(),
                in: .rect(cornerRadius: 20)
            )
            .overlay {
                Capsule().stroke(.white.opacity(0.14), lineWidth: 0.75)
            }
            .shadow(color: accentColor.opacity(0.15), radius: 7, y: 2)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Listen on \(destination.label)")
        .accessibilityHint("Opens using the system URL behavior")
    }

    private var accentColor: Color {
        destination.label == "Spotify" ? Color(red: 0.12, green: 0.84, blue: 0.38) : .pink
    }

    @ViewBuilder
    private var serviceLogo: some View {
        switch destination.label {
        case "Apple Music":
            Image("AppleMusicIcon")
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        case "Spotify":
            Image("SpotifyIcon")
                .resizable()
                .scaledToFit()
                .frame(width: 24, height: 24)
                .clipShape(Circle())
        default:
            Image(systemName: "music.note")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.9))
                .frame(width: 24, height: 24)
        }
    }
}

private extension View {
    func mediaDetailSurface(cornerRadius: CGFloat) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)

        return self
            .background(Color.white.opacity(0.028), in: shape)
            .overlay { shape.stroke(.white.opacity(0.045), lineWidth: 1) }
    }
}

struct AniListRatingSummary: Equatable {
    struct Bucket: Equatable, Identifiable {
        let score: Int
        let count: Int

        var id: Int { score }
    }

    let averageScore: Int
    let ratingCount: Int
    let buckets: [Bucket]
    let hasReviews: Bool
    let url: URL?

    func bucket(at x: CGFloat, width: CGFloat) -> Bucket? {
        guard x.isFinite, width.isFinite, width > 0, !buckets.isEmpty else { return nil }
        let index = min(max(Int(x / width * CGFloat(buckets.count)), 0), buckets.count - 1)
        return buckets[index]
    }

    init?(detail: MediaDetail) {
        guard
            detail.ref.mediaType == "anime",
            let object = detail.details?["anilist_rating"]?.objectValue,
            let averageScore = object["average_score"]?.intValue,
            (0...100).contains(averageScore),
            let declaredRatingCount = object["rating_count"]?.intValue,
            declaredRatingCount >= 0,
            let values = object["score_distribution"]?.arrayValue
        else { return nil }

        var counts: [Int: Int] = [:]
        for value in values {
            guard
                let bucket = value.objectValue,
                let score = bucket["score"]?.intValue,
                stride(from: 10, through: 100, by: 10).contains(score),
                let count = bucket["count"]?.intValue,
                count >= 0
            else { continue }
            counts[score, default: 0] += count
        }

        let buckets = stride(from: 10, through: 100, by: 10).map {
            Bucket(score: $0, count: counts[$0, default: 0])
        }
        self.averageScore = averageScore
        ratingCount = buckets.reduce(0) { $0 + $1.count }
        self.buckets = buckets
        if case let .bool(value)? = object["has_reviews"] {
            hasReviews = value
        } else {
            hasReviews = false
        }
        url = aniListDestination(object["url"]?.displayString)
    }
}

@MainActor
@Observable
final class MediaReviewsViewModel {
    var reviews: [MediaReview] = []
    var reviewCount = 0
    var nextPage: String?
    var isLoadingInitial = false
    var isLoadingMore = false
    var initialError: String?
    var paginationError: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        ref: MediaRef,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        self.ref = ref
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func loadInitial() async {
        guard reviews.isEmpty, !isLoadingInitial else { return }
        isLoadingInitial = true
        initialError = nil
        defer { isLoadingInitial = false }

        do {
            apply(try await mediaRepository.reviewPage(ref: ref, page: nil))
        } catch is CancellationError {
            return
        } catch {
            initialError = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func retryInitial() async {
        reviews = []
        reviewCount = 0
        nextPage = nil
        await loadInitial()
    }

    func loadNextPage() async {
        guard let page = nextPage, !isLoadingInitial, !isLoadingMore else { return }
        isLoadingMore = true
        paginationError = nil
        defer { isLoadingMore = false }

        do {
            apply(try await mediaRepository.reviewPage(ref: ref, page: page))
        } catch is CancellationError {
            return
        } catch {
            paginationError = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }

    func shouldLoadNext(after reviewID: Int) -> Bool {
        guard
            nextPage != nil,
            let index = reviews.firstIndex(where: { $0.id == reviewID })
        else { return false }
        return index >= max(reviews.count - 5, 0)
    }

    private func apply(_ page: PagedResponse<MediaReview>) {
        var seen = Set(reviews.map(\.id))
        reviews.append(contentsOf: page.results.filter { seen.insert($0.id).inserted })
        reviewCount = page.count
        nextPage = APIPageCursor.nextPage(from: page.next)
        initialError = nil
        paginationError = nil
    }
}

@MainActor
@Observable
final class AniListReviewsViewModel {
    var reviews: [AniListReview] = []
    var nextPage: Int?
    var isLoadingInitial = false
    var isLoadingMore = false
    var initialError: String?
    var paginationError: String?

    private let ref: MediaRef
    private let mediaRepository: MediaRepository
    private var loadedPages: Set<Int> = []

    init(ref: MediaRef, mediaRepository: MediaRepository) {
        self.ref = ref
        self.mediaRepository = mediaRepository
    }

    func loadInitial() async {
        guard reviews.isEmpty, !isLoadingInitial else { return }
        isLoadingInitial = true
        initialError = nil
        defer { isLoadingInitial = false }
        do {
            apply(try await mediaRepository.anilistReviews(ref: ref, page: 1))
        } catch is CancellationError {
            return
        } catch {
            initialError = error.localizedDescription
        }
    }

    func retryInitial() async {
        reviews = []
        nextPage = nil
        loadedPages = []
        await loadInitial()
    }

    func loadNextPage() async {
        guard
            let page = nextPage,
            !loadedPages.contains(page),
            !isLoadingInitial,
            !isLoadingMore
        else { return }
        isLoadingMore = true
        paginationError = nil
        defer { isLoadingMore = false }
        do {
            apply(try await mediaRepository.anilistReviews(ref: ref, page: page))
        } catch is CancellationError {
            return
        } catch {
            paginationError = error.localizedDescription
        }
    }

    func shouldLoadNext(after reviewID: String) -> Bool {
        guard
            nextPage != nil,
            let index = reviews.firstIndex(where: { $0.id == reviewID })
        else { return false }
        return index >= max(reviews.count - 5, 0)
    }

    private func apply(_ page: AniListReviewPage) {
        loadedPages.insert(page.currentPage)
        var seen = Set(reviews.map(\.id))
        reviews.append(
            contentsOf: page.results.filter { seen.insert($0.id).inserted }
        )
        nextPage = page.nextPage
        initialError = nil
        paginationError = nil
    }
}

private struct AniListRatingCard: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var selectedBucketScore: Int?
    @State private var haptics = UISelectionFeedbackGenerator()

    let summary: AniListRatingSummary
    let onSeeReviews: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                HStack(spacing: 8) {
                    Image("RatingAniList")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 38, height: 38)
                        .accessibilityLabel("AniList")
                    metric(title: "AVERAGE SCORE", value: "\(summary.averageScore)%")
                }
                Spacer()
                metric(
                    title: selectedBucket.map { "\($0.score) SCORE VOTES" } ?? "NUMBER OF RATINGS",
                    value: (selectedBucket?.count ?? summary.ratingCount).formatted()
                )
            }

            GeometryReader { proxy in
                HStack(alignment: .bottom, spacing: 4) {
                    ForEach(summary.buckets) { bucket in
                        VStack(spacing: 4) {
                            RoundedRectangle(cornerRadius: 2, style: .continuous)
                                .fill(
                                    .white.opacity(
                                        selectedBucketScore == nil || selectedBucketScore == bucket.score
                                            ? 0.86
                                            : 0.28
                                    )
                                )
                                .frame(
                                    height: max(
                                        3,
                                        CGFloat(bucket.count) / CGFloat(maxCount) * 42
                                    )
                                )
                            Text("\(bucket.score)")
                                .font(.system(size: 8, weight: .heavy))
                                .foregroundStyle(.white.opacity(0.52))
                        }
                        .frame(maxWidth: .infinity)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(accessibilityLabel(for: bucket))
                    }
                }
                .contentShape(Rectangle())
                .gesture(scrubGesture(width: proxy.size.width))
            }
            .frame(height: 58, alignment: .bottom)
            .onAppear { haptics.prepare() }

            HStack(alignment: .center) {
                if summary.hasReviews {
                    Button(action: onSeeReviews) {
                        HStack(spacing: 5) {
                            Text("SEE ALL REVIEWS")
                            Image(systemName: "arrow.right")
                        }
                        .font(.system(size: 11, weight: .heavy))
                        .foregroundStyle(.white)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens AniList written reviews")
                } else {
                    Text("No written reviews.")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white.opacity(0.52))
                }

                Spacer()

                Text("AniList")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.56))
            }
        }
        .padding(14)
        .frame(
            minHeight: dynamicTypeSize.isAccessibilitySize ? 216 : 176,
            alignment: .top
        )
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
    }

    private func metric(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 9, weight: .heavy))
                .foregroundStyle(.white.opacity(0.5))
            Text(value)
                .font(.system(size: 22, weight: .heavy))
                .foregroundStyle(.white)
        }
    }

    private var maxCount: Int {
        max(summary.buckets.map(\.count).max() ?? 1, 1)
    }

    private var selectedBucket: AniListRatingSummary.Bucket? {
        summary.buckets.first { $0.score == selectedBucketScore }
    }

    private func scrubGesture(width: CGFloat) -> some Gesture {
        LongPressGesture(minimumDuration: 0.2)
            .sequenced(before: DragGesture(minimumDistance: 0))
            .onChanged { value in
                guard case let .second(true, drag) = value, let drag else { return }
                selectBucket(at: drag.location.x, width: width)
            }
            .onEnded { _ in
                selectedBucketScore = nil
            }
    }

    private func selectBucket(at x: CGFloat, width: CGFloat) {
        guard
            let bucket = summary.bucket(at: x, width: width),
            bucket.score != selectedBucketScore
        else { return }
        selectedBucketScore = bucket.score
        haptics.selectionChanged()
        haptics.prepare()
    }

    private func accessibilityLabel(for bucket: AniListRatingSummary.Bucket) -> String {
        let percentage = summary.ratingCount > 0
            ? Double(bucket.count) / Double(summary.ratingCount) * 100
            : 0
        return "\(bucket.score) score, \(bucket.count.formatted()) ratings, \(percentage.formatted(.number.precision(.fractionLength(0)))) percent"
    }
}

private struct MediaReviewsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: MediaReviewsViewModel

    private let detail: MediaDetail

    init(
        detail: MediaDetail,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.detail = detail
        _viewModel = State(
            initialValue: MediaReviewsViewModel(
                ref: detail.ref,
                mediaRepository: mediaRepository,
                onUnauthorized: onUnauthorized
            )
        )
    }

    var body: some View {
        ZStack {
            SpinePageBackground()
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    reviewsContent
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .task {
            await viewModel.loadInitial()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            reviewPoster
                .frame(width: 40, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(headerLabel)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                Text(detail.displayTitle)
                    .font(.system(size: 19, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.06), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close reviews")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    @ViewBuilder
    private var reviewPoster: some View {
        SpineAsyncImage(url: detail.displayPosterURL.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                let theme = MediaTypeTheme.theme(for: detail.ref.mediaType)
                LinearGradient(
                    colors: theme.gradientColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .overlay {
                    Image(systemName: theme.symbolName)
                        .foregroundStyle(.white.opacity(0.5))
                }
            }
        }
        .accessibilityHidden(true)
    }

    @ViewBuilder
    private var reviewsContent: some View {
        if viewModel.isLoadingInitial, viewModel.reviews.isEmpty {
            ProgressView("Loading reviews…")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.7))
                .frame(maxWidth: .infinity, minHeight: 220)
        } else if let error = viewModel.initialError, viewModel.reviews.isEmpty {
            errorState(message: error) {
                Task { await viewModel.retryInitial() }
            }
            .frame(minHeight: 220)
        } else if viewModel.reviews.isEmpty {
            Text("No written reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
                .frame(maxWidth: .infinity, minHeight: 220)
        } else {
            ForEach(viewModel.reviews) { review in
                MediaReviewRow(review: review, mediaType: detail.ref.mediaType)
                    .padding(.horizontal, 16)
                    .task {
                        guard viewModel.shouldLoadNext(after: review.id) else { return }
                        await viewModel.loadNextPage()
                    }

                if review.id != viewModel.reviews.last?.id {
                    Divider()
                        .overlay(.white.opacity(0.08))
                        .padding(.horizontal, 16)
                }
            }
            paginationState
        }
    }

    @ViewBuilder
    private var paginationState: some View {
        if viewModel.isLoadingMore {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
                .padding(24)
        } else if let error = viewModel.paginationError {
            errorState(message: error) {
                Task { await viewModel.loadNextPage() }
            }
        }
    }

    private func errorState(message: String, retry: @escaping () -> Void) -> some View {
        VStack(spacing: 10) {
            Text(message)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.red.opacity(0.82))
                .multilineTextAlignment(.center)
            Button("Retry", action: retry)
                .font(.system(size: 13, weight: .heavy))
                .buttonStyle(.bordered)
                .tint(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
    }

    private var headerLabel: String {
        guard viewModel.reviewCount > 0 else { return "Reviews for…" }
        let noun = viewModel.reviewCount == 1 ? "review" : "reviews"
        return "\(viewModel.reviewCount.formatted()) \(noun) for…"
    }
}

private struct MediaReviewRow: View {
    @State private var revealsSpoiler = false

    let review: MediaReview
    let mediaType: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                reviewerAvatar

                VStack(alignment: .leading, spacing: 2) {
                    Text(review.user.displayName)
                        .font(.system(size: 15, weight: .heavy))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    HStack(spacing: 7) {
                        Text("@\(review.user.username)")
                        if let date = review.createdAt?.shortDateLabel {
                            Text(date)
                        }
                    }
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.46))
                }

                Spacer()

                if let rating = review.rating {
                    Label(
                        rating.starRatingLabel(mediaType: mediaType),
                        systemImage: "star.fill"
                    )
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.86))
                }
            }

            if review.containsSpoilers, !revealsSpoiler {
                Button {
                    revealsSpoiler = true
                } label: {
                    Label("This review contains spoilers. Tap to reveal.", systemImage: "eye.slash.fill")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.yellow.opacity(0.84))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(.yellow.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                .accessibilityHint("Reveals the review title and text")
            } else {
                if let title = review.reviewTitle?.nilIfEmpty {
                    Text(title)
                        .font(.system(size: 16, weight: .heavy))
                        .foregroundStyle(.white)
                }

                Text(review.review)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.72))
                    .fixedSize(horizontal: false, vertical: true)
            }

            if review.likeCount > 0 {
                Text("\(review.likeCount.formatted()) \(review.likeCount == 1 ? "like" : "likes")")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.white.opacity(0.46))
            }
        }
        .padding(.vertical, 18)
    }

    private var reviewerAvatar: some View {
        SpineAsyncImage(url: review.user.avatarUrl.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                Circle()
                    .fill(.white.opacity(0.08))
                    .overlay {
                        Image(systemName: "person.fill")
                            .foregroundStyle(.white.opacity(0.42))
                    }
            }
        }
        .frame(width: 42, height: 42)
        .clipShape(Circle())
        .accessibilityHidden(true)
    }
}

private struct AniListReviewsView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: AniListReviewsViewModel

    private let detail: MediaDetail

    init(detail: MediaDetail, mediaRepository: MediaRepository) {
        self.detail = detail
        _viewModel = State(
            initialValue: AniListReviewsViewModel(
                ref: detail.ref,
                mediaRepository: mediaRepository
            )
        )
    }

    var body: some View {
        ZStack {
            SpinePageBackground()
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    spoilerWarning
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                    reviewsContent
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            doneButton
        }
        .task {
            await viewModel.loadInitial()
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            reviewPoster
                .frame(width: 40, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text("AniList reviews for…")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                Text(detail.displayTitle)
                    .font(.system(size: 19, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
            }

            Spacer()

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 42, height: 42)
                    .background(.white.opacity(0.06), in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close AniList reviews")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(.white.opacity(0.08))
                .frame(height: 1)
        }
    }

    @ViewBuilder
    private var reviewPoster: some View {
        SpineAsyncImage(url: detail.displayPosterURL.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                LinearGradient(
                    colors: MediaTypeTheme.theme(for: "anime").gradientColors,
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                    .overlay {
                        Image(systemName: "play.rectangle.fill")
                            .foregroundStyle(.white.opacity(0.5))
                    }
            }
        }
        .accessibilityHidden(true)
    }

    private var spoilerWarning: some View {
        Label(
            "AniList reviews may contain spoilers.",
            systemImage: "exclamationmark.triangle.fill"
        )
        .font(.system(size: 12, weight: .bold))
        .foregroundStyle(.yellow.opacity(0.82))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(11)
        .background(.yellow.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
    }

    @ViewBuilder
    private var reviewsContent: some View {
        if viewModel.isLoadingInitial, viewModel.reviews.isEmpty {
            ProgressView("Loading AniList reviews…")
                .tint(.white)
                .foregroundStyle(.white.opacity(0.7))
                .frame(maxWidth: .infinity, minHeight: 180)
        } else if let error = viewModel.initialError, viewModel.reviews.isEmpty {
            errorState(message: error) {
                Task { await viewModel.retryInitial() }
            }
            .frame(minHeight: 180)
        } else if viewModel.reviews.isEmpty {
            Text("No written reviews.")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.58))
                .frame(maxWidth: .infinity, minHeight: 180)
        } else {
            ForEach(viewModel.reviews) { review in
                AniListReviewRow(review: review)
                    .padding(.horizontal, 16)
                    .onAppear {
                        guard viewModel.shouldLoadNext(after: review.id) else { return }
                        Task { await viewModel.loadNextPage() }
                    }
                Divider()
                    .overlay(.white.opacity(0.08))
                    .padding(.horizontal, 16)
            }
            paginationState
        }
    }

    @ViewBuilder
    private var paginationState: some View {
        if viewModel.isLoadingMore {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity)
                .padding(24)
        } else if let error = viewModel.paginationError {
            errorState(message: error) {
                Task { await viewModel.loadNextPage() }
            }
        }
    }

    private func errorState(message: String, retry: @escaping () -> Void) -> some View {
        VStack(spacing: 10) {
            Text(message)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.red.opacity(0.82))
                .multilineTextAlignment(.center)
            Button("Retry", action: retry)
                .font(.system(size: 13, weight: .heavy))
                .buttonStyle(.bordered)
                .tint(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
    }

    private var doneButton: some View {
        Button {
            dismiss()
        } label: {
            Text("Done")
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(.black)
                .frame(maxWidth: .infinity)
                .frame(height: 54)
                .background(.white, in: RoundedRectangle(cornerRadius: 15))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(SpinePalette.pageBackground.opacity(0.96))
        .accessibilityHint("Closes AniList reviews")
    }
}

private struct AniListReviewRow: View {
    @State private var isExpanded = false

    let review: AniListReview

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            reviewerHeader

            HStack {
                if let score = review.score {
                    Text("\(score)%")
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white)
                }
                Spacer()
                if let date = review.createdAt?.shortDateLabel {
                    Text(date)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.48))
                }
            }

            if let summary = review.summary?.nilIfEmpty {
                Text(summary)
                    .font(.system(size: 15, weight: .heavy))
                    .foregroundStyle(.white)
            }

            Text(review.body)
                .font(.system(size: 14, weight: .regular))
                .foregroundStyle(.white.opacity(0.76))
                .lineSpacing(3)
                .lineLimit(isExpanded ? nil : 5)

            if review.body.count > 180 {
                Button(isExpanded ? "SHOW LESS" : "SHOW MORE") {
                    isExpanded.toggle()
                }
                .font(.system(size: 11, weight: .heavy))
                .foregroundStyle(.white.opacity(0.58))
                .buttonStyle(.plain)
                .accessibilityValue(isExpanded ? "Expanded" : "Collapsed")
            }

            HStack(spacing: 14) {
                if let helpfulness {
                    Text(helpfulness)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.46))
                }
                Spacer()
                if let destination = aniListDestination(review.url) {
                    Link(destination: destination) {
                        Label("Original review", systemImage: "arrow.up.right")
                            .font(.system(size: 11, weight: .heavy))
                            .foregroundStyle(.white.opacity(0.72))
                    }
                }
            }
        }
        .padding(.vertical, 18)
    }

    @ViewBuilder
    private var reviewerHeader: some View {
        if let destination = aniListDestination(review.user.profileUrl) {
            Link(destination: destination) {
                reviewerIdentity
            }
            .accessibilityLabel("Open \(review.user.name)'s AniList profile")
        } else {
            reviewerIdentity
        }
    }

    private var reviewerIdentity: some View {
        HStack(spacing: 10) {
            SpineAsyncImage(url: aniListDestination(review.user.avatarUrl)) { phase in
                switch phase {
                case let .success(image):
                    image
                        .resizable()
                        .scaledToFill()
                default:
                    Circle()
                        .fill(.white.opacity(0.08))
                        .overlay {
                            Image(systemName: "person.fill")
                                .foregroundStyle(.white.opacity(0.42))
                        }
                }
            }
            .frame(width: 42, height: 42)
            .clipShape(Circle())

            Text(review.user.name)
                .font(.system(size: 15, weight: .heavy))
                .foregroundStyle(.white)
                .lineLimit(2)
        }
    }

    private var helpfulness: String? {
        switch (review.communityRating, review.communityRatingCount) {
        case let (rating?, count?):
            "\(rating.formatted()) helpful score · \(count.formatted()) votes"
        case let (rating?, nil):
            "\(rating.formatted()) helpful score"
        case let (nil, count?):
            "\(count.formatted()) votes"
        case (nil, nil):
            nil
        }
    }
}

private func aniListDestination(_ rawValue: String?) -> URL? {
    guard
        let rawValue,
        let url = URL(string: rawValue),
        ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
        url.host != nil
    else { return nil }
    return url
}

private struct SpineRatingDistributionSection: View {
    let community: CommunityStats?
    let mediaType: String
    let reviewCount: Int
    let onSeeReviews: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                if let average = community?.averageRating {
                    Label(average.starRatingLabel(mediaType: mediaType), systemImage: "star.fill")
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white)
                }
                Spacer()
                Text("\((community?.ratingCount ?? 0).formatted()) ratings")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white.opacity(0.52))
            }

            if buckets.isEmpty {
                Text("No ratings yet.")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.58))
            } else {
                HStack(alignment: .bottom, spacing: 8) {
                    ForEach(buckets, id: \.rating) { bucket in
                        VStack(spacing: 6) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.white.opacity(0.82))
                                .frame(width: 18, height: max(4, CGFloat(bucket.count) / CGFloat(maxCount) * 70))
                            Text(bucket.rating)
                                .font(.system(size: 9, weight: .heavy))
                                .foregroundStyle(.white.opacity(0.58))
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
                .frame(height: 96, alignment: .bottom)
            }

            if reviewCount > 0 {
                Button(action: onSeeReviews) {
                    HStack(spacing: 5) {
                        Text(reviewsButtonTitle)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .heavy))
                    }
                    .font(.system(size: 12, weight: .heavy))
                    .foregroundStyle(.white.opacity(0.82))
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity, alignment: .trailing)
                .accessibilityHint("Opens every written review")
            }
        }
        .padding(14)
        .background(Color.white.opacity(0.025), in: RoundedRectangle(cornerRadius: 16))
    }

    private var buckets: [RatingDistributionBucket] {
        let rawBuckets = community?.ratingDistribution ?? []
        guard !rawBuckets.isEmpty else { return [] }
        let counts = Dictionary(
            grouping: rawBuckets,
            by: { $0.rating.starRatingStep(mediaType: mediaType) }
        )
            .mapValues { $0.reduce(0) { $0 + $1.count } }
        return (1...10).map { step in
            RatingDistributionBucket(rating: String.starRatingLabel(forStep: step), count: counts[step, default: 0])
        }
    }

    private var maxCount: Int {
        max(buckets.map(\.count).max() ?? 1, 1)
    }

    private var reviewsButtonTitle: String {
        reviewCount == 1 ? "See 1 review" : "See all \(reviewCount.formatted()) reviews"
    }
}

private struct CreditDisplay: Identifiable {
    let name: String
    let subtitle: String?
    let imageUrl: String?
    let personRef: PersonRef?

    var id: String { "\(name):\(subtitle ?? ""):\(personRef?.id ?? "")" }
}

private struct CreditSection: View {
    let title: String
    let cast: [CreditDisplay]
    let crew: [CreditDisplay]
    let onSelect: (PersonRef) -> Void
    @State private var selectedTab: CreditTab

    init(
        title: String,
        cast: [CreditDisplay],
        crew: [CreditDisplay],
        onSelect: @escaping (PersonRef) -> Void
    ) {
        self.title = title
        self.cast = cast
        self.crew = crew
        self.onSelect = onSelect
        _selectedTab = State(initialValue: title == "Characters & Creators" ? .crew : .cast)
    }

    private var visiblePeople: [CreditDisplay] {
        switch selectedTab {
        case .cast:
            cast.isEmpty ? crew : cast
        case .crew:
            crew.isEmpty ? cast : crew
        }
    }

    var body: some View {
        if !visiblePeople.isEmpty {
            VStack(alignment: .leading, spacing: 13) {
                HStack {
                    SectionLabel(title: title)
                    Spacer()
                    if !cast.isEmpty && !crew.isEmpty {
                        creditTabs
                    }
                }

                LazyVStack(spacing: 7) {
                    ForEach(visiblePeople) { person in
                        creditRow(person)
                    }
                }
            }
        }
    }

    private var creditTabs: some View {
        HStack(spacing: 4) {
            if title == "Characters & Creators" {
                creditTab(.crew)
                creditTab(.cast)
            } else {
                creditTab(.cast)
                creditTab(.crew)
            }
        }
        .padding(3)
        .background(.white.opacity(0.055), in: Capsule())
    }

    private func creditTab(_ tab: CreditTab) -> some View {
        Button {
            selectedTab = tab
        } label: {
            Text(tabTitle(tab))
                .font(.system(size: 11, weight: .heavy))
                .foregroundStyle(selectedTab == tab ? .white.opacity(0.9) : .white.opacity(0.52))
                .padding(.horizontal, 10)
                .frame(height: 24)
                .background(selectedTab == tab ? .white.opacity(0.13) : .clear, in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(tabTitle(tab))
        .accessibilityAddTraits(selectedTab == tab ? .isSelected : [])
    }

    private func tabTitle(_ tab: CreditTab) -> String {
        if tab == .crew, title == "Cast & Characters" {
            return "Characters"
        }
        if title == "Characters & Creators" {
            return tab == .cast ? "Characters" : "Creators"
        }
        return tab.title
    }

    private func creditRow(_ person: CreditDisplay) -> some View {
        Group {
            if let personRef = person.personRef {
                Button {
                    onSelect(personRef)
                } label: {
                    creditRowContent(person, showsChevron: true)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(accessibilityLabel(for: person))
                .accessibilityHint("Opens person details")
            } else {
                creditRowContent(person, showsChevron: false)
                    .accessibilityElement(children: .combine)
            }
        }
    }

    private func creditRowContent(_ person: CreditDisplay, showsChevron: Bool) -> some View {
        HStack(spacing: 10) {
            SpineAsyncImage(url: URL(string: person.imageUrl ?? "")) { phase in
                switch phase {
                case let .success(image):
                    image.resizable().scaledToFill()
                default:
                    Circle()
                        .fill(.white.opacity(0.12))
                        .overlay {
                            Image(systemName: "person.fill")
                                .foregroundStyle(.white.opacity(0.7))
                        }
                }
            }
            .frame(width: 38, height: 38)
            .clipShape(Circle())

            VStack(alignment: .leading, spacing: 3) {
                Text(person.name)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.88))
                    .lineLimit(1)

                if let subtitle = person.subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(.white.opacity(0.52))
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.38))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .frame(minHeight: 52)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.white.opacity(0.12), in: Capsule())
        .contentShape(Capsule())
    }

    private func accessibilityLabel(for person: CreditDisplay) -> String {
        guard let subtitle = person.subtitle, !subtitle.isEmpty else {
            return "View \(person.name)"
        }
        return "View \(person.name), \(subtitle)"
    }
}

private enum CreditTab {
    case cast
    case crew

    var title: String {
        switch self {
        case .cast: "Cast"
        case .crew: "Crew"
        }
    }
}

private struct SeasonsSection: View {
    let seasons: [SeasonSummary]
    let onSelect: (SeasonSummary) -> Void

    var body: some View {
        if !seasons.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                SectionLabel(title: "Seasons")
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(seasons) { season in
                            Button {
                                onSelect(season)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    MediaArtwork(
                                        url: season.imageUrl,
                                        title: season.title,
                                        slot: .seasonCard,
                                        mediaType: "season"
                                    )
                                    Text(season.title)
                                        .font(.system(size: 12, weight: .heavy))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                    if let count = season.episodeCount {
                                        Text("\(count) episodes")
                                            .font(.system(size: 11, weight: .semibold))
                                            .foregroundStyle(.white.opacity(0.55))
                                            .lineLimit(1)
                                    }
                                }
                                .frame(width: MediaDetailLayout.seasonPosterSize.width, alignment: .topLeading)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(season.title)")
                            .frame(width: MediaDetailLayout.seasonPosterSize.width, alignment: .topLeading)
                        }
                    }
                }
            }
        }
    }
}

private struct EpisodesSection: View {
    let episodes: [EpisodeSummary]
    let completion: CompletionProgress?
    let onSelect: (EpisodeSummary) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                SectionLabel(title: "Episodes")
                Spacer()
                if let completion, completion.isVisible {
                    SWCompletionProgressButton(progress: completion)
                }
            }
            if episodes.isEmpty {
                ContentUnavailableView(
                    "No episodes available",
                    systemImage: "play.rectangle",
                    description: Text("Episode information has not been released yet.")
                )
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 130)
                .mediaDetailSurface(cornerRadius: 18)
            } else {
                LazyVStack(spacing: 16) {
                    ForEach(episodes) { episode in
                        EpisodeCard(episode: episode) {
                            onSelect(episode)
                        }
                    }
                }
            }
        }
    }
}

private struct EpisodeCard: View {
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    let episode: EpisodeSummary
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            if dynamicTypeSize.isAccessibilitySize {
                accessibilityLayout
            } else {
                compactLayout
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityDescription)
        .accessibilityHint("Opens episode details")
        .accessibilityAddTraits(.isButton)
    }

    private var compactLayout: some View {
        HStack(spacing: 12) {
            EpisodeCardStill(episode: episode, fillsWidth: false)

            episodeCopy

            Image(systemName: "chevron.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(.white.opacity(0.32))
                .accessibilityHidden(true)
        }
        .padding(.trailing, 12)
        .frame(maxWidth: .infinity, minHeight: 84, maxHeight: 84, alignment: .leading)
        .background(cardBackground)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var accessibilityLayout: some View {
        VStack(alignment: .leading, spacing: 12) {
            EpisodeCardStill(episode: episode, fillsWidth: true)
            episodeCopy
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(cardBackground)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private var episodeCopy: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(episode.title)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(.white)
                .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 2)
                .frame(maxWidth: .infinity, alignment: .leading)

            if !metadata.isEmpty {
                Text(metadata)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? nil : 1)
            }

            if let overview = episode.overview?.nilIfEmpty {
                Text(overview)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.white.opacity(0.62))
                    .lineLimit(dynamicTypeSize.isAccessibilitySize ? 3 : 1)
            }
        }
    }

    private var metadata: String {
        [episode.airDate?.longDateLabel, episode.runtime]
            .compactMap { $0?.nilIfEmpty }
            .joined(separator: " · ")
    }

    private var accessibilityDescription: String {
        var components = ["Episode \(episode.episodeNumber), \(episode.title)"]
        if let airDate = episode.airDate?.longDateLabel.nilIfEmpty {
            components.append("Aired \(airDate)")
        }
        if let runtime = episode.runtime?.nilIfEmpty {
            components.append(runtime)
        }
        if let overview = episode.overview?.nilIfEmpty {
            components.append(overview)
        }
        return components.joined(separator: ". ")
    }

    private var cardBackground: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return shape
            .fill(.white.opacity(0.035))
            .overlay { shape.stroke(.white.opacity(0.055), lineWidth: 0.75) }
    }
}

private struct EpisodeCardStill: View {
    let episode: EpisodeSummary
    let fillsWidth: Bool

    var body: some View {
        artwork
            .aspectRatio(16 / 9, contentMode: .fill)
            .frame(maxWidth: fillsWidth ? .infinity : nil)
            .frame(width: fillsWidth ? nil : 148, height: fillsWidth ? nil : 84)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(alignment: .bottomLeading) {
                Text("E\(episode.episodeNumber)")
                    .font(.caption2.weight(.black))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 4)
                    .background(.black.opacity(0.62), in: Capsule())
                    .padding(7)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(.white.opacity(0.08), lineWidth: 0.75)
            }
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var artwork: some View {
        SpineAsyncImage(url: episode.imageUrl.flatMap(URL.init(string:))) { phase in
            switch phase {
            case let .success(image):
                image
                    .resizable()
                    .scaledToFill()
            default:
                placeholder
            }
        }
    }

    private var placeholder: some View {
        let theme = MediaTypeTheme.theme(for: "episode")
        return ZStack {
            LinearGradient(
                colors: theme.gradientColors.map { $0.opacity(0.78) },
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: "play.rectangle.fill")
                .font(.title2)
                .foregroundStyle(.white.opacity(0.56))
        }
    }
}

private struct RecommendationsSection: View {
    let sections: [RelatedMediaSection]
    let onSelectSection: ((RelatedMediaSection) -> Void)?
    let onSelect: (MediaSummary) -> Void

    init(
        sections: [RelatedMediaSection],
        onSelectSection: ((RelatedMediaSection) -> Void)? = nil,
        onSelect: @escaping (MediaSummary) -> Void
    ) {
        self.sections = sections
        self.onSelectSection = onSelectSection
        self.onSelect = onSelect
    }

    var body: some View {
        ForEach(sections.filter { !$0.items.isEmpty }) { section in
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 12) {
                    if (section.id == "series" || section.id == "collection"),
                       let onSelectSection {
                        Button {
                            onSelectSection(section)
                        } label: {
                            HStack(spacing: 6) {
                                SectionLabel(title: section.title)
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 9, weight: .heavy))
                                    .foregroundStyle(.white.opacity(0.42))
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("View \(section.title) series")
                    } else {
                        SectionLabel(title: section.title)
                    }

                    Spacer()
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(section.items) { item in
                            Button {
                                onSelect(item)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    MediaArtwork(
                                        url: item.displayPosterURL,
                                        title: item.displayTitle,
                                        slot: .carousel,
                                        mediaType: item.ref.mediaType,
                                        orientation: item.posterOrientation
                                    )
                                    .overlay(alignment: .bottomLeading) {
                                        if let relation = item.relation?.nilIfEmpty {
                                            Text(relation)
                                                .font(.system(size: 9, weight: .heavy))
                                                .foregroundStyle(.white.opacity(0.92))
                                                .lineLimit(1)
                                                .padding(.horizontal, 7)
                                                .frame(height: 20)
                                                .background(.black.opacity(0.72), in: Capsule())
                                                .padding(6)
                                        }
                                    }
                                    Text(item.displayTitle)
                                        .font(.system(size: 12, weight: .heavy))
                                        .foregroundStyle(.white)
                                        .lineLimit(2)
                                        .frame(height: 32, alignment: .topLeading)
                                }
                                .frame(
                                    width: MediaDetailLayout.recommendationPosterSize.width,
                                    height: MediaDetailLayout.recommendationCardHeight,
                                    alignment: .topLeading
                                )
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(item.displayTitle)")
                        }
                    }
                }
            }
        }
    }

}

private extension JSONValue {
    var displayString: String? {
        switch self {
        case let .string(value):
            value
        case let .number(value):
            value.rounded() == value ? String(Int(value)) : String(value)
        case let .bool(value):
            value ? "Yes" : "No"
        case let .object(value):
            value["name"]?.displayString ?? value["provider_name"]?.displayString
        case .array, .null:
            nil
        }
    }

    var displayStrings: [String] {
        switch self {
        case let .array(values):
            values.flatMap(\.displayStrings)
        case let .object(value):
            if let string = displayString {
                [string]
            } else {
                value.values.flatMap(\.displayStrings)
            }
        default:
            displayString.map { [$0] } ?? []
        }
    }

    var numberValue: Double? {
        switch self {
        case let .number(value):
            value
        case let .string(value):
            Double(value)
        default:
            nil
        }
    }

    var intValue: Int? {
        numberValue.map(Int.init)
    }

    var objectValue: [String: JSONValue]? {
        if case let .object(value) = self {
            return value
        }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case let .array(value) = self {
            return value
        }
        return nil
    }
}

private extension Color {
    init?(hex: String?) {
        guard let hex else { return nil }
        let value = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard value.count == 6, let int = Int(value, radix: 16) else { return nil }
        self.init(
            red: Double((int >> 16) & 0xFF) / 255,
            green: Double((int >> 8) & 0xFF) / 255,
            blue: Double(int & 0xFF) / 255
        )
    }
}

func supportsTitleLogo(_ detail: MediaDetail) -> Bool {
    detail.displayLogoURL != nil
}

struct MediaTitleDisplay: View {
    let detail: MediaDetail
    let title: String
    @Binding var showsLogo: Bool
    let font: Font
    let lineLimit: Int?
    let minimumScaleFactor: CGFloat
    let maxLogoHeight: CGFloat
    let alignment: Alignment
    let onTap: (() -> Void)?
    let onLongPress: (() -> Void)?

    init(
        detail: MediaDetail,
        title: String,
        showsLogo: Binding<Bool>,
        font: Font,
        lineLimit: Int?,
        minimumScaleFactor: CGFloat,
        maxLogoHeight: CGFloat,
        alignment: Alignment = .center,
        onTap: (() -> Void)?,
        onLongPress: (() -> Void)?
    ) {
        self.detail = detail
        self.title = title
        _showsLogo = showsLogo
        self.font = font
        self.lineLimit = lineLimit
        self.minimumScaleFactor = minimumScaleFactor
        self.maxLogoHeight = maxLogoHeight
        self.alignment = alignment
        self.onTap = onTap
        self.onLongPress = onLongPress
    }

    private var canToggle: Bool {
        supportsTitleLogo(detail)
    }

    private var isInteractive: Bool {
        onTap != nil || canToggle
    }

    var body: some View {
        Group {
            if canToggle, showsLogo, let logoUrl = detail.displayLogoURL, let url = URL(string: logoUrl) {
                SpineAsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        TitleLogoLayout(maxLogoHeight: maxLogoHeight, aspectRatio: aspectRatio, alignment: alignment) {
                            image
                                .resizable()
                                .scaledToFit()
                        }
                    case .failure:
                        titleText
                    default:
                        Color.clear
                            .frame(height: maxLogoHeight)
                    }
                }
                .frame(maxWidth: .infinity, alignment: alignment)
            } else {
                titleText
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            if let onTap {
                onTap()
                return
            }
            guard canToggle else { return }
            withAnimation(.easeInOut(duration: 0.2)) {
                showsLogo.toggle()
            }
        }
        .onLongPressGesture {
            guard canToggle, showsLogo else { return }
            onLongPress?()
        }
        .accessibilityLabel(title)
        .accessibilityHint(onTap != nil ? "Open media" : canToggle ? "Double tap to switch between logo and text title" : "")
        .accessibilityAddTraits(isInteractive ? .isButton : [])
    }

    private var titleText: some View {
        Text(title)
            .font(font)
            .foregroundStyle(.white)
            .lineLimit(lineLimit)
            .minimumScaleFactor(minimumScaleFactor)
    }

    private var aspectRatio: CGFloat? {
        if let ratio = detail.logoAspectRatio, ratio > 0 {
            return CGFloat(ratio)
        }
        if let width = detail.logoWidth, let height = detail.logoHeight, height > 0 {
            return CGFloat(width) / CGFloat(height)
        }
        return nil
    }
}

private struct TitleLogoLayout: Layout {
    let maxLogoHeight: CGFloat
    let aspectRatio: CGFloat?
    let alignment: Alignment

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        return CGSize(width: width, height: logoSize(for: width).height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let subview = subviews.first else { return }
        let size = logoSize(for: bounds.width)
        let x = alignment == .leading
            ? bounds.minX
            : alignment == .trailing
                ? bounds.maxX - size.width
                : bounds.midX - size.width / 2
        let origin = CGPoint(
            x: x,
            y: bounds.minY
        )
        subview.place(
            at: origin,
            proposal: ProposedViewSize(size)
        )
    }

    private func logoSize(for availableWidth: CGFloat) -> CGSize {
        titleLogoSize(
            availableWidth: availableWidth,
            maxLogoHeight: maxLogoHeight,
            aspectRatio: aspectRatio
        )
    }
}

func titleLogoSize(
    availableWidth: CGFloat,
    maxLogoHeight: CGFloat,
    aspectRatio: CGFloat?
) -> CGSize {
    guard availableWidth > 0, let aspectRatio, aspectRatio > 0 else {
        return CGSize(width: availableWidth, height: maxLogoHeight)
    }

    let targetWidth = availableWidth * 0.82
    let preferredHeight = min(
        max(targetWidth / aspectRatio, maxLogoHeight),
        maxLogoHeight * 1.45
    )
    let width = min(availableWidth, preferredHeight * aspectRatio)
    return CGSize(width: width, height: width / aspectRatio)
}

private extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }

    var yearPrefix: String? {
        count >= 4 ? String(prefix(4)) : nil
    }

    var starRatingLabel: String {
        guard let raw = Double(self) else { return self }
        let stars = raw / 2
        return "\(Self.cleanRating(stars))/5"
    }

    var starRatingValue: String {
        guard let raw = Double(self) else { return self }
        return String(format: "%.1f", raw / 2)
    }

    var starRatingStep: Int {
        guard let raw = Double(self) else { return 0 }
        return min(max(Int(round(raw)), 1), 10)
    }

    func starRatingLabel(mediaType: String) -> String {
        guard let raw = Double(self) else { return self }
        let stars = ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2
        return "\(Self.cleanRating(stars))/5"
    }

    func starRatingValue(mediaType: String) -> String {
        guard let raw = Double(self) else { return self }
        let stars = ["movie", "music", "book", "game"].contains(mediaType) ? raw : raw / 2
        return String(format: "%.1f", stars)
    }

    func starRatingStep(mediaType: String) -> Int {
        guard let raw = Double(self) else { return 0 }
        let step = ["movie", "music", "book", "game"].contains(mediaType) ? raw * 2 : raw
        return min(max(Int(round(step)), 1), 10)
    }

    var shortDateLabel: String {
        let trimmed = String(prefix(10))
        let input = DateFormatter()
        input.calendar = Calendar(identifier: .gregorian)
        input.locale = Locale(identifier: "en_US_POSIX")
        input.dateFormat = "yyyy-MM-dd"
        guard let date = input.date(from: trimmed) else { return trimmed }

        let output = DateFormatter()
        output.calendar = Calendar(identifier: .gregorian)
        output.locale = Locale.current
        output.dateFormat = "MMM d, yyyy"
        return output.string(from: date)
    }

    var longDateLabel: String {
        LongDateFormatter().string(from: self) ?? self
    }

    var oneDecimalLabel: String {
        guard let value = Double(self) else { return self }
        return String(format: "%.1f", value)
    }

    static func starRatingLabel(forStep step: Int) -> String {
        cleanRating(Double(step) / 2)
    }

    private static func cleanRating(_ value: Double) -> String {
        value.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(value))" : String(format: "%.1f", value)
    }
}

extension ExternalRating {
    var displayValue: String {
        let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if ["anilist", "rotten tomatoes", "rottentomatoes"].contains(source.lowercased()) {
            return trimmedValue.hasSuffix("%") ? trimmedValue : "\(trimmedValue)%"
        }
        if source.lowercased() == "hardcover" {
            let displayRating = Double(trimmedValue).map { rawValue in
                let value = rawValue > 5 ? rawValue / 2 : rawValue
                return value.rounded() == value ? "\(Int(value))" : String(format: "%.1f", value)
            } ?? trimmedValue
            return "\(displayRating)/5"
        }
        if trimmedValue.contains("/") || trimmedValue.hasSuffix("%") {
            return trimmedValue
        }
        if let denominator = ratingDenominator ?? maxValue?.nilIfEmpty {
            return "\(trimmedValue)/\(denominator)"
        }
        return trimmedValue
    }

    private var ratingDenominator: String? {
        switch source.lowercased() {
        case "spine", "letterboxd", "hardcover", "google books":
            "5"
        case "imdb":
            "10"
        default:
            nil
        }
    }

    var ratingAssetName: String? {
        switch source.lowercased() {
        case "imdb":
            "RatingIMDb"
        case "tmdb":
            "RatingTMDB"
        case "letterboxd":
            "RatingLetterboxd"
        case "rotten tomatoes":
            rottenTomatoesAssetName
        case "mal", "myanimelist":
            "RatingMAL"
        case "anilist":
            "RatingAniList"
        case "hardcover":
            "RatingHardcover"
        case "metacritic":
            "RatingMetacritic"
        case "steam":
            "RatingSteam"
        case "igdb":
            "RatingIGDB"
        default:
            nil
        }
    }

    private var rottenTomatoesAssetName: String {
        // ponytail: API only sends RT score; use percent thresholds until it sends certification.
        guard let score = value.split(whereSeparator: { !$0.isNumber && $0 != "." }).first.flatMap({ Double($0) }) else {
            return "RatingRottenTomatoes"
        }
        if score <= 59 {
            return "RatingRottenTomatoesRotten"
        }
        if score >= 75 {
            return "RatingRottenTomatoesCertifiedFresh"
        }
        return "RatingRottenTomatoes"
    }
}

extension String {

    var ratingCountLabel: String {
        switch lowercased() {
        case "letterboxd", "google books":
            "ratings"
        case "rotten tomatoes", "rottentomatoes", "steam":
            "reviews"
        default:
            "votes"
        }
    }

    var ratingAbbreviation: String {
        switch lowercased() {
        case "imdb":
            "IM"
        case "letterboxd":
            "LB"
        case "rotten tomatoes":
            "RT"
        case "tmdb":
            "TM"
        case "hardcover":
            "HC"
        case "igdb":
            "IG"
        case "metacritic":
            "MC"
        case "steam":
            "ST"
        case "mal":
            "MA"
        case "anilist":
            "AL"
        case "mangaupdates":
            "MU"
        case "openlibrary":
            "OL"
        case "google books":
            "Google Books"
        default:
            String(prefix(2)).uppercased()
        }
    }
}

private extension Array where Element == String {
    var joinedOrNil: String? {
        let value = joined(separator: ", ")
        return value.isEmpty ? nil : value
    }
}

private extension MediaDetail {
    var listSummary: MediaSummary {
        MediaSummary(
            ref: ref,
            title: title,
            preferredTitle: preferredTitle,
            subtitle: subtitle,
            overview: overview ?? synopsis,
            imageUrl: imageUrl,
            posterUrl: posterUrl,
            customPosterUrl: customPosterUrl,
            backdropUrl: backdropUrl,
            customBackdropUrl: customBackdropUrl,
            posterOrientation: posterOrientation,
            posterAspectRatio: posterAspectRatio,
            posterWidth: posterWidth,
            posterHeight: posterHeight,
            posterAccentColor: posterAccentColor,
            releaseDate: releaseDate,
            defaultSource: defaultSource,
            userState: userState
        )
    }
}

private struct LongDateFormatter {
    private let isoFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private let displayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale.current
        formatter.dateFormat = "MMMM d, yyyy"
        return formatter
    }()

    func string(from raw: String) -> String? {
        let trimmed = String(raw.prefix(10))
        guard let date = isoFormatter.date(from: trimmed) else { return nil }
        return displayFormatter.string(from: date)
    }
}
