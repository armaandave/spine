import Nuke
import SwiftUI
import UIKit

/// What a log's share picture says, read from the log and its title's details. The design is the share canvas's
/// "Log cards" page: spoiler words are never drawn, not even blurred, and a spoiler log shows no favorite.
struct LogShareContent {
    enum ArtShape {
        case poster
        /// A book, manga or comic: a cover with a spine edge on its left.
        case book
        /// An album.
        case square
    }

    let title: String
    /// "Season 2 · 2025", or the year.
    let subtitle: String?
    let username: String
    /// "Nov 20" this year, "Mar 21, 2025" before it.
    let date: String?
    /// Stars in halves: 4.5 draws four and a half; 0 draws none.
    let stars: Double
    let liked: Bool
    let isRepeat: Bool
    let repeatLabel: String
    /// The review on one line, nil when there is none or it holds spoilers.
    let words: String?
    /// The log has a review marked as spoilers: the picture says so instead of drawing it.
    let hidesSpoilers: Bool
    let favorite: LogFavorite?
    let shape: ArtShape
    let posterURL: URL?
    let avatarURL: URL?

    init(entry: DiaryEntry, detail: MediaDetail?, now: Date = Date()) {
        let media = entry.media
        let parts = PostHero.titleParts(media.displayTitle)
        let year = parts.year ?? DiaryLogFormat.year(detail?.releaseDate)
        let show = Self.clean(media.showTitle) ?? parts.title
        switch media.ref.mediaType {
        case "season":
            title = show
            subtitle = Self.joined([media.ref.seasonNumber.map { "Season \($0)" }, year])
        case "episode":
            title = show
            let code = media.ref.seasonNumber.flatMap { season in
                media.ref.episodeNumber.map { "S\(season) E\($0)" }
            }
            subtitle = Self.joined([code, year])
        default:
            title = parts.title
            subtitle = year
        }

        username = entry.user.username
        date = Self.dateLabel(entry.consumedAt, now: now)
        stars = entry.rating.map { Double($0.ratingHalfSteps) / 2 } ?? 0
        liked = entry.liked
        isRepeat = entry.isRewatch || entry.isTrueReread == true
        repeatLabel = media.ref.repeatLabel

        let review = Self.clean(entry.review)
        hidesSpoilers = entry.containsSpoilers && review != nil
        words = hidesSpoilers ? nil : review.map { Self.oneLine($0) }
        favorite = entry.containsSpoilers ? nil : entry.favorite

        switch media.ref.mediaType {
        case "music": shape = .square
        case "book", "manga", "comic": shape = .book
        default: shape = .poster
        }
        posterURL = URL(string: detail?.displayPosterURL ?? media.displayPosterURL ?? "")
        avatarURL = URL(string: entry.user.avatarUrl ?? "")
    }

    var hasMarks: Bool { stars > 0 || liked || isRepeat }

    /// A review over 280 characters gets the Story's reading layout: small art beside the title, the words below.
    var isLong: Bool { (words?.count ?? 0) > 280 }

    /// The title's length picks its size, so a long one still fits two lines.
    func titleSize(short: CGFloat, medium: CGFloat, long: CGFloat) -> CGFloat {
        title.count <= 16 ? short : title.count <= 26 ? medium : long
    }

