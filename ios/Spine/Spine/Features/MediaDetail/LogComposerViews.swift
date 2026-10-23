import SwiftUI

/// Rows shared by the log sheet (`MediaLogView`) and the Edit Log sheet. Both are
/// plain grouped forms; these are their custom rows.
enum LogComposerStyle {
    static let starGold = Color(red: 0.94, green: 0.64, blue: 0.24)
    static let gameProgressFootnote = "Blank means unknown; zero means zero. Time replaces this playthrough's total rather than adding a session."
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

    nonisolated static func containsSeparator(_ text: String) -> Bool {
        text.contains { $0 == "\n" || $0 == "," }
    }

    /// Text typed into the tag field. Everything before a return or comma becomes a tag.
    func updateQuery(_ text: String) {
        let parts = text.split(omittingEmptySubsequences: false) { $0 == "\n" || $0 == "," }
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
        let available = library.filter { suggestion in
            !added.contains { $0.caseInsensitiveCompare(suggestion.name) == .orderedSame }
        }
        let needle = query.trimmingCharacters(in: .whitespaces)
        guard !needle.isEmpty else { return Array(available.prefix(limit)) }

        let options: String.CompareOptions = [.caseInsensitive, .diacriticInsensitive]
        let startsWith = available.filter { $0.name.range(of: needle, options: options.union(.anchored)) != nil }
        let contains = available.filter { suggestion in
            suggestion.name.range(of: needle, options: options) != nil && !startsWith.contains(suggestion)
        }
        return Array((startsWith + contains).prefix(limit))
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
                Text(title)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(.primary)
                    .lineLimit(3)

                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            .padding(.bottom, 4)

            Spacer(minLength: 0)
        }
    }

    /// "Feb 23, 2017" for an API date like "2017-02-23"; anything else (a bare year) as is.
    static func dateLabel(_ raw: String) -> String {
        let parts = raw.prefix(10).split(separator: "-")
        guard parts.count == 3, let date = CalendarDateCodec.date(from: String(raw.prefix(10))) else { return raw }
        return date.formatted(.dateTime.month(.abbreviated).day().year())
    }
}

/// The rating card: stars with their value, and Like and Rewatch under them.
struct LogRatingCard: View {
    @Binding var ratingSteps: Int
    @Binding var liked: Bool
    @Binding var isRepeat: Bool
    let repeatLabel: String
    let ratingLabel: String
    let ratingIdentifier: String
    let onScrub: (_ locationX: CGFloat, _ width: CGFloat) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                LogStars(
                    steps: $ratingSteps,
                    accessibilityValue: ratingLabel,
                    accessibilityIdentifier: ratingIdentifier,
                    onScrub: onScrub
                )

                Spacer(minLength: 8)

                if ratingSteps > 0 {
                    Text(Self.value(for: ratingSteps))
                        .font(.title3.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                }
            }

            HStack(spacing: 24) {
                toggle(liked ? "Liked" : "Like", systemImage: liked ? "heart.fill" : "heart", tint: .pink, isOn: liked) {
                    liked.toggle()
                }
                .accessibilityLabel("Like")
                .sensoryFeedback(trigger: liked) { _, isLiked in
                    isLiked ? SensoryFeedback.impact(weight: .medium) : .selection
                }

                toggle(repeatLabel, systemImage: "arrow.clockwise", tint: .green, isOn: isRepeat) {
                    isRepeat.toggle()
                }
                .sensoryFeedback(.selection, trigger: isRepeat)
            }
        }
        .padding(.vertical, 6)
    }

    /// "4.5" for nine half-steps, "4" for eight.
    static func value(for steps: Int) -> String {
        steps.isMultiple(of: 2) ? "\(steps / 2)" : "\(steps / 2).5"
    }

    private func toggle(
        _ title: String,
        systemImage: String,
        tint: Color,
        isOn: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: systemImage)
                Text(title)
            }
            .font(.body.weight(.medium))
            .foregroundStyle(isOn ? tint : .secondary)
        }
        .buttonStyle(.borderless)
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

/// Five stars rated in half steps. A tap or a sideways drag sets the rating;
/// a vertical drag is left to the scroll view.
struct LogStars: View {
    @Binding var steps: Int
    let accessibilityValue: String
    let accessibilityIdentifier: String
    let onScrub: (_ locationX: CGFloat, _ width: CGFloat) -> Void

    private static let starWidth: CGFloat = 40
    private static let width = starWidth * 5

    var body: some View {
        HStack(spacing: 0) {
            ForEach(1...5, id: \.self) { star in
                Image(systemName: symbol(for: star))
                    .font(.system(size: 32))
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
struct LogTagsSection<Focus: Hashable>: View {
    @Bindable var draft: LogTagDraft
    let focus: FocusState<Focus?>.Binding
    let focusValue: Focus

    static var fieldID: String { "log-tags-field" }

    static func suggestionID(_ name: String) -> String {
        "log-tag-suggestion-\(name)"
    }

    /// The last row of the section, to keep in view above the keyboard while typing.
    static func scrollTarget(for draft: LogTagDraft) -> String {
        draft.suggestions.last.map { suggestionID($0.name) } ?? fieldID
    }

    private var isEditing: Bool {
        focus.wrappedValue == focusValue
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
