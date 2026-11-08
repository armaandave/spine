import Foundation

/// Every rule about what a feed card says, as one pure function from an activity item to a `FeedCardModel`. The views
/// only draw the result. It never edits `ActivityFeedPresentation`, whose wording is pinned elsewhere.
enum FeedCardPresenter {
    /// - Parameters:
    ///   - standing: where the person stands on the title. Used only when `isNewestOfTitle`: an older card of the
    ///     same title never gets a meter or a "where".
    ///   - planning: the titles of a Planning day, once loaded. Until then a Planning card shows its one face.
    static func model(
        for item: ActivityItem,
        standing: FeedStanding? = nil,
        isNewestOfTitle: Bool,
        planning: PlanningDay? = nil,
        dayLabel: String? = nil
    ) -> FeedCardModel {
        if item.planning != nil {
            return planningModel(for: item, day: planning, dayLabel: dayLabel)
        }

        let (title, tag) = titleAndTag(for: item)
        let standing = isNewestOfTitle ? standing : nil
        let review = ActivityFeedPresentation.reviewText(for: item)
        let kind: FeedCardKind
        let headline: FeedHeadline?
        var meter: ProgressMeterModel?

        switch item.object.type {
        case "diary":
            kind = .log
            headline = logHeadline(for: item)
        case "progress_day":
            let day = progressDay(for: item, standing: standing)
            kind = day.kind
            headline = day.headline
            meter = day.meter
        default:
            kind = .other
            headline = FeedHeadline.status(words: capitalized(ActivityFeedPresentation.actionText(for: item)), tail: nil)
        }

        let details = FeedDetailPlanner.rows(
            hasMeter: meter != nil,
            hasHeadline: headline != nil,
            spoilers: item.containsSpoilers == true,
            review: review,
            favorite: nil,
            quiet: nil
        )
        let engagement = item.engagement
        let hint = ActivityFeedPresentation.destinationHint(for: item)

        return FeedCardModel(
            id: item.id,
            kind: kind,
            username: item.actor.username,
            avatarURL: item.actor.avatarUrl,
            dayLabel: dayLabel,
            title: title,
            tag: tag,
            headline: headline,
            details: details,
            meter: meter,
            engagement: engagement,
            art: art(for: item),
            accessibilityLabel: accessibilityLabel(
                username: item.actor.username,
                dayLabel: dayLabel,
                title: title,
                tag: tag,
                headlineWords: headlineWords(headline, item: item),
                details: details,
                engagement: engagement
            ),
            accessibilityHint: hint,
            destination: ActivityDestination.resolve(item)
        )
    }

    // MARK: Title and tag

    static func titleAndTag(for item: ActivityItem) -> (title: String, tag: String?) {
        guard let media = item.media else {
            let fallback = ActivityFeedPresentation.clean(item.person?.name)
                ?? ActivityFeedPresentation.listName(for: item)
                ?? ""
            return (fallback, nil)
        }
        let ref = media.ref
        let isAnime = ref.isAnime || ref.source == "mal"
        switch ref.mediaType {
        case "episode":
            var tag: String?
            if let episode = ref.episodeNumber {
                if isAnime {
                    tag = AnimeTrackingPresentation.episodeCode(episode)
                } else if let season = ref.seasonNumber {
                    tag = TVTrackingPresentation.episodeCode(season: season, episode: episode)
                }
            }
            return (showTitle(of: media), tag)
        case "season":
            let tag = isAnime ? nil : ref.seasonNumber.map(TVTrackingPresentation.seasonTitle)
            return (showTitle(of: media), tag)
        default:
            return (media.displayTitle, nil)
        }
    }

    /// The show's title, never `displayTitle`, which appends "S2" to a season.
    private static func showTitle(of media: MediaSummary) -> String {
        if let title = ActivityFeedPresentation.clean(media.showTitle) { return title }
        if let title = ActivityFeedPresentation.clean(media.preferredTitle) { return title }
        return media.title
    }

    // MARK: Headlines

