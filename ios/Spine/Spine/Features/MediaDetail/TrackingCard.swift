import SwiftUI

/// What the media page's Your Tracking card says, one shape for every type: your marks, a caption in the Friends
/// cards' form ("Watching · 9 of 85"), one date, and Home's activity bar with today's part lit.
struct TrackingCardModel: Equatable {
    /// The status word ("Watching", "Reading", "Completed"), a season's place ("6 of 10") or "Watched".
    var word: String?
    /// What follows the word: where the title stands and how many logs. Nil when there is nothing more.
    var detail: String?
    /// "Logged Nov 18", "Started Oct 27", "Started Jan 17, 2025": a single log's day, else the start.
    var date: String?
    var marks: FriendCardMarks
    /// Your latest log's review, as a friend's card shows theirs: nil when it has none, or when it is marked as a
    /// spoiler (the eye says so instead).
    var review: Review?
    /// "3.5", for VoiceOver.
    var starsText: String?
    /// Home's bar: where the title stands, today's part lit. Nil when there is nothing honest to draw.
    var meter: ProgressMeterModel?
    /// The bar ends in the check: the title is finished.
    var finished: Bool
    var showsUpdate: Bool
    /// A tap on the card opens your log, or your diary for the title when it has several.
    var opensDiary: Bool

    struct Review: Equatable {
        var title: String?
        var text: String
    }

    static func make(
        detail: MediaDetail,
        state: UserMediaState?,
        preferredMode: ProgressUpdateMode?,
        today: TodayPart?,
        baseline: Double?,
        now: Date = Date(),
        calendar: Calendar = .autoupdatingCurrent
    ) -> TrackingCardModel {
        let ref = detail.ref
        let status = TrackingSummarySection.status(detail: detail, state: state)
        let progress = TrackingSummarySection.progressLines(
            detail: detail, state: state, preferredMode: preferredMode, countSuffix: ""
        )
        let parts = progress + [TrackingSummarySection.logLine(state)].compactMap { $0 }

        var date: String?
        if !TrackingSummarySection.hasMultipleLogs(state), let logged = state?.diaryConsumedAt {
            date = dayText(logged, now: now, calendar: calendar).map { "Logged \($0)" }
        }
        if date == nil, ref.mediaType != "movie", let start = state?.displayStartDate {
            let day = ref.isTVFamily || ref.isAnime ? TVTrackingPresentation.startDay(start, calendar: calendar) : start
            date = dayText(day, now: now, calendar: calendar).map { "Started \($0)" }
        }

        var meter: ProgressMeterModel?
        if let place = place(detail: detail, state: state) {
            var lit = TileTodayMath.today(
                server: today,
                baseline: baseline,
                current: place.current,
                unit: place.unit,
                total: place.pagesTotal ?? place.total
            )
            // A season's page counts the show's day, never more than the season has watched.
            if ref.mediaType == "season" { lit = min(lit, place.current) }
            meter = ProgressMeterModel.make(
                unit: InProgressTilePresenter.meterUnit(for: place.unit),
                from: whole(place.current - lit),
                to: whole(place.current),
                total: place.total.map(whole)
            )
        }
        let done = state?.status == "Completed" || status == "Watched"
        let reviewText = state?.diaryReview.flatMap(TrackingCardModel.words)
        let reviewTitle = state?.diaryReviewTitle.flatMap(TrackingCardModel.words)
        // As on a friend's card, a spoiler review shows the eye, not its words.
        let spoiler = state?.diaryContainsSpoilers == true && (reviewText != nil || reviewTitle != nil)

        return TrackingCardModel(
            word: status.map { ref.trackingStatusLabel($0) },
            detail: parts.isEmpty ? nil : parts.joined(separator: " · "),
            date: date,
            marks: FriendCardMarks(
                stars: (state?.rating.map { PostPageStyle.starSymbols(rating: $0) } ?? [])
                    .compactMap(FeedMark.init(starSymbol:)),
                liked: state?.hasLiked == true,
                rewatch: state?.diaryIsRewatch == true,
                spoiler: spoiler
            ),
            // A review with only a title shows the title as its words.
            review: spoiler ? nil : (reviewText ?? reviewTitle).map { Review(title: reviewText == nil ? nil : reviewTitle, text: $0) },
            starsText: TrackingSummarySection.stars(detail: detail, state: state),
            meter: meter,
            finished: done && (meter?.endFraction ?? 0) >= 1,
            showsUpdate: TrackingSummarySection.showsUpdateProgress(ref: ref, status: status, state: state),
            opensDiary: (state?.diaryCount ?? 0) > 0 || state?.diaryEntryId != nil
        )
    }

    /// Where the title stands in the unit its bar counts, before today is taken off. The page keeps it as the baseline
    /// for progress made on the page itself.
    static func current(detail: MediaDetail, state: UserMediaState?) -> Double? {
        place(detail: detail, state: state)?.current
    }