    private static func clean(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    private static func joined(_ parts: [String?]) -> String? {
        let present = parts.compactMap { $0 }
        return present.isEmpty ? nil : present.joined(separator: " · ")
    }

    /// Paragraphs run together: a picture has no room for blank lines.
    private static func oneLine(_ text: String) -> String {
        text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    static func dateLabel(_ raw: String?, now: Date) -> String? {
        guard let raw, raw.count >= 10, let day = dayFormatter.date(from: String(raw.prefix(10))) else { return nil }
        let calendar = Calendar(identifier: .gregorian)
        let sameYear = calendar.component(.year, from: day) == calendar.component(.year, from: now)
        let style = Date.FormatStyle.dateTime.month(.abbreviated).day()
        return day.formatted(sameYear ? style : style.year())
    }
}

/// Words cut to fit a number of lines, always after a whole word.
enum ShareTextFit {
    struct Result {
        let text: String
        let isCut: Bool
    }

    static func fit(_ text: String, font: UIFont, lineSpacing: CGFloat, width: CGFloat, lines: Int) -> Result {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = lineSpacing
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .paragraphStyle: style]
        // Halfway between `lines` and one more, so the measure's rounding can't tip it either way.
        let limit = (CGFloat(lines) + 0.5) * (font.lineHeight + lineSpacing)
        func fits(_ candidate: String) -> Bool {
            let size = (candidate as NSString).boundingRect(
                with: CGSize(width: width, height: .greatestFiniteMagnitude),
                options: [.usesLineFragmentOrigin, .usesFontLeading],
                attributes: attributes,
                context: nil
            )
            return size.height <= limit
        }
        if fits(text) { return Result(text: text, isCut: false) }

        let words = text.split(separator: " ").map(String.init)
        func cut(_ count: Int) -> String {
            let head = words.prefix(count).joined(separator: " ")
            return head.trimmingCharacters(in: CharacterSet(charactersIn: ",;:–—-")) + "…"
        }
        var low = 1
        var high = max(words.count - 1, 1)
        while low < high {
            let middle = (low + high + 1) / 2
            if fits(cut(middle)) { low = middle } else { high = middle - 1 }
        }
        return Result(text: cut(low), isCut: true)
    }
}

/// The pictures the share cards are drawn with, loaded before drawing: an image that hasn't loaded can't be drawn.
struct LogShareArt {
    var poster: UIImage?
    var avatar: UIImage?
    /// How much the blurred backdrop is darkened: a light cover more than a dark one, so white words stay readable.
    var dim: Double = 0.55

    static func load(for content: LogShareContent) async -> LogShareArt {
        async let poster = image(content.posterURL)
        async let avatar = image(content.avatarURL)
        var art = await LogShareArt(poster: poster, avatar: avatar)
        if let poster = art.poster {
            art.dim = min(max(0.3 / max(averageLuminance(poster), 0.01), 0.42), 0.65)
        }
        return art
    }

    private static func image(_ url: URL?) async -> UIImage? {
        guard let request = SpineImagePipeline.request(url: url, thumbnailSize: nil) else { return nil }
        return try? await ImagePipeline.shared.image(for: request)
    }

    /// The poster's average brightness, 0 to 1, from an 8x8 copy of it.
    private static func averageLuminance(_ image: UIImage) -> Double {
        guard let cgImage = image.cgImage else { return 0.5 }
        let side = 8
        var pixels = [UInt8](repeating: 0, count: side * side * 4)
        let drawn: Bool = pixels.withUnsafeMutableBytes { buffer in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: side,
                height: side,
                bitsPerComponent: 8,
                bytesPerRow: side * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.interpolationQuality = .medium
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: side, height: side))
            return true
        }
        guard drawn else { return 0.5 }
        var total = 0.0
        for index in stride(from: 0, to: pixels.count, by: 4) {
            total += 0.2126 * Double(pixels[index]) + 0.7152 * Double(pixels[index + 1]) + 0.0722 * Double(pixels[index + 2])
        }
        return total / Double(side * side) / 255
    }
}

// MARK: - Pieces both cards draw

/// The poster blurred behind everything, darkened by `dim` and spread past the edges so its blur has no rim.
struct LogShareBackdrop: View {
    let image: UIImage?
    let dim: Double
    let blur: CGFloat
    let size: CGSize

    var body: some View {
        ZStack {
            Color(red: 0.04, green: 0.04, blue: 0.045)
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: size.width + 120, height: size.height + 120)
                    .clipped()
                    .blur(radius: blur, opaque: true)
                    .saturation(1.6)
                    .colorMultiply(Color(white: dim))
            }
        }
        .frame(width: size.width, height: size.height)
        .clipped()
    }
}

/// The poster, cover or album art with its corners, a book's spine edge, a hairline and a shadow.
struct LogShareArtView: View {
    let image: UIImage?
    let shape: LogShareContent.ArtShape
    let aspectRatio: CGFloat

    private var corners: UnevenRoundedRectangle {
        switch shape {
        case .book:
            UnevenRoundedRectangle(topLeadingRadius: 3, bottomLeadingRadius: 3, bottomTrailingRadius: 6, topTrailingRadius: 6)
        case .square:
            UnevenRoundedRectangle(cornerRadii: .init(topLeading: 6, bottomLeading: 6, bottomTrailing: 6, topTrailing: 6))
        case .poster:
            UnevenRoundedRectangle(cornerRadii: .init(topLeading: 8, bottomLeading: 8, bottomTrailing: 8, topTrailing: 8))
        }
    }