    private static func logHeadline(for item: ActivityItem) -> FeedHeadline? {
        let summary = ActivityFeedPresentation.ratingSummary(for: item)
        let symbols = summary.starSymbols
        let rewatch = ActivityFeedPresentation.isRewatch(item)
        guard !symbols.isEmpty || summary.liked || rewatch else { return nil }
        return .marks(
            starSymbols: symbols,
            liked: summary.liked,
            rewatch: rewatch,
            repeatLabel: item.media?.ref.repeatLabel ?? "Rewatch"
        )
    }

    private struct ProgressDay {
        let kind: FeedCardKind
        let headline: FeedHeadline?
        let meter: ProgressMeterModel?
    }

    private static func progressDay(for item: ActivityItem, standing: FeedStanding?) -> ProgressDay {
        let label = ActivityFeedPresentation.clean(item.object.label)
        let summary = ActivityFeedPresentation.clean(item.object.summary)
        let moves = pills(from: item.object.moves)

        if let label {
            if label.lowercased().hasPrefix("started") {
                return ProgressDay(kind: .start, headline: .started, meter: nil)
            }
            let amount = label.hasPrefix("+") ? label : "+" + label
            return ProgressDay(
                kind: .progress,
                headline: .progress(amount: amount, tail: standing?.whereText, pills: moves),
                meter: meter(from: standing, isStatus: false)
            )
        }
        if !moves.isEmpty {
            return ProgressDay(
                kind: .status,
                headline: .moves(pills: moves, tail: standing?.whereText),
                meter: meter(from: standing, isStatus: true)
            )
        }
        // A server from before labels: the day's own words.
        if let summary {
            if summary.lowercased().hasPrefix("started") {
                return ProgressDay(kind: .start, headline: .started, meter: nil)
            }
            return ProgressDay(
                kind: .progress,
                headline: .status(words: capitalized(summary), tail: nil),
                meter: meter(from: standing, isStatus: false)
            )
        }
        return ProgressDay(kind: .progress, headline: nil, meter: nil)
    }

    /// "Paused", "Stopped", "Picked back up", in the order sent, each once.
    private static func pills(from moves: [String]?) -> [String] {
        var seen = Set<String>()
        var words: [String] = []
        for move in moves ?? [] {
            let key = move.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            guard !key.isEmpty, seen.insert(key).inserted else { continue }
            switch key {
            case "paused": words.append("Paused")
            case "stopped": words.append("Stopped")
            case "picked_back_up": words.append("Picked back up")
            default: words.append(capitalized(key.replacingOccurrences(of: "_", with: " ")))
            }
        }
        return words
    }

    /// A status row's meter lights nothing (`from` is `to`).
    private static func meter(from standing: FeedStanding?, isStatus: Bool) -> ProgressMeterModel? {
        guard let standing else { return nil }
        let to = standing.to ?? standing.from
        return ProgressMeterModel.make(
            unit: standing.unit,
            from: isStatus ? to : standing.from,
            to: to,
            total: standing.total
        )
    }

    // MARK: Art

    private static func art(for item: ActivityItem) -> FeedCardArtSpec {
        guard let media = item.media else {
            return FeedCardArtSpec(
                posterURL: nil, backgroundURL: nil, accentHex: nil, guessInk: .dark, isSquare: false, planningPosterURLs: []
            )
        }
        // An episode row has no poster on the wire, only the still.
        let url = media.ref.isEpisode
            ? media.displayBackdropURL ?? media.imageUrl ?? media.displayPosterURL
            : media.displayPosterURL
        return FeedCardArtSpec(
            posterURL: url,
            backgroundURL: url,
            accentHex: media.posterAccentColor,
            guessInk: ArtInk.guess(accentHex: media.posterAccentColor),
            isSquare: isSquare(media),
            planningPosterURLs: []
        )
    }

    private static func isSquare(_ media: MediaSummary) -> Bool {
        media.ref.mediaType == "music" || media.posterOrientation == .square
    }

    // MARK: Planning