    /// Home's own position for the title (`InProgressTilePresenter.position`), or a season page's episodes. Nil for
    /// an episode, a movie or an album, and for anything not started.
    private static func place(detail: MediaDetail, state: UserMediaState?) -> TilePosition? {
        guard let state else { return nil }
        switch detail.ref.mediaType {
        case "episode":
            return nil
        case "season":
            guard let season = state.tv?.season, let watched = season.watchedCount, watched > 0 else { return nil }
            let total = [season.episodeCount, season.airedCount].compactMap { $0 }.first { $0 > 0 }
            return TilePosition(unit: .episodes, current: Double(watched), total: total.map(Double.init), pagesTotal: nil)
        default:
            let tracking = TrendingRailRing.trackingState(from: state)
            guard let position = InProgressTilePresenter.position(ref: detail.ref, tracking: tracking),
                  position.current > 0 else { return nil }
            return position
        }
    }

    /// The text without surrounding spaces, or nil when nothing is left.
    static func words(_ text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func whole(_ value: Double) -> Int {
        Int(max(0, value).rounded())
    }

    /// "Nov 18" this year, "Jan 17, 2025" any other: the card's one date style.
    static func dayText(_ raw: String?, now: Date = Date(), calendar: Calendar = .autoupdatingCurrent) -> String? {
        guard let date = TVTrackingPresentation.calendarDate(raw, calendar: calendar) else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        let sameYear = calendar.component(.year, from: date) == calendar.component(.year, from: now)
        formatter.dateFormat = sameYear ? "MMM d" : "MMM d, yyyy"
        return formatter.string(from: date)
    }
}

/// The Your Tracking card: your row in the Friends cards' form over a soft wash of the poster, Home's bar under it,
/// and the Up next episode and an anime's next title inside the same card.
struct TrackingCard: View {
    struct UpNext {
        let pointer: TVEpisodePointer
        let row: TVEpisodeRowModel
        /// "S1 E10", or an anime's "E13".
        let code: String
        let trailing: TVEpisodeTrailing
        let onTick: () -> Void
        let onMarkUpTo: (() -> Void)?
        let onSelect: (() -> Void)?
    }

    private static let shape = RoundedRectangle(cornerRadius: 24, style: .continuous)

    let model: TrackingCardModel
    let posterURL: String?
    /// The title's color, for a faint wash; nil until the poster's colors are known.
    let tint: Color?
    let palette: MeterPalette
    let avatarURL: String?
    let onOpenDiary: (() -> Void)?
    let onUpdateProgress: () -> Void
    let upNext: UpNext?
    let nextTitle: AnimeNextTitle?
    let onOpenNextTitle: ((AnimeNextTitle) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            summary
            if let meter = model.meter {
                HStack(alignment: .top, spacing: 6) {
                    ProgressMeterView(model: meter, palette: palette)
                    if model.finished {
                        LogCheckMark(color: palette.todayColor)
                            .padding(.top, -3)
                    }
                }
                .padding(.top, 10)
            }
            if let upNext {
                upNextRow(upNext)
                    .padding(.top, 14)
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("tv.up-next")
            }
            if let nextTitle, let onOpenNextTitle {
                nextTitleRow(nextTitle, open: onOpenNextTitle)
                    .padding(.top, 10)
                    .accessibilityIdentifier("anime.next-title")
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background { background }
        .overlay { Self.shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.5) }
        .overlay(alignment: .top) { specular }
    }

    // MARK: You