    var body: some View {
        Color.clear
            .aspectRatio(aspectRatio, contentMode: .fit)
            .overlay {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                } else {
                    Color.white.opacity(0.08)
                }
            }
            .overlay {
                if shape == .book {
                    GeometryReader { proxy in
                        let width = max(proxy.size.width, 1)
                        LinearGradient(
                            stops: [
                                .init(color: .black.opacity(0.32), location: 0),
                                .init(color: .white.opacity(0.22), location: 2.5 / width),
                                .init(color: .white.opacity(0.05), location: 7 / width),
                                .init(color: .black.opacity(0.12), location: 9 / width),
                                .init(color: .clear, location: 15 / width),
                            ],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    }
                }
            }
            .clipShape(corners)
            .overlay { corners.stroke(.white.opacity(0.16), lineWidth: 0.5) }
            .shadow(color: .black.opacity(0.35), radius: 5, y: 3)
            .shadow(color: .black.opacity(0.55), radius: 22, y: 18)
    }

    /// The art's own shape, kept near a poster's or a cover's so an odd image can't stretch the card.
    static func aspectRatio(of image: UIImage?, shape: LogShareContent.ArtShape) -> CGFloat {
        if shape == .square { return 1 }
        guard let image, image.size.height > 0 else { return 2.0 / 3.0 }
        return min(max(image.size.width / image.size.height, 0.6), 0.8)
    }
}

/// White stars with half stars cut in half, then the heart and the green arrow, each with its soft glow.
struct LogShareMarks: View {
    let content: LogShareContent
    let starSize: CGFloat
    let iconSize: CGFloat
    var starSpacing: CGFloat = 3
    var spacing: CGFloat = 10

    private static let heart = Color(red: 1, green: 0.42, blue: 0.54)
    private static let repeatGreen = Color(red: 0.29, green: 0.87, blue: 0.5)

    var body: some View {
        HStack(spacing: spacing) {
            if content.stars > 0 {
                HStack(spacing: starSpacing) {
                    ForEach(0..<Int(content.stars), id: \.self) { _ in star }
                    if content.stars - Double(Int(content.stars)) >= 0.5 {
                        star
                            .frame(width: starSize / 2, alignment: .leading)
                            .clipped()
                    }
                }
                .shadow(color: .white.opacity(0.5), radius: 2.5)
                .shadow(color: .white.opacity(0.26), radius: 7)
            }
            if content.liked {
                Image(systemName: "heart.fill")
                    .font(.system(size: iconSize * 0.86))
                    .foregroundStyle(Self.heart)
                    .shadow(color: Self.heart.opacity(0.55), radius: 2.5)
                    .shadow(color: Self.heart.opacity(0.3), radius: 7)
            }
            if content.isRepeat {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: iconSize * 0.8, weight: .bold))
                    .foregroundStyle(Self.repeatGreen)
                    .shadow(color: Self.repeatGreen.opacity(0.55), radius: 2.5)
                    .shadow(color: Self.repeatGreen.opacity(0.3), radius: 7)
            }
        }
    }

    private var star: some View {
        Image(systemName: "star.fill")
            .font(.system(size: starSize * 0.86))
            .foregroundStyle(.white)
            .frame(width: starSize, height: starSize)
    }
}

/// The person's photo, round, with a hairline.
struct LogShareAvatar: View {
    let image: UIImage?
    let size: CGFloat

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.fill")
                    .font(.system(size: size * 0.45, weight: .bold))
                    .foregroundStyle(.white.opacity(0.6))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.white.opacity(0.12))
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay { Circle().strokeBorder(.white.opacity(0.35), lineWidth: 0.5) }
    }
}

/// The Spine icon and name.
struct LogShareSpineMark: View {
    let iconSize: CGFloat
    let fontSize: CGFloat

    var body: some View {
        HStack(spacing: 8) {
            Image("ShareSpineIcon")
                .resizable()
                .frame(width: iconSize, height: iconSize)
                .clipShape(.rect(cornerRadius: iconSize * 0.27, style: .continuous))
            Text("Spine")
                .font(.system(size: fontSize, weight: .semibold))
                .foregroundStyle(.white.opacity(0.85))
        }
    }
}

