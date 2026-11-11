import Nuke
import SwiftUI

/// The page a log and a day's progress share, so the two can only differ in their middle: the poster-tinted background,
/// Back (with Edit and Delete for the owner), the hero, the date and how long ago under the middle, the comments under a
/// hairline, the comment bar and the edge swipe back. Each page brings its own hero (`PostHero`) and middle, and its own
/// sheets and dialogs.
struct PostPage<Item, Hero: View, Middle: View>: View {
    @Environment(\.dismiss) private var dismiss
    @State private var edgeDragOffset: CGFloat = 0
    @State private var topSafeAreaInset: CGFloat = 0
    @FocusState private var isCommentFocused: Bool

    private let item: Item?
    private let isLoading: Bool
    private let errorTitle: String
    private let errorMessage: String?
    private let artURL: URL?
    private let accentHex: String?
    private let engagement: PostEngagementModel
    private let date: String?
    private let dateNote: String?
    private let hidesComments: Bool
    private let showsEdit: Bool
    private let showsDelete: Bool
    private let isBusy: Bool
    private let onEdit: () -> Void
    private let onDelete: () -> Void
    private let hero: (Item) -> Hero
    private let middle: (Item) -> Middle

    init(
        item: Item?,
        isLoading: Bool,
        errorTitle: String,
        errorMessage: String?,
        artURL: URL?,
        accentHex: String?,
        engagement: PostEngagementModel,
        date: String?,
        dateNote: String? = nil,
        hidesComments: Bool = false,
        showsEdit: Bool,
        showsDelete: Bool,
        isBusy: Bool,
        onEdit: @escaping () -> Void,
        onDelete: @escaping () -> Void,
        @ViewBuilder hero: @escaping (Item) -> Hero,
        @ViewBuilder middle: @escaping (Item) -> Middle
    ) {
        self.item = item
        self.isLoading = isLoading
        self.errorTitle = errorTitle
        self.errorMessage = errorMessage
        self.artURL = artURL
        self.accentHex = accentHex
        self.engagement = engagement
        self.date = date
        self.dateNote = dateNote
        self.hidesComments = hidesComments
        self.showsEdit = showsEdit
        self.showsDelete = showsDelete
        self.isBusy = isBusy
        self.onEdit = onEdit
        self.onDelete = onDelete
        self.hero = hero
        self.middle = middle
    }

    var body: some View {
        ZStack(alignment: .top) {
            PostPageBackground(url: artURL, accentHex: accentHex)

            ScrollViewReader { proxy in
                ScrollView(showsIndicators: false) {
                    Group {
                        if isLoading, item == nil {
                            ProgressView()
                                .tint(.white)
                                .frame(maxWidth: .infinity, minHeight: 520)
                        } else if let item {
                            content(item)
                        } else if let errorMessage {
                            ContentUnavailableView(errorTitle, systemImage: "exclamationmark.triangle", description: Text(errorMessage))
                                .foregroundStyle(.white)
                                .padding()
                        }
                    }
                    .spineContentTransition(value: contentPhase)
                    .padding(.bottom, 24)
                }
                .scrollContentBackground(.hidden)
                .scrollDismissesKeyboard(.immediately)
                .scrollEdgeEffectStyle(.hard, for: .bottom)
                .ignoresSafeArea(edges: .top)
                .onChange(of: engagement.sentCount) {
                    PostCommentsSection.scrollToSent(engagement, with: proxy)
                }
            }

            topButtons
        }
        // Inside the bar's inset, so the swipe's edge doesn't cover the heart, and it ends where a log's favorite play
        // circle starts: 16 of page inset and 8 of button padding.
        .overlay(alignment: .leading) {
            Color.clear
                .frame(width: 24)
                .contentShape(Rectangle())
                .gesture(edgeSwipeBackGesture)
        }
        .safeAreaBar(edge: .bottom) {
            if item != nil {
                PostCommentBar(model: engagement, isFocused: $isCommentFocused)
            }
        }
        .onGeometryChange(for: CGFloat.self) { $0.safeAreaInsets.top } action: { topSafeAreaInset = $0 }
        .navigationBarBackButtonHidden()
        // The buttons are drawn by the page, so no navigation bar adds an inset of its own.
        .toolbar(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .dismissExplorationOnReturnHome()
        .offset(x: edgeDragOffset)
    }

    private var contentPhase: SpineContentPhase {
        .resolve(isLoading: isLoading, hasContent: item != nil, hasError: errorMessage != nil)
    }

    /// The hero, then the page's own middle, the date, the hairline and the comments.
    private func content(_ item: Item) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            hero(item)

            VStack(alignment: .leading, spacing: 16) {
                middle(item)

                PostDateLines(date: date, note: dateNote)

                PostHairline()
                    .padding(.top, 8)

                PostCommentsSection(model: engagement, hidesComments: hidesComments)

                Color.clear
                    .frame(height: 1)
                    .id("post-end")
            }
        }
        .padding(.horizontal, 16)
        // The back button ends 50 pt below the safe area; the artwork starts 18 pt under it, whatever its shape.
        .padding(.top, topSafeAreaInset + 68)
    }

    /// Back, and Edit and Delete when the page offers them: one row at one inset.
    private var topButtons: some View {
        GlassEffectContainer(spacing: 8) {
            HStack(spacing: 8) {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 17, weight: .bold))
                        .frame(width: 38, height: 38)
                }
                .buttonStyle(.plain)
                .contentShape(.circle)
                .glassEffect(.regular.interactive(), in: .circle)
                .accessibilityLabel("Back")

                Spacer()

                if showsEdit {
                    Button(action: onEdit) {
                        Text("Edit")
                            .font(.system(size: 14, weight: .heavy))
                            .padding(.horizontal, 13)
                            .frame(height: 38)
                    }
                    .buttonStyle(.plain)
                    .contentShape(.capsule)
                    .glassEffect(.regular.interactive(), in: .capsule)
                    .disabled(isBusy)
                }

                if showsDelete {
                    Button(role: .destructive, action: onDelete) {
                        Image(systemName: "trash")
                            .font(.system(size: 16, weight: .bold))
                            .frame(width: 38, height: 38)
                    }
                    .buttonStyle(.plain)
                    .contentShape(.circle)
                    .glassEffect(.regular.interactive(), in: .circle)
                    .accessibilityLabel("Delete")
                    .disabled(isBusy)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
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
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        edgeDragOffset = 0
                    }
                }
            }
    }
}

