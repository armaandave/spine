import SwiftUI

/// What the Trending panel's rail shows for the focused title, worked out from the viewer's state the way the media
/// page works out its own rail (`standardActionRail` and `tvActionRail`), as one pure value. No view, no repository.
///
/// The lead slot follows where the title stands in your library. "Plan" is only ever produced for a title that is not
/// in the library, which is the fix for a title you already have saying "Plan to watch".
struct TrendingRailConfig: Equatable {
    enum Lead: Equatable {
        /// Not in your library: tap to plan it.
        case plan
        /// Planning: tap to take it back out.
        case planned
        /// In progress with a step to take: a ring as far as you are; tap runs the step.
        case ring(fraction: Double)
        /// Anything else in the library: the page's "+".
        case track
    }

    let lead: Lead
    let leadLabel: String
    let eyeLabel: String
    let eyeHint: String?
    let isEyeSelected: Bool
    /// The star is always there on Home, so the pill never resizes while you swipe.
    let showsRating: Bool
    let isRatingFilled: Bool
    let offersRatingAfterBaseAction: Bool
    let isLiked: Bool
    /// Show and anime stars can't be set before anything has aired: this is the page's reason.
    let ratingUnavailableReason: String?

    /// The lead slot's symbol for the rail.
    var railLead: RailLead {
        switch lead {
        case .plan: .symbol(name: "bookmark", size: 20)
        case .planned: .symbol(name: "bookmark.fill", size: 20)
        case .track: .plus
        case let .ring(fraction): .ring(fraction: fraction, tint: .white)
        }
    }

    // MARK: Building it

    /// "In library": tracked, or any status, or a show or anime whose own state says so.
    static func isInLibrary(_ viewer: UserMediaState) -> Bool {
        viewer.isTracked || viewer.status != nil || viewer.tv?.inLibrary == true || viewer.anime?.inLibrary == true
    }

    /// An In progress title with a step to take: how far it is (0...1) and the step's words.
    struct Ring: Equatable {
        let fraction: Double
        let label: String
    }

    /// - Parameters:
    ///   - title: the title's name, for the VoiceOver words.
    ///   - viewer: the page's one tracking state (`MediaDetailViewModel.viewer`).
    ///   - ring: set when `QuickActionPlan.make` has a step for the title (see `TrendingRailRing`).
    static func make(
        title: String,
        ref: MediaRef,
        viewer: UserMediaState,
        ring: Ring? = nil,
        ratingUnavailableReason: String? = nil
    ) -> TrendingRailConfig {
        let isFamily = ref.isTVFamily || (ref.isAnime && viewer.anime != nil)
        let lead = lead(for: viewer, ringFraction: ring?.fraction)
        let isEyeSelected = isFamily ? isTVEyeFilled(viewer, ref: ref) : isCompleted(viewer)

        let eyeLabel: String
        let eyeHint: String?
        if isFamily {
            eyeLabel = tvEyeLabel(ref: ref, filled: isEyeSelected)
            eyeHint = isEyeSelected ? "Undoes marking these episodes watched" : "Marks every aired episode watched, without a date"
        } else if ref.isSingleWeight {
            eyeLabel = ref.mediaType == "music"
                ? (isEyeSelected ? "Listened" : "Mark as listened")
                : (isEyeSelected ? "Watched" : "Mark as watched")
            eyeHint = nil
        } else if ref.readsLikeBook || ref.mediaType == "game" {
            eyeLabel = bookGameCopy(for: ref.mediaType).finished
            eyeHint = nil
        } else {
            eyeLabel = "Mark as watched"
            eyeHint = nil
        }

        let offersRating: Bool
        if isFamily {
            offersRating = false
        } else {
            offersRating = ref.isSingleWeight
                || (ref.readsLikeBook && viewer.book?.hasLiveJourney != true)
                || (ref.mediaType == "game" && viewer.game?.hasLivePlaythrough != true)
        }

        return TrendingRailConfig(
            lead: lead,
            leadLabel: leadLabel(lead, ref: ref, title: title, ringLabel: ring?.label, isFamily: isFamily),
            eyeLabel: eyeLabel,
            eyeHint: eyeHint,
            isEyeSelected: isEyeSelected,
            showsRating: true,
            isRatingFilled: (viewer.rating?.ratingHalfSteps ?? 0) > 0,
            offersRatingAfterBaseAction: offersRating,
            isLiked: viewer.hasLiked,
            ratingUnavailableReason: isFamily ? ratingUnavailableReason : nil
        )
    }

