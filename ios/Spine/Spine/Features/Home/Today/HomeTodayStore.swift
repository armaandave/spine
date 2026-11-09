import Foundation

/// What you did today on each title, from one request for today's Activity rows. It reads activity rows only:
/// marking something watched is never activity, so it never lights anything here.
@MainActor
@Observable
final class HomeTodayStore {
    /// Today's part per title (`titleKey`). Replaced whole by each answer; a failure keeps the last one.
    private(set) var parts: [String: TodayPart] = [:]

    /// A refresh that succeeded less than this long ago is not repeated unless forced.
    static let minimumInterval: TimeInterval = 20
    static let pageSize = 50

    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void
    /// Each request takes the next number; only the newest request's answer is used.
    private var generation = 0
    private var inFlight = false
    private var lastSuccess: Date?
    /// Whose parts, and which day's, `parts` holds and the last success was for.
    private var partsOwner: String?
    private var partsDay: String?

    init(activityRepository: ActivityRepository, onUnauthorized: @escaping () -> Void) {
        self.activityRepository = activityRepository
        self.onUnauthorized = onUnauthorized
    }

    /// The title a ref belongs to: a season or an episode folds into its show.
    nonisolated static func titleKey(for ref: MediaRef) -> String {
        let title = ref.parentTVRef ?? ref
        return "\(title.source):\(title.mediaType):\(title.mediaId)"
    }

    func part(for ref: MediaRef) -> TodayPart? {
        parts[Self.titleKey(for: ref)]
    }

    /// Reads today's rows. `now` and `calendar` are the phone's: the server's day is the saver's calendar day.
    func refresh(
        username: String,
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent,
        force: Bool = false
    ) async {
        let day = CalendarDateCodec.string(from: now, calendar: calendar)
        let sameScope = partsOwner == username && partsDay == day
        if !force {
            if inFlight, sameScope { return }
            if sameScope, let lastSuccess, (0 ..< Self.minimumInterval).contains(now.timeIntervalSince(lastSuccess)) {
                return
            }
        }
        // Another person's or another day's parts are not this one's to show while the answer is on its way.
        if !sameScope {
            parts = [:]
            lastSuccess = nil
        }
        partsOwner = username
        partsDay = day

        generation += 1
        let mine = generation
        inFlight = true
        do {
            let response = try await activityRepository.userActivityList(
                username: username,
                filter: ActivityListFilter(startDate: day, endDate: day),
                pageSize: Self.pageSize,
                cursorLink: nil
            )
            guard mine == generation else { return }
            inFlight = false
            parts = Self.parts(from: response.results)
            lastSuccess = now
        } catch is CancellationError {
            if mine == generation { inFlight = false }
        } catch {
            if mine == generation { inFlight = false }
            if case APIError.unauthorized = error { onUnauthorized() }
        }
    }

    /// Progress days only, summed per title. A title's first row (the newest) names its unit; a row in another unit
    /// would add apples to oranges, so it is left out.
    nonisolated static func parts(from rows: [ActivityItem]) -> [String: TodayPart] {
        var result: [String: TodayPart] = [:]
        for row in rows {
            guard
                row.type == "progress_updated",
                case .progress = row.post,
                let media = row.media,
                let told = ToldAmount.parse(
                    label: row.object.label,
                    unit: row.object.unit,
                    amount: row.object.amount,
                    percent: row.object.percent
                )
            else { continue }
            let key = titleKey(for: media.ref)
            if var existing = result[key] {
                guard existing.unit == told.unit else { continue }
                existing.amount += told.amount
                result[key] = existing
            } else {
                result[key] = told
            }
        }
        return result
    }
}