/// The top of a post page: the poster, and beside it, down to the poster's bottom edge, who posted it, the title (its
/// logo when the title has one) and one muted line under it. Tapping it opens the title.
///
/// The words beside the poster wait until the title is known to be a logo or text, then appear together, so a title
/// never shows as text and then turns into its logo. A logo is downloaded before it shows (one already in the image
/// cache shows at once); if that takes too long the title stays text.
struct PostHero: View {
    let posterURL: String?
    let posterTitle: String
    let mediaType: String
    let orientation: PosterOrientation?
    let user: UserSummary
    let title: String
    let detail: MediaDetail?
    /// The year and runtime under the title ("2025 - 2h 18m"), or the episode tag under a day's ("S2 E7").
    let subtitle: String?
    /// The title's details are still on the way, so whether it has a logo isn't known yet.
    let isLoadingDetail: Bool
    let onOpen: () -> Void

    /// How long the words wait for the details and the logo before showing the title as text.
    static let titleWait: Duration = .seconds(1.5)

    enum TitleArt: Equatable {
        case pending
        case logo
        case text
    }

    @State private var titleArt: TitleArt

    init(
        posterURL: String?,
        posterTitle: String,
        mediaType: String,
        orientation: PosterOrientation?,
        user: UserSummary,
        title: String,
        detail: MediaDetail?,
        subtitle: String?,
        isLoadingDetail: Bool = false,
        onOpen: @escaping () -> Void
    ) {
        self.posterURL = posterURL
        self.posterTitle = posterTitle
        self.mediaType = mediaType
        self.orientation = orientation
        self.user = user
        self.title = title
        self.detail = detail
        self.subtitle = subtitle
        self.isLoadingDetail = isLoadingDetail
        self.onOpen = onOpen
        _titleArt = State(initialValue: Self.titleArt(
            isLoadingDetail: isLoadingDetail,
            logoRequest: Self.logoRequest(for: detail),
            isLogoCached: { ImagePipeline.shared.cache.cachedImage(for: $0) != nil }
        ))
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 14) {
            PostHeroPoster(url: posterURL, title: posterTitle, mediaType: mediaType, orientation: orientation)

            VStack(alignment: .leading, spacing: 6) {
                PostHeroAuthorLine(user: user)
                    .padding(.bottom, 2)

                if titleArt == .logo, let detail {
                    MediaTitleDisplay(
                        detail: detail,
                        title: title,
                        showsLogo: .constant(true),
                        font: PostPageStyle.titleFont,
                        lineLimit: 4,
                        minimumScaleFactor: 0.72,
                        maxLogoHeight: 48,
                        alignment: .leading,
                        onTap: onOpen,
                        onLongPress: nil
                    )
                } else {
                    PostHeroTitle(text: title)
                }

                if let subtitle {
                    Text(subtitle)
                        .font(PostPageStyle.subtitleFont)
                        .foregroundStyle(.white.opacity(0.78))
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 2)
            .opacity(titleArt == .pending ? 0 : 1)
            .animation(.easeOut(duration: 0.18), value: titleArt)
        }
        .contentShape(Rectangle())
        .onTapGesture(perform: onOpen)
        .accessibilityLabel("View \(title)")
        .accessibilityAddTraits(.isButton)
        .task(id: Self.logoRequest(for: detail)?.url) { await settleTitleArt() }
        .task(id: isLoadingDetail) { await settleTitleArt() }
        .task {
            try? await Task.sleep(for: Self.titleWait)
            if titleArt == .pending { titleArt = .text }
        }
    }