    static func lead(for viewer: UserMediaState, ringFraction: Double?) -> Lead {
        guard isInLibrary(viewer) else { return .plan }
        let status = viewer.tv?.status ?? viewer.anime?.status ?? viewer.status
        if status?.caseInsensitiveCompare("Planning") == .orderedSame { return .planned }
        if status?.caseInsensitiveCompare("In progress") == .orderedSame, let ringFraction {
            return .ring(fraction: min(max(ringFraction, 0), 1))
        }
        return .track
    }

    // MARK: Words

    private static func leadLabel(_ lead: Lead, ref: MediaRef, title: String, ringLabel: String?, isFamily: Bool) -> String {
        switch lead {
        case .plan: return "Plan \(title)"
        case .planned: return "Planned. Remove from Planning"
        case .ring: return ringLabel ?? "Update \(title)"
        case .track:
            if isFamily { return ref.isAnime ? "Track anime" : "Track show" }
            return ref.readsLikeBook || ref.mediaType == "game" ? "Track" : "Log"
        }
    }

    private static func tvEyeLabel(ref: MediaRef, filled: Bool) -> String {
        switch ref.mediaType {
        case "anime": filled ? "Anime watched" : "Mark anime watched"
        case "season": filled ? "Season watched" : "Mark season watched"
        default: filled ? "Show watched" : "Mark show watched"
        }
    }

    // MARK: Eye state

    private static func isTVEyeFilled(_ viewer: UserMediaState, ref: MediaRef) -> Bool {
        if ref.isAnime { return viewer.tvPending.markedShow || viewer.anime?.eye?.filled == true }
        guard let tv = viewer.tv else { return false }
        return viewer.tvPending.markedShow || tv.eye?.filled == true
    }

    private static func isCompleted(_ viewer: UserMediaState) -> Bool {
        viewer.status?.caseInsensitiveCompare("Completed") == .orderedSame
    }
}

/// How far an In progress title is, for the ring, and whether it has a step to take (the ring's tap runs that step).
enum TrendingRailRing {
    /// The ring for a title with a quick-action plan (fraction 0 when its end is unknown); nil without a plan.
    static func make(media: MediaSummary, viewer: UserMediaState) -> TrendingRailConfig.Ring? {
        let item = LibraryItem(media: media, tracking: trackingState(from: viewer))
        guard let plan = QuickActionPlan.make(for: item) else { return nil }
        let label: String
        if case .openSheet = plan { label = "Update \(media.displayTitle)" } else { label = plan.label }
        guard let position = InProgressTilePresenter.position(for: item), let total = position.total, total > 0 else {
            return TrendingRailConfig.Ring(fraction: 0, label: label)
        }
        return TrendingRailConfig.Ring(fraction: min(max(position.current / total, 0), 1), label: label)
    }

    /// The page-style tracking row for a title, built from what the page already holds.
    static func trackingState(from viewer: UserMediaState) -> TrackingState {
        TrackingState(
            trackingId: viewer.trackingId ?? 0,
            status: viewer.status,
            rating: viewer.rating,
            progress: viewer.progress,
            repeats: nil,
            startDate: viewer.startDate,
            endDate: nil,
            notes: nil,
            updatedAt: nil,
            liked: viewer.hasLiked,
            diaryCount: viewer.diaryCount,
            book: viewer.book,
            game: viewer.game,
            tv: viewer.tv,
            anime: viewer.anime
        )
    }
}