/// "Contains spoilers" with the red crossed-out eye.
struct LogShareSpoilerLine: View {
    let fontSize: CGFloat

    private static let red = Color(red: 0.79, green: 0.32, blue: 0.29)

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "eye.slash")
                .font(.system(size: fontSize * 1.05, weight: .medium))
                .foregroundStyle(Self.red)
                .shadow(color: Self.red.opacity(0.5), radius: 4)
            Text("Contains spoilers")
                .font(.system(size: fontSize))
                .foregroundStyle(.white.opacity(0.86))
        }
    }
}

// MARK: - The Story picture

/// The tall picture Share Image makes, 1080 x 1920 once drawn: the art, the title and its line, the marks, up to four
/// lines of the review (then "Read the rest on Spine"), a favorite, who logged it and when, and the Spine mark. A
/// review over 280 characters switches to a reading layout with small art beside the title.
struct LogShareStory: View {
    static let size = CGSize(width: 360, height: 640)

    let content: LogShareContent
    let art: LogShareArt

    var body: some View {
        ZStack {
            LogShareBackdrop(image: art.poster, dim: art.dim, blur: 44, size: Self.size)
            LinearGradient(
                stops: [
                    .init(color: .black.opacity(0.14), location: 0),
                    .init(color: .black.opacity(0.22), location: 0.45),
                    .init(color: .black.opacity(0.62), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )

            Group {
                if content.isLong {
                    readingLayout
                } else {
                    centeredLayout
                }
            }
            .padding(.top, 64)
            .padding(.bottom, 88)
            .padding(.horizontal, 30)
            .frame(width: Self.size.width, height: Self.size.height)

            LogShareSpineMark(iconSize: 22, fontSize: 15)
                .padding(.bottom, 40)
                .frame(maxHeight: .infinity, alignment: .bottom)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .clipped()
        .foregroundStyle(.white)
        .environment(\.colorScheme, .dark)
    }

    private var aspectRatio: CGFloat {
        LogShareArtView.aspectRatio(of: art.poster, shape: content.shape)
    }

    // MARK: Most logs

    private var centeredLayout: some View {
        VStack(spacing: 0) {
            // The art gives up height first, so nothing below it is ever squeezed.
            LogShareArtView(image: art.poster, shape: content.shape, aspectRatio: aspectRatio)
                .frame(maxWidth: 300, maxHeight: content.shape == .square ? 236 : 238)
                .layoutPriority(-1)

            Text(content.title)
                .font(.system(size: content.titleSize(short: 30, medium: 26, long: 23), weight: .bold))
                .tracking(-0.4)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .padding(.top, 20)

            if let subtitle = content.subtitle {
                Text(subtitle)
                    .font(.system(size: 14))
                    .foregroundStyle(.white.opacity(0.66))
                    .padding(.top, 4)
            }

            if content.hasMarks {
                LogShareMarks(content: content, starSize: 24, iconSize: 22)
                    .padding(.top, 12)
            }

            if let words = content.words {
                let fitted = Self.quote(words)
                Text(fitted.text)
                    .font(.system(size: 15.5))
                    .lineSpacing(Self.quoteSpacing)
                    .foregroundStyle(.white.opacity(0.9))
                    .multilineTextAlignment(.center)
                    .lineLimit(4)
                    .frame(width: 300)
                    .padding(.top, 12)
                if fitted.isCut {
                    readTheRest
                        .padding(.top, 6)
                }
            }

            if content.hidesSpoilers {
                VStack(spacing: 4) {
                    LogShareSpoilerLine(fontSize: 15.5)
                    Text("Read it on Spine")
                        .font(.system(size: 13))
                        .italic()
                        .foregroundStyle(.white.opacity(0.6))
                }
                .padding(.top, 14)
            }

            if let favorite = content.favorite {
                favoriteBar(favorite)
                    .padding(.top, 14)
            }

            byline(avatarSize: 24)
                .padding(.top, 16)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private static let quoteFont = UIFont.systemFont(ofSize: 15.5)
    private static let quoteSpacing: CGFloat = 4

    /// The review in quotes, cut after a whole word when four lines can't hold it.
    private static func quote(_ words: String) -> ShareTextFit.Result {
        let fitted = ShareTextFit.fit("“\(words)”", font: quoteFont, lineSpacing: quoteSpacing, width: 300, lines: 4)
        guard fitted.isCut else { return fitted }
        return ShareTextFit.Result(text: fitted.text + "”", isCut: true)
    }

    // MARK: A long review

    private var readingLayout: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 16) {
                let artHeight: CGFloat = content.shape == .square ? 112 : 132
                LogShareArtView(image: art.poster, shape: content.shape, aspectRatio: aspectRatio)
                    .frame(width: artHeight * aspectRatio, height: artHeight)
                VStack(alignment: .leading, spacing: 0) {
                    Text(content.title)
                        .font(.system(size: content.titleSize(short: 26, medium: 22, long: 19), weight: .bold))
                        .tracking(-0.3)
                        .lineLimit(3)
                    if let subtitle = content.subtitle {
                        Text(subtitle)
                            .font(.system(size: 13))
                            .foregroundStyle(.white.opacity(0.66))
                            .padding(.top, 4)
                    }
                    if content.hasMarks {
                        LogShareMarks(content: content, starSize: 19, iconSize: 17, starSpacing: 2, spacing: 8)
                            .padding(.top, 10)
                    }
                }
            }

            if let words = content.words {
                let fitted = ShareTextFit.fit(
                    words,
                    font: UIFont.systemFont(ofSize: 15),
                    lineSpacing: Self.readingSpacing,
                    width: 300,
                    lines: 10
                )
                Text(fitted.text)
                    .font(.system(size: 15))
                    .lineSpacing(Self.readingSpacing)
                    .foregroundStyle(.white.opacity(0.9))
                    .lineLimit(10)
                    .frame(width: 300, alignment: .leading)
                    .padding(.top, 24)
                if fitted.isCut {
                    readTheRest
                        .padding(.top, 10)
                }
            }

            byline(avatarSize: 24)
                .padding(.top, 22)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private static let readingSpacing: CGFloat = 4.6

    // MARK: Shared parts

    private var readTheRest: some View {
        Text("Read the rest on Spine")
            .font(.system(size: 14, weight: .semibold))
            .italic()
            .foregroundStyle(.white.opacity(0.64))
    }

    private func byline(avatarSize: CGFloat) -> some View {
        HStack(spacing: 8) {
            LogShareAvatar(image: art.avatar, size: avatarSize)
            Text(content.username)
                .font(.system(size: 15, weight: .semibold))
            if let date = content.date {
                Text(date)
                    .font(.system(size: 15))
                    .foregroundStyle(.white.opacity(0.62))
            }
        }
    }

    /// The log page's favorite bar, without its play button: "Favorite song" over a rounded bar.
    private func favoriteBar(_ favorite: LogFavorite) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(favorite.kind.label)
                .font(.system(size: 12.5, weight: .semibold))
                .foregroundStyle(.white.opacity(0.56))
            HStack(spacing: 8) {
                if let number = favorite.numberLabel {
                    Text(number)
                        .foregroundStyle(.white.opacity(0.6))
                        .monospacedDigit()
                }
                Text(favorite.title)
                    .lineLimit(1)
            }
            .font(.system(size: 14.5, weight: .medium))
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
            .background(
                LinearGradient(colors: [.white.opacity(0.14), .white.opacity(0.07)], startPoint: .top, endPoint: .bottom),
                in: .rect(cornerRadius: 13, style: .continuous)
            )
            .overlay {
                RoundedRectangle(cornerRadius: 13, style: .continuous)
                    .strokeBorder(.white.opacity(0.3), lineWidth: 0.75)
            }
        }
        .frame(width: 300)
    }
}

// MARK: - Drawing and sharing

@MainActor
enum LogSharePicture {
    /// The Story picture at 1080 x 1920.
    static func story(for content: LogShareContent) async -> UIImage? {
        let art = await LogShareArt.load(for: content)
        let renderer = ImageRenderer(content: LogShareStory(content: content, art: art))
        renderer.proposedSize = ProposedViewSize(LogShareStory.size)
        renderer.scale = 3
        renderer.isOpaque = true
        return renderer.uiImage
    }

    /// Opens the iOS share sheet over whatever is on screen, the share sheet included.
    static func present(_ items: [Any]) {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }
        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController {
            top = presented
        }
        top?.present(UIActivityViewController(activityItems: items, applicationActivities: nil), animated: true)
    }
}