    /// Settles a pending title once the details are in: its logo, downloaded first, or its text.
    private func settleTitleArt() async {
        guard titleArt == .pending, !isLoadingDetail else { return }
        guard let request = Self.logoRequest(for: detail) else {
            titleArt = .text
            return
        }
        do {
            _ = try await ImagePipeline.shared.image(for: request)
            if titleArt == .pending { titleArt = .logo }
        } catch {
            if !Task.isCancelled, titleArt == .pending { titleArt = .text }
        }
    }

    /// The request the title's logo loads with: the same one `MediaTitleDisplay` asks for, so a logo downloaded here is
    /// the one it finds in the cache.
    static func logoRequest(for detail: MediaDetail?) -> ImageRequest? {
        guard let detail, supportsTitleLogo(detail), let logo = detail.displayLogoURL, let url = URL(string: logo) else {
            return nil
        }
        return SpineImagePipeline.request(url: url, thumbnailSize: nil)
    }

    /// What the title starts as: text when the details are in and have no logo, the logo when it's already cached, and
    /// pending (the words hidden) while the details or the logo are still coming.
    static func titleArt(isLoadingDetail: Bool, logoRequest: ImageRequest?, isLogoCached: (ImageRequest) -> Bool) -> TitleArt {
        if isLoadingDetail { return .pending }
        guard let logoRequest else { return .text }
        return isLogoCached(logoRequest) ? .logo : .pending
    }

    /// The line under the title: the year (from a "(2000)" at the end of the title, or the release date) and the
    /// runtime, each said once. Nil when there is neither.
    static func metadata(displayTitle: String, detail: MediaDetail?) -> String? {
        var seen = Set<String>()
        let parts = [titleParts(displayTitle).year, DiaryLogFormat.year(detail?.releaseDate), runtime(detail)]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
        return parts.isEmpty ? nil : parts.joined(separator: " - ")
    }

    /// A title without the "(2000)" at its end, and that year.
    static func titleParts(_ title: String) -> (title: String, year: String?) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasSuffix(")") else { return (trimmed, nil) }
        let suffix = String(trimmed.suffix(6))
        guard suffix.first == "(", suffix.last == ")" else { return (trimmed, nil) }
        let year = String(suffix.dropFirst().dropLast())
        guard year.count == 4, year.allSatisfy(\.isNumber) else { return (trimmed, nil) }
        return (String(trimmed.dropLast(7)), year)
    }

    private static func runtime(_ detail: MediaDetail?) -> String? {
        switch detail?.details?["runtime"] {
        case let .string(string):
            return string
        case let .number(number):
            return number.truncatingRemainder(dividingBy: 1) == 0 ? "\(Int(number))" : "\(number)"
        default:
            return nil
        }
    }
}

/// Under a post's own part: a calendar, the day it was for ("Mar 14, 2023") and, from the next day on, how long ago that
/// was ("3 years, 7 months and 27 days ago") under the date. A game log's "At completion" line comes first.
struct PostDateLines: View {
    /// The day as the server sends it ("2023-03-14").
    let date: String?
    var note: String?

    var body: some View {
        let day = DiaryLogFormat.dateLabel(date)
        if note != nil || day != nil {
            VStack(alignment: .leading, spacing: 2) {
                if let note {
                    Text(note)
                        .font(PostPageStyle.loggedFont)
                        .foregroundStyle(.white.opacity(0.72))
                }
                if let day {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "calendar")
                            .font(.system(size: 12, weight: .semibold))
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(day)
                                .font(PostPageStyle.loggedFont)
                            if let age = DiaryLogFormat.ageLabel(date) {
                                Text(age)
                                    .font(PostPageStyle.timeFont)
                                    .foregroundStyle(.white.opacity(0.5))
                            }
                        }
                    }
                    .foregroundStyle(.white.opacity(0.72))
                }
            }
            .accessibilityElement(children: .combine)
        }
    }
}