    /// Your picture, "You" and your marks, then the caption: one tap target that opens your log.
    private var summary: some View {
        let content = VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                UserAvatar(urlString: avatarURL, size: 40)
                VStack(alignment: .leading, spacing: 4) {
                    Text("You")
                        .font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white)
                    if !model.marks.isEmpty {
                        marksRow
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(height: 40)
            if model.word != nil || model.detail != nil || model.date != nil {
                captionRow
            }
            if let review = model.review {
                reviewText(review)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())

        return Group {
            if let onOpenDiary {
                Button(action: onOpenDiary) { content }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens your log")
            } else {
                content
            }
        }
        .overlay(alignment: .topTrailing) {
            if model.showsUpdate {
                updateButton
                    .frame(height: 40)
            }
        }
    }

    private var marksRow: some View {
        HStack(spacing: 8) {
            if !model.marks.stars.isEmpty {
                HStack(spacing: 1.5) {
                    ForEach(Array(model.marks.stars.enumerated()), id: \.offset) { _, mark in
                        FeedMarkView(mark: mark, ink: .dark, size: 12)
                    }
                }
            }
            if model.marks.liked { FeedMarkView(mark: .heart, ink: .dark, size: 12) }
            if model.marks.rewatch { FeedMarkView(mark: .rewatch, ink: .dark, size: 12) }
            if model.marks.spoiler { FeedMarkView(mark: .spoiler, ink: .dark, size: 14) }
        }
        .frame(height: 16, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            [
                model.starsText.map { "Rated \($0) out of 5 stars" },
                model.marks.liked ? "Liked" : nil,
                model.marks.rewatch ? "Rewatch" : nil,
                model.marks.spoiler ? "Review contains spoilers" : nil,
            ]
            .compactMap { $0 }
            .joined(separator: ", ")
        )
    }

    /// "Watching · 9 of 85" on the left, as a friend's card reads, and the date on the right.
    private var captionRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            HStack(spacing: 0) {
                if let word = model.word {
                    Text(word)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.9))
                }
                if let detail = model.detail {
                    Text(model.word == nil ? detail : " · \(detail)")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white.opacity(0.72))
                }
            }
            .lineLimit(1)
            .frame(maxWidth: .infinity, alignment: .leading)
            if let date = model.date {
                Text(date)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(.white.opacity(0.58))
                    .lineLimit(1)
                    .fixedSize()
            }
        }
        .monospacedDigit()
    }

    /// Your review, two lines at most, its title included, as a friend's card shows theirs. Your log has the rest.
    private func reviewText(_ review: TrackingCardModel.Review) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            if let title = review.title {
                Text(title)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(.white.opacity(0.94))
                    .lineLimit(1)
            }
            Text(review.text)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(.white.opacity(0.84))
                .lineLimit(review.title == nil ? 2 : 1)
        }
        .lineSpacing(2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var updateButton: some View {
        Button(action: onUpdateProgress) {
            Text("Update")
                .font(.system(size: 13, weight: .semibold))
                .padding(.horizontal, 14)
                .frame(height: 30)
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .capsule)
        .accessibilityLabel("Update Progress")
    }

    // MARK: Up next and the next title

    private func upNextRow(_ upNext: UpNext) -> some View {
        let word = upNext.pointer.isNew ? "New episode" : "Up next"
        let line = [word, upNext.code, TrackingCardModel.dayText(upNext.pointer.airDate)]
            .compactMap { $0 }
            .joined(separator: " · ")
        let copy = HStack(spacing: 12) {
            SpineAsyncImage(url: upNext.pointer.imageUrl.flatMap(URL.init(string:))) { phase in
                if case let .success(image) = phase {
                    image.resizable().scaledToFill()
                } else {
                    Color.white.opacity(0.06)
                }
            }
            .frame(width: 64, height: 36)
            .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(upNext.row.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text(line)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.white.opacity(0.62))
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(word), \(upNext.code), \(upNext.row.title)")

        return HStack(spacing: 4) {
            if let onSelect = upNext.onSelect {
                Button(action: onSelect) { copy }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens episode details")
            } else {
                copy
            }
            switch upNext.trailing {
            case let .tick(watched):
                TVEpisodeTickButton(isWatched: watched, episodeTitle: "Episode \(upNext.pointer.episodeNumber)", action: upNext.onTick)
                    .padding(.trailing, -10)
            case let .airDate(text):
                Text(text)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.5))
                    .accessibilityHidden(true)
            case .none:
                EmptyView()
            }
        }
        .padding(.top, 10)
        .overlay(alignment: .top) { hairline }
        .contextMenu {
            if let onMarkUpTo = upNext.onMarkUpTo {
                Button("Mark Watched Up to Here", systemImage: "checkmark.circle", action: onMarkUpTo)
            }
        }
    }

    private func nextTitleRow(_ next: AnimeNextTitle, open: @escaping (AnimeNextTitle) -> Void) -> some View {
        Button { open(next) } label: {
            HStack(spacing: 8) {
                Text("Next")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.62))
                Text(next.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.white.opacity(0.45))
            }
            .frame(height: 40)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Next: \(next.title)")
        .padding(.top, 2)
        .overlay(alignment: .top) { hairline }
    }

    // MARK: Surface

    private var hairline: some View {
        Rectangle()
            .fill(.white.opacity(0.12))
            .frame(height: 0.5)
            .accessibilityHidden(true)
    }

    /// The poster, blurred, under a dark shade and a faint wash of its own color.
    private var background: some View {
        ZStack {
            BlurredArtBackground(
                url: posterURL.flatMap(URL.init(string:)),
                accentHex: nil,
                style: .plate,
                artOpacity: 0.55,
                baseOpacity: 0
            )
            Color(red: 18 / 255, green: 18 / 255, blue: 17 / 255).opacity(0.58)
            (tint ?? .clear).opacity(0.08)
        }
        .clipShape(Self.shape)
        .accessibilityHidden(true)
    }

    /// The top edge's catch of light, as on a friend's card.
    private var specular: some View {
        Rectangle()
            .fill(LinearGradient(
                stops: [
                    .init(color: .white.opacity(0), location: 0),
                    .init(color: .white.opacity(0.75), location: 0.5),
                    .init(color: .white.opacity(0), location: 1),
                ],
                startPoint: .leading,
                endPoint: .trailing
            ))
            .frame(height: 1)
            .padding(.horizontal, 20)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}
