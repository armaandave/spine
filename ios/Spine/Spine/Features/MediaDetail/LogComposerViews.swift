import Nuke
import SwiftUI

/// Rows shared by the log sheet (`MediaLogView`) and the Edit Log sheet. Both are
/// plain grouped forms; these are their custom rows.
enum LogComposerStyle {
    static let starGold = Color(red: 0.94, green: 0.64, blue: 0.24)
    static let gameProgressFootnote = "Blank means unknown; zero means zero. Time replaces this playthrough's total rather than adding a session."
    /// A translucent, glossy card: the blurred backdrop shows through it, with a soft sheen
    /// at the top. It's a plain translucent fill rather than a live material, so scrolling
    /// costs nothing extra.
    static let rowBackground = LinearGradient(
        colors: [.white.opacity(0.12), .white.opacity(0.06)],
        startPoint: .top,
        endPoint: .bottom
    )
}

extension View {
    /// The log forms' glassy card background, in place of the solid grouped-row gray.
    func logRowBackground() -> some View {
        listRowBackground(LogComposerStyle.rowBackground)
    }
}

/// Tags on a log. The user's own tags load once and are matched locally as they
/// type, so suggestions appear instantly; return or a comma adds a tag and keeps
/// the keyboard up for the next one.
@MainActor
@Observable
final class LogTagDraft {
    var tags: [String]
    var query = ""
    private(set) var library: [DiaryTagSuggestion] = []
    private var hasLoaded = false

    init(tags: [String] = []) {
        self.tags = tags
    }

    var suggestions: [DiaryTagSuggestion] {
        Self.suggestions(for: query, in: library, excluding: tags)
    }

    func load(from repository: DiaryRepository) async throws {
        guard !hasLoaded else { return }
        library = try await repository.allTags(mine: true)
        hasLoaded = true
    }

    /// Return or comma ends a tag.
    nonisolated static func isSeparator(_ character: Character) -> Bool {
        character == "\n" || character == ","
    }

    nonisolated static func containsSeparator(_ text: String) -> Bool {
        text.contains(where: isSeparator)
    }

    /// Text typed into the tag field. Everything before a return or comma becomes a tag.
    func updateQuery(_ text: String) {
        let parts = text.split(omittingEmptySubsequences: false, whereSeparator: Self.isSeparator)
        guard parts.count > 1 else {
            query = text
            return
        }
        parts.dropLast().forEach { add(String($0)) }
        query = String((parts.last ?? "").drop(while: \.isWhitespace))
    }

    /// Adds whatever is still typed in the field, so a tag isn't lost on Done or Save.
    func commitQuery() {
        add(query)
        query = ""
    }

    func pick(_ name: String) {
        add(name)
        query = ""
    }

    func add(_ rawTag: String) {
        let tag = rawTag.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !tag.isEmpty, !tags.contains(where: { $0.caseInsensitiveCompare(tag) == .orderedSame }) else { return }
        tags.append(tag)
    }

    func remove(_ tag: String) {
        tags.removeAll { $0 == tag }
    }

    /// Tags that start with the query come first, then tags that contain it; each group
    /// keeps the library's most-used-first order. An empty query offers the most used.
    nonisolated static func suggestions(
        for query: String,
        in library: [DiaryTagSuggestion],
        excluding added: [String],
        limit: Int = 5
    ) -> [DiaryTagSuggestion] {
        let addedNames = Set(added.map { $0.lowercased() })
        let needle = query.trimmingCharacters(in: .whitespaces)
        let options: String.CompareOptions = [.caseInsensitive, .diacriticInsensitive]
        var startsWith: [DiaryTagSuggestion] = []
        var contains: [DiaryTagSuggestion] = []

        for suggestion in library where !addedNames.contains(suggestion.name.lowercased()) {
            if needle.isEmpty || suggestion.name.range(of: needle, options: options.union(.anchored)) != nil {
                startsWith.append(suggestion)
                if startsWith.count == limit { break }
            } else if contains.count < limit, suggestion.name.range(of: needle, options: options) != nil {
                contains.append(suggestion)
            }
        }
        return Array((startsWith + contains).prefix(limit))
    }
}

