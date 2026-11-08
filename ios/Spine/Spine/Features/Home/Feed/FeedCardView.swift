import SwiftUI

/// One card of the Home feed, 115 pt tall: baked blurred art behind a straight poster on the left, a text column
/// (author, title, headline, then what room is left for detail rows), a meter along the bottom and the read-only like
/// and comment counts at the bottom right, beside the last row and the meter.
///
/// Everything it draws is decided by `FeedCardModel`, so a card is cheap to re-evaluate and skips drawing when its
/// model is equal (`.equatable()` at the call site). Positions are leading and top paddings from the design's
/// numbers, never measured. Only the background layer is clipped to the card's rounded shape.
struct FeedCardView: View, Equatable {
    let model: FeedCardModel
    /// Takes the item's id, so the tap resolves the current row at tap time and never opens stale data.
    let onOpen: (Int) -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The ink the art's measured brightness settled when that is not the ink the poster's accent already guessed, else
    /// nil. It starts as what was measured before, so a card scrolled back in has the right ink on its first frame.
    /// The brightness itself is the background's: a measurement that agrees with the guess changes nothing here, so
    /// a card whose art lands is drawn a second time only when its ink really changes.
    @State private var measuredInk: ArtInk?

    init(model: FeedCardModel, onOpen: @escaping (Int) -> Void) {
        self.model = model
        self.onOpen = onOpen
        _measuredInk = State(initialValue: Self.settledInk(for: model))
    }

    static func == (lhs: FeedCardView, rhs: FeedCardView) -> Bool {
        lhs.model == rhs.model
    }

    var body: some View {
        let _ = PerfCounters.bump(.feedCardBody)
        let ink = measuredInk ?? model.art.guessInk
        ZStack(alignment: .topLeading) {
            FeedCardBackground(art: model.art) { settled in
                let differing = settled == model.art.guessInk ? nil : settled
                if measuredInk != differing { measuredInk = differing }
            }
            content(ink: ink)
            FeedAuthorLink(username: model.username, avatarURL: model.avatarURL, dayLabel: model.dayLabel, ink: ink)
                .padding(.top, HomeDesign.Metric.cardTextTop)
                .padding(.leading, textLeading)
                .padding(.trailing, HomeDesign.Metric.cardTrailing)
        }
        .frame(maxWidth: .infinity)
        .frame(height: HomeDesign.Metric.cardHeight)
        // The ink settles over 0.18 s when the art is measured, instead of jumping.
        .animation(SpineMotion.animation(reduceMotion: reduceMotion), value: ink)
        .dynamicTypeSize(...HomeDesign.Text.maxCategory)
        .contentShape(RoundedRectangle(cornerRadius: HomeDesign.Metric.cardCorner, style: .continuous))
        .onTapGesture { onOpen(model.id) }
        .onChange(of: model.art.backgroundURL) { _, _ in
            let remembered = Self.settledInk(for: model)
            if measuredInk != remembered { measuredInk = remembered }
        }
        .onChange(of: model.art.guessInk) { _, _ in
            let remembered = Self.settledInk(for: model)
            if measuredInk != remembered { measuredInk = remembered }
        }
        .onAppear { PerfCounters.bump(.feedCardAppear) }
    }

    /// What was measured before for this card's art, as `measuredInk` keeps it.
    private static func settledInk(for model: FeedCardModel) -> ArtInk? {
        let measured = ArtInk.conclusive(luma: FeedCardArt.rememberedLuma(backgroundURL: model.art.backgroundURL))
        return measured == model.art.guessInk ? nil : measured
    }

    // MARK: Geometry

    /// Where the text starts: after the poster, or after square art.
    private var textLeading: CGFloat {
        model.art.isSquare ? HomeDesign.Metric.cardTextLeadingSquare : HomeDesign.Metric.cardTextLeading
    }

    static let titleRowHeight: CGFloat = 21
    static let headlineHeight: CGFloat = 20
    /// The gaps above the title and the headline. The card is short, so its lines sit close.
    static let titleTop: CGFloat = 3
    static let headlineTop: CGFloat = 2

    /// The meter's top edge: its drawing ends `meterBottom` above the card's bottom, level with the counts, so a
    /// window (ticks and a series line) starts higher than plain ticks or a bar.
    static func meterTop(for kind: ProgressMeterModel.Kind) -> CGFloat {
        HomeDesign.Metric.cardHeight - HomeDesign.Metric.meterBottom - ProgressMeterMetrics.height(for: kind)
    }