    private static func planningModel(for item: ActivityItem, day: PlanningDay?, dayLabel: String?) -> FeedCardModel {
        let count = item.object.count ?? day?.count
        let title = count.map { "\($0) \(planningNoun(for: item.object.mediaType, count: $0))" } ?? "Titles"
        let posters = (day?.results ?? []).compactMap(\.displayPosterURL).prefix(3)
        let names = day.flatMap { planningNames($0.results) }
        let details = FeedDetailPlanner.rows(
            hasMeter: false,
            hasHeadline: false,
            spoilers: false,
            review: nil,
            favorite: nil,
            quiet: names,
            quietLines: 2
        )

        let face = item.media == nil ? nil : art(for: item)
        let square = item.object.mediaType == "music" || face?.isSquare == true
        let facePoster = face?.posterURL
        let spec = FeedCardArtSpec(
            posterURL: posters.first ?? facePoster,
            backgroundURL: posters.first ?? facePoster,
            accentHex: face?.accentHex,
            guessInk: face?.guessInk ?? .dark,
            isSquare: square,
            planningPosterURLs: Array(posters)
        )
        return FeedCardModel(
            id: item.id,
            kind: .planning,
            username: item.actor.username,
            avatarURL: item.actor.avatarUrl,
            dayLabel: dayLabel,
            title: title,
            tag: "Planning",
            headline: nil,
            details: details,
            meter: nil,
            engagement: nil,
            art: spec,
            accessibilityLabel: accessibilityLabel(
                username: item.actor.username,
                dayLabel: dayLabel,
                title: title,
                tag: "Planning",
                headlineWords: nil,
                details: details,
                engagement: nil
            ),
            accessibilityHint: ActivityFeedPresentation.destinationHint(for: item),
            destination: ActivityDestination.resolve(item)
        )
    }

    /// Phase 5's PlanningWords table keeps its nouns private, so this is a copy.
    static func planningNoun(for mediaType: String?, count: Int) -> String {
        let plural = count != 1
        switch mediaType {
        case "movie": return plural ? "movies" : "movie"
        case "tv": return plural ? "shows" : "show"
        case "anime": return "anime"
        case "manga": return "manga"
        case "game": return plural ? "games" : "game"
        case "book": return plural ? "books" : "book"
        case "comic": return plural ? "comics" : "comic"
        case "music": return plural ? "albums" : "album"
        default: return plural ? "titles" : "title"
        }
    }

    /// "A", "A and B", "A, B and C".
    static func planningNames(_ titles: [MediaSummary]) -> String? {
        let names = titles.map(\.displayTitle).compactMap { ActivityFeedPresentation.clean($0) }
        guard let last = names.last else { return nil }
        guard names.count > 1 else { return last }
        return names.dropLast().joined(separator: ", ") + " and " + last
    }

    // MARK: VoiceOver

    private static func headlineWords(_ headline: FeedHeadline?, item: ActivityItem) -> String? {
        switch headline {
        case .marks(_, _, let rewatch, let repeatLabel):
            let summary = ActivityFeedPresentation.ratingSummary(for: item)
            return [summary.isEmpty ? nil : summary.accessibilityLabel, rewatch ? repeatLabel : nil]
                .compactMap { $0 }
                .joined(separator: ", ")
        case let .progress(amount, tail, pills):
            return ([amount] + pills + [tail.map(FeedStandingText.spoken)].compactMap { $0 }).joined(separator: ", ")
        case let .moves(pills, tail):
            return (pills + [tail.map(FeedStandingText.spoken)].compactMap { $0 }).joined(separator: ", ")
        case let .status(words, tail):
            return [words, tail].compactMap { $0 }.joined(separator: ", ")
        case .started:
            return "Started"
        case nil:
            return nil
        }
    }

    private static func accessibilityLabel(
        username: String,
        dayLabel: String?,
        title: String,
        tag: String?,
        headlineWords: String?,
        details: [FeedDetailRow],
        engagement: PostEngagement?
    ) -> String {
        let parts: [String?] = [username, dayLabel, title, tag, headlineWords]
            + details.map { $0.accessibilityText }
            + [engagement.map(PostEngagementSummary.accessibilityText(for:))]
        return parts.compactMap { part in
            guard let part, !part.isEmpty else { return nil }
            return part
        }.joined(separator: ", ")
    }

    private static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}