/// A faint, blurred wash of the media's artwork behind the top of a log form, with a soft
/// sheen, fading into the page. The blur and color boost are baked into the cached image when
/// it loads, so nothing is re-blurred while the form scrolls or the keyboard moves.
struct LogBackdrop: View {
    let url: URL?
    var accentColor: Color?

    static let processors: [any ImageProcessing] = [
        ImageProcessors.GaussianBlur(radius: 22),
        ImageProcessors.CoreImageFilter(
            name: "CIColorControls",
            parameters: ["inputSaturation": 1.35],
            identifier: "spine.log-backdrop.saturation-1.35"
        ),
    ]

    var body: some View {
        ZStack(alignment: .top) {
            SpinePalette.pageBackground

            SpineAsyncImage(url: url, thumbnailSize: CGSize(width: 180, height: 110), processors: Self.processors) { phase in
                if case let .success(image) = phase {
                    image
                        .resizable()
                        .scaledToFill()
                } else {
                    (accentColor ?? .clear).opacity(0.3)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .opacity(0.75)

            LinearGradient(colors: [.white.opacity(0.08), .clear], startPoint: .top, endPoint: .center)

            // Strongest at the top, but a faint wash of color stays all the way down so the
            // glassy cards always have something to show through.
            LinearGradient(
                stops: [
                    .init(color: SpinePalette.pageBackground.opacity(0.2), location: 0),
                    .init(color: SpinePalette.pageBackground.opacity(0.55), location: 0.35),
                    .init(color: SpinePalette.pageBackground.opacity(0.8), location: 1),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

/// Poster, title and subtitle at the top of a log form.
struct LogHeader: View {
    let title: String
    var subtitle: String?
    var posterURL: String?
    var mediaType: String
    var orientation: PosterOrientation?
    var showsPoster = true

    var body: some View {
        HStack(alignment: .bottom, spacing: 16) {
            if showsPoster {
                MediaArtwork(
                    url: posterURL,
                    title: title,
                    slot: .logSheet,
                    mediaType: mediaType,
                    orientation: orientation
                )
            }

            VStack(alignment: .leading, spacing: 4) {
                // Explicit colors: the header sits in a section header, which dims .primary.
                Text(title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(3)

                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.6))
                        .lineLimit(2)
                }
            }
            .padding(.bottom, 4)

            Spacer(minLength: 0)
        }
    }
}

/// The rating card: one row with the stars, then Like and Rewatch on the right.
struct LogRatingCard: View {
    @Binding var ratingSteps: Int
    @Binding var liked: Bool
    @Binding var isRepeat: Bool
    let repeatLabel: String
    let ratingLabel: String
    let ratingIdentifier: String
    let onScrub: (_ locationX: CGFloat, _ width: CGFloat) -> Void

    var body: some View {
        HStack(spacing: 8) {
            LogStars(
                steps: $ratingSteps,
                accessibilityValue: ratingLabel,
                accessibilityIdentifier: ratingIdentifier,
                onScrub: onScrub
            )

            Spacer(minLength: 0)

            GlassEffectContainer(spacing: 6) {
                HStack(spacing: 6) {
                    Button {
                        liked.toggle()
                    } label: {
                        Image(systemName: liked ? "heart.fill" : "heart")
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundStyle(.white)
                            .frame(width: 36, height: 36)
                            .contentShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .glassEffect(liked ? .regular.tint(.pink).interactive() : .regular.interactive(), in: .circle)
                    .accessibilityLabel("Like")
                    .accessibilityAddTraits(liked ? .isSelected : [])
                    .sensoryFeedback(trigger: liked) { _, isLiked in
                        isLiked ? SensoryFeedback.impact(weight: .medium) : .selection
                    }

                    Button {
                        isRepeat.toggle()
                    } label: {
                        // On a narrow phone the pill drops its icon, then its word, before the row overflows.
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 4) {
                                Image(systemName: "arrow.clockwise")
                                Text(repeatLabel)
                            }
                            Text(repeatLabel)
                            Image(systemName: "arrow.clockwise")
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                        .padding(.horizontal, 10)
                        .frame(minWidth: 36, minHeight: 36)
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .glassEffect(isRepeat ? .regular.tint(.green).interactive() : .regular.interactive(), in: .capsule)
                    .accessibilityLabel(repeatLabel)
                    .accessibilityAddTraits(isRepeat ? .isSelected : [])
                    .sensoryFeedback(.selection, trigger: isRepeat)
                }
            }
            .animation(.snappy(duration: 0.2), value: liked)
            .animation(.snappy(duration: 0.2), value: isRepeat)
        }
        .padding(.vertical, 4)
    }
}

/// Five stars rated in half steps. A tap or a sideways drag sets the rating;
/// a vertical drag is left to the scroll view.
struct LogStars: View {
    @Binding var steps: Int
    let accessibilityValue: String
    let accessibilityIdentifier: String
    let onScrub: (_ locationX: CGFloat, _ width: CGFloat) -> Void

    private static let starWidth: CGFloat = 34
    private static let width = starWidth * 5

    var body: some View {
        HStack(spacing: 0) {
            ForEach(1...5, id: \.self) { star in
                Image(systemName: symbol(for: star))
                    .font(.system(size: 28))
                    .foregroundStyle(steps >= star * 2 - 1 ? LogComposerStyle.starGold : Color(.tertiaryLabel))
                    .frame(width: Self.starWidth, height: 44)
            }
        }
        .contentShape(Rectangle())
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { value in
                    guard abs(value.translation.width) > abs(value.translation.height) else { return }
                    onScrub(value.location.x, Self.width)
                }
                .onEnded { value in
                    guard abs(value.translation.width) < 10, abs(value.translation.height) < 10 else { return }
                    onScrub(value.location.x, Self.width)
                }
        )
        .accessibilityRepresentation {
            Slider(value: Binding(get: { Double(steps) }, set: { steps = Int($0) }), in: 0...10, step: 1) {
                Text("Rating")
            }
            .accessibilityValue(accessibilityValue)
            .accessibilityIdentifier(accessibilityIdentifier)
        }
        .sensoryFeedback(.selection, trigger: steps)
    }

    private func symbol(for star: Int) -> String {
        if steps >= star * 2 { return "star.fill" }
        if steps == star * 2 - 1 { return "star.leadinghalf.filled" }
        return "star"
    }
}

/// The tags section: added tags, the field, and the user's matching tags while typing.
/// It keeps its own rows in view and reacts to typing itself, so a keystroke here doesn't
/// redraw the rest of the form.
struct LogTagsSection<Focus: Hashable>: View {
    @Bindable var draft: LogTagDraft
    let focus: FocusState<Focus?>.Binding
    let focusValue: Focus
    let scrollProxy: ScrollViewProxy

    private static var fieldID: String { "log-tags-field" }

    private static func suggestionID(_ name: String) -> String {
        "log-tag-suggestion-\(name)"
    }

    /// The last row of the section, to keep in view above the keyboard while typing.
    private var scrollTarget: String {
        draft.suggestions.last.map { Self.suggestionID($0.name) } ?? Self.fieldID
    }

    private var isEditing: Bool {
        focus.wrappedValue == focusValue
    }

    private func scrollToLastRow() {
        withAnimation(.easeOut(duration: 0.2)) {
            scrollProxy.scrollTo(scrollTarget, anchor: .bottom)
        }
    }

    var body: some View {
        Section {
            // Chips and field share one row: inserting a chip row above the field
            // would rebuild the field's row and drop the keyboard.
            VStack(alignment: .leading, spacing: 10) {
                if !draft.tags.isEmpty {
                    FlowLayout(spacing: 8) {
                        ForEach(draft.tags, id: \.self) { tag in
                            Button {
                                draft.remove(tag)
                            } label: {
                                HStack(spacing: 5) {
                                    Text(tag)
                                    Image(systemName: "xmark")
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(.secondary)
                                }
                                .font(.subheadline)
                                .foregroundStyle(.primary)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color(.tertiarySystemFill), in: Capsule())
                            }
                            .buttonStyle(.borderless)
                            .accessibilityLabel("Remove tag \(tag)")
                        }
                    }
                }

                // Return adds the tag and keeps the field focused for the next one.
                // The on-screen keyboard's return types a newline into this
                // vertical field, which never ends editing; the newline is turned
                // into a tag after the edit, because a field ignores text rewritten
                // inside its own edit. A hardware return submits instead.
                TextField("Add tags", text: $draft.query, axis: .vertical)
                    .lineLimit(1...3)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused(focus, equals: focusValue)
                    .onChange(of: draft.query) { _, text in
                        if LogTagDraft.containsSeparator(text) {
                            draft.updateQuery(text)
                        }
                    }
                    .onSubmit {
                        draft.commitQuery()
                        focus.wrappedValue = focusValue
                    }
            }
            .id(Self.fieldID)
            .sensoryFeedback(trigger: draft.tags.count) { oldCount, newCount in
                newCount > oldCount ? SensoryFeedback.impact(weight: .light) : .selection
            }
            .onChange(of: isEditing) { _, editing in
                guard editing else {
                    // Done, a swipe or another field: keep what was typed as a tag.
                    draft.commitQuery()
                    return
                }
                // The suggestion rows and the keyboard arrive after focus does.
                Task { @MainActor in
                    try? await Task.sleep(for: .milliseconds(350))
                    scrollToLastRow()
                }
            }
            .onChange(of: scrollTarget) {
                if isEditing {
                    scrollToLastRow()
                }
            }

            if isEditing {
                ForEach(draft.suggestions, id: \.name) { suggestion in
                    Button {
                        draft.pick(suggestion.name)
                        focus.wrappedValue = focusValue
                    } label: {
                        HStack(spacing: 10) {
                            Image(systemName: "number")
                                .foregroundStyle(.secondary)
                            Text(highlighted(suggestion.name))
                                .foregroundStyle(.primary)
                            Spacer(minLength: 8)
                            Text("\(suggestion.usageCount)")
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityLabel("Add tag \(suggestion.name)")
                    .id(Self.suggestionID(suggestion.name))
                }
            }
        }
        .logRowBackground()
    }

    /// Bolds the part of the tag that matches what's typed.
    private func highlighted(_ name: String) -> AttributedString {
        var text = AttributedString(name)
        let needle = draft.query.trimmingCharacters(in: .whitespaces)
        if !needle.isEmpty, let range = text.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) {
            text[range].font = .body.weight(.semibold)
        }
        return text
    }
}

/// Total time and completion for a game log, as form rows.
struct LogGameProgressRows<Focus: Hashable>: View {
    @Binding var draft: GameProgressDraft
    let focus: FocusState<Focus?>.Binding
    let hoursFocus: Focus
    let minutesFocus: Focus
    let percentageFocus: Focus

    var body: some View {
        LabeledContent("Total time") {
            HStack(spacing: 4) {
                numberField("Total hours this playthrough", text: $draft.hours, focusValue: hoursFocus)
                Text("h")
                numberField("Minutes", text: $draft.minutes, focusValue: minutesFocus)
                Text("m")
                clearButton("Clear time", isVisible: !draft.hours.isEmpty || !draft.minutes.isEmpty) {
                    draft.hours = ""
                    draft.minutes = ""
                }
            }
        }

        LabeledContent("Completion") {
            HStack(spacing: 4) {
                numberField("Percentage", text: $draft.percentage, focusValue: percentageFocus)
                Text("%")
                clearButton("Clear percentage", isVisible: !draft.percentage.isEmpty) {
                    draft.percentage = ""
                }
            }
        }
    }

    private func numberField(_ label: String, text: Binding<String>, focusValue: Focus) -> some View {
        TextField("–", text: text)
            .keyboardType(.numberPad)
            .multilineTextAlignment(.trailing)
            .monospacedDigit()
            .foregroundStyle(.primary)
            .frame(minWidth: 28)
            .fixedSize()
            .focused(focus, equals: focusValue)
            .accessibilityLabel(label)
            .accessibilityIdentifier(label)
    }

    private func clearButton(_ label: String, isVisible: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.tertiary)
        }
        .buttonStyle(.borderless)
        .opacity(isVisible ? 1 : 0)
        .disabled(!isVisible)
        .accessibilityHidden(!isVisible)
        .accessibilityLabel(label)
        .accessibilityIdentifier(label)
    }
}