    /// Room kept at the right of the detail rows and the meter for the like and comment counts, from their digits
    /// rather than measured: two 13 pt icons with 3 pt gaps, 10 pt between the pairs, 8 pt a digit, and 10 pt of air.
    /// A card without counts (a Planning day) keeps none.
    static func countsReserve(for engagement: PostEngagement?) -> CGFloat {
        guard let engagement else { return 0 }
        let digits = countText(engagement.likeCount).count + countText(engagement.commentCount).count
        return 13 + 3 + 10 + 13 + 3 + 10 + 8 * CGFloat(digits)
    }

    // MARK: Content

    /// Everything but the background and the author link: one accessibility element that opens the post.
    private func content(ink: ArtInk) -> some View {
        ZStack(alignment: .topLeading) {
            FeedCardPoster(art: model.art, title: model.title)
            textColumn(ink: ink)
            if let meter = model.meter {
                ProgressMeterView(model: meter, palette: palette(ink: ink))
                    .padding(.top, Self.meterTop(for: meter.kind))
                    .padding(.leading, textLeading)
                    .padding(.trailing, HomeDesign.Metric.cardTrailing + Self.countsReserve(for: model.engagement))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(alignment: .bottomTrailing) {
            counts(ink: ink)
                .padding(.trailing, HomeDesign.Metric.cardTrailing)
                .padding(.bottom, HomeDesign.Metric.countsBottom)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Self.accessibilityText(for: model))
        .accessibilityHint(model.accessibilityHint)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction(.default) { onOpen(model.id) }
    }

    /// The presenter's words, then where the meter stands.
    static func accessibilityText(for model: FeedCardModel) -> String {
        [model.accessibilityLabel, model.meter?.accessibilityLabel]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: ", ")
    }

    private func palette(ink: ArtInk) -> MeterPalette {
        MeterPalette(before: ink.before, rest: ink.rest, accent: ink.accent(posterAccentHex: model.art.accentHex))
    }

    // MARK: Text column

    private func textColumn(ink: ArtInk) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            // The author line is drawn above, as its own link; this keeps its place.
            Color.clear.frame(height: FeedAuthorLink.height)
            titleRow(ink: ink)
                .padding(.top, Self.titleTop)
            if let headline = model.headline {
                headlineRow(headline, ink: ink)
                    .padding(.top, Self.headlineTop)
            }
            // The rows reach the counts' band at the bottom, so they stop short of the counts.
            ForEach(Array(model.details.enumerated()), id: \.offset) { _, row in
                detailRow(row, ink: ink)
                    .padding(.trailing, Self.countsReserve(for: model.engagement))
                    .padding(.top, row.topMargin)
            }
        }
        .padding(.top, HomeDesign.Metric.cardTextTop)
        .padding(.leading, textLeading)
        .padding(.trailing, HomeDesign.Metric.cardTrailing)
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }

    /// The title gives way to its tag's width and truncates; the tag never shrinks. They share a baseline.
    private func titleRow(ink: ArtInk) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(model.title)
                .font(HomeDesign.Text.title)
                .foregroundStyle(ink.text)
                .lineLimit(1)
            if let tag = model.tag {
                Text(tag)
                    .font(HomeDesign.Text.tag)
                    .foregroundStyle(ink.muted)
                    .lineLimit(1)
                    .fixedSize()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: Self.titleRowHeight, alignment: .top)
    }

    // MARK: Headline

    private func headlineRow(_ headline: FeedHeadline, ink: ArtInk) -> some View {
        HStack(spacing: 6) {
            switch headline {
            case let .marks(starSymbols, liked, rewatch, _):
                if !starSymbols.isEmpty {
                    HStack(spacing: 1) {
                        ForEach(Array(starSymbols.enumerated()), id: \.offset) { _, symbol in
                            Image(systemName: symbol)
                        }
                    }
                    .foregroundStyle(ink.star)
                }
                if liked {
                    Image(systemName: "heart.fill")
                        .foregroundStyle(ink.heart)
                }
                if rewatch {
                    Image(systemName: "arrow.clockwise")
                        .foregroundStyle(ink.rewatch)
                }
            case let .progress(amount, tail, pills):
                Text(amount)
                    .fontWeight(.heavy)
                    .foregroundStyle(ink.accent(posterAccentHex: model.art.accentHex))
                    .lineLimit(1)
                    .layoutPriority(2)
                if !pills.isEmpty {
                    ForEach(pills, id: \.self) { pill($0, ink: ink) }
                } else if let tail {
                    Text(tail)
                        .fontWeight(.medium)
                        .foregroundStyle(ink.muted)
                        .lineLimit(1)
                }
            case let .status(words, tail):
                Text(words)
                    .fontWeight(.bold)
                    .foregroundStyle(ink.text)
                    .lineLimit(1)
                    .layoutPriority(2)
                if let tail {
                    Text(tail)
                        .fontWeight(.medium)
                        .foregroundStyle(ink.muted)
                        .lineLimit(1)
                }
            case .started:
                Text("Started")
                    .fontWeight(.heavy)
                    .foregroundStyle(ink.accent(posterAccentHex: model.art.accentHex))
                    .lineLimit(1)
            }
        }
        .font(HomeDesign.Text.headline)
        .fontWeight(isMarks(headline) ? .bold : nil)
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: Self.headlineHeight)
    }

    private func isMarks(_ headline: FeedHeadline) -> Bool {
        if case .marks = headline { return true }
        return false
    }

    /// "Paused", "Stopped", "Picked back up": a small chip after the amount.
    private func pill(_ word: String, ink: ArtInk) -> some View {
        Text(word)
            .font(HomeDesign.Text.pill)
            .foregroundStyle(ink.text)
            .lineLimit(1)
            .padding(.horizontal, 9)
            .frame(height: 22)
            .background(ink.chipFill, in: Capsule())
            .overlay { Capsule().strokeBorder(ink.chipLine, lineWidth: 0.5) }
    }

    // MARK: Detail rows

    @ViewBuilder
    private func detailRow(_ row: FeedDetailRow, ink: ArtInk) -> some View {
        switch row {
        case .spoilers:
            HStack(spacing: 5) {
                Image(systemName: "eye.slash")
                    .font(.system(size: 12, weight: .semibold))
                Text("Contains spoilers")
                    .font(HomeDesign.Text.pill)
                    .lineLimit(1)
            }
            .foregroundStyle(ink.text)
            .padding(.horizontal, 10)
            .frame(height: row.height)
            .background(ink.chipFill, in: Capsule())
            .overlay { Capsule().strokeBorder(ink.chipLine, lineWidth: 0.5) }
            .frame(maxWidth: .infinity, alignment: .leading)
        case let .review(text, lines):
            Text(text)
                .font(HomeDesign.Text.review)
                .foregroundStyle(ink.text)
                .lineLimit(lines)
                .lineSpacing(2.3)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .frame(height: row.height, alignment: .top)
        case let .favorite(glyph, text):
            HStack(spacing: 6) {
                Text(glyph)
                Text(text)
                    .lineLimit(1)
            }
            .font(HomeDesign.Text.quiet)
            .foregroundStyle(ink.muted)
            .frame(maxWidth: .infinity, alignment: .leading)
            .frame(height: row.height, alignment: .top)
        case let .quiet(text, lines):
            Text(text)
                .font(HomeDesign.Text.quiet)
                .foregroundStyle(ink.muted)
                .lineLimit(lines)
                .lineSpacing(1.9)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .frame(height: row.height, alignment: .top)
        }
    }

    // MARK: Counts

    /// A count's words: always the number, so a card nobody has reacted to reads "0" beside each icon.
    static func countText(_ count: Int) -> String {
        count.formatted()
    }

    /// Read-only likes and comments, each with its number (a zero reads "0", as in the mock). A Planning card has none.
    @ViewBuilder
    private func counts(ink: ArtInk) -> some View {
        if let engagement = model.engagement {
            HStack(spacing: 10) {
                HStack(spacing: 3) {
                    Image(systemName: "heart.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(engagement.hasLiked ? ink.heart : ink.muted)
                    Text(Self.countText(engagement.likeCount))
                }
                HStack(spacing: 3) {
                    CommentBubbleShape()
                        .fill(ink.muted)
                        .frame(width: 13, height: 13)
                    Text(Self.countText(engagement.commentCount))
                }
            }
            .font(HomeDesign.Text.counts)
            .foregroundStyle(ink.muted)
        }
    }
}
