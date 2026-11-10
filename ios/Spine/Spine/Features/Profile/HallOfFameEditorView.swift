import SwiftUI

/// Settings › Hall of Fame: the crown as your profile shows it, and under it the list that sets it, read left to
/// right across the crown. Every change saves as you make it.
@MainActor
@Observable
final class HallOfFameEditorModel {
    /// Every pick in crown order, including ones of types you turned off.
    private(set) var picks: [MediaSummary]
    var errorMessage: String?

    private let enabledMediaTypes: [String]
    private let repository: ProfileRepository
    private let onSaved: ([MediaSummary]) -> Void
    private let onUnauthorized: () -> Void
    private var savedPicks: [MediaSummary]
    private var generation = 0
    private var saveChain: Task<Void, Never>?
    private var orderBeforeReorder: [String]?

    init(
        picks: [MediaSummary],
        enabledMediaTypes: [String],
        repository: ProfileRepository,
        onSaved: @escaping ([MediaSummary]) -> Void,
        onUnauthorized: @escaping () -> Void
    ) {
        self.picks = picks
        self.savedPicks = picks
        self.enabledMediaTypes = enabledMediaTypes
        self.repository = repository
        self.onSaved = onSaved
        self.onUnauthorized = onUnauthorized
    }

    /// The picks the crown shows, in crown order.
    var visible: [MediaSummary] {
        ProfileFavorites.visible(picks, enabledMediaTypes: enabledMediaTypes)
    }

    /// Picks of a type you turned off: kept, and still one of the eight.
    var hidden: [MediaSummary] {
        let shown = Set(visible.map(\.id))
        return picks.filter { !shown.contains($0.id) }
    }

    /// The list under the crown: the card above the photo first when there is one, then the row left to right.
    var listRows: [MediaSummary] {
        HallOfFameOrder.listOrder(visible)
    }

    var canAdd: Bool {
        picks.count < HallOfFameOrder.limit
    }

    var pickedIDs: Set<String> {
        Set(picks.map(\.id))
    }

    /// A row is picked up by its handle: remember the order, so letting go saves only a real change.
    func beginReorder() {
        orderBeforeReorder = picks.map(\.id)
    }

    /// Moves the dragged row to `destination` in the list. The crown follows; nothing saves until `endReorder`.
    func moveRow(_ media: MediaSummary, to destination: Int) {
        var rows = listRows
        guard let source = rows.firstIndex(where: { $0.id == media.id }),
              rows.indices.contains(destination),
              source != destination else { return }
        rows.insert(rows.remove(at: source), at: destination)
        picks = HallOfFameOrder.crownOrder(rows) + hidden
    }

    func endReorder() {
        defer { orderBeforeReorder = nil }
        guard let orderBeforeReorder, orderBeforeReorder != picks.map(\.id) else { return }
        save()
    }

    /// A new pick goes on the right end of the row.
    func add(_ media: MediaSummary) {
        guard canAdd, !pickedIDs.contains(media.id) else { return }
        apply(visible + [media] + hidden)
    }

    func replace(_ old: MediaSummary, with new: MediaSummary) {
        guard old.id != new.id, !pickedIDs.contains(new.id) else { return }
        apply(picks.map { $0.id == old.id ? new : $0 })
    }

    func remove(_ media: MediaSummary) {
        apply(picks.filter { $0.id != media.id })
    }

    /// Waits for the save in flight, so the server never takes an older list after a newer one.
    func waitForSaves() async {
        await saveChain?.value
    }

    private func apply(_ newPicks: [MediaSummary]) {
        picks = newPicks
        save()
    }

    /// Saves run one at a time, and a save that a newer edit has overtaken is skipped: the newer one sends the
    /// whole list anyway. A failed save puts back the last list the server took.
    private func save() {
        generation += 1
        let generation = generation
        let previous = saveChain
        saveChain = Task { [weak self] in
            await previous?.value
            guard let self, generation == self.generation else { return }
            do {
                let saved = try await repository.saveHallOfFame(refs: picks.map(\.ref))
                savedPicks = saved
                onSaved(saved)
                if generation == self.generation {
                    picks = saved
                }
            } catch is CancellationError {
                return
            } catch {
                if generation == self.generation {
                    picks = savedPicks
                    errorMessage = error.localizedDescription
                }
                if case APIError.unauthorized = error {
                    onUnauthorized()
                }
            }
        }
    }
}

struct HallOfFameEditorView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var model: HallOfFameEditorModel
    @State private var pickerTarget: HallOfFamePickerTarget?
    /// The row held by its handle, and the list position it was picked up from.
    @State private var draggedID: String?
    @State private var dragStartIndex = 0

    private let profile: UserProfile
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        profile: UserProfile,
        profileRepository: ProfileRepository,
        mediaRepository: MediaRepository,
        onUnauthorized: @escaping () -> Void,
        onSaved: @escaping ([MediaSummary]) -> Void
    ) {
        self.profile = profile
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        _model = State(initialValue: HallOfFameEditorModel(
            picks: profile.hallOfFamePicks,
            enabledMediaTypes: profile.preferences.enabledMediaTypes,
            repository: profileRepository,
            onSaved: onSaved,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        list
        .background {
            SpinePageBackground()
        }
        .navigationTitle("Hall of Fame")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $pickerTarget) { target in
            switch target {
            case .add:
                HallOfFameSearchView(
                    title: "Add Favorite",
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized
                ) { media in
                    model.add(media)
                }
            case let .change(current):
                HallOfFameSearchView(
                    title: "Change Favorite",
                    mediaRepository: mediaRepository,
                    onUnauthorized: onUnauthorized,
                    onSelect: { media in
                        model.replace(current, with: media)
                    },
                    onRemove: {
                        model.remove(current)
                    }
                )
            }
        }
        .alert("Couldn’t Save Your Hall of Fame", isPresented: errorBinding) {
            Button("OK") {
                model.errorMessage = nil
            }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }

    private var list: some View {
        List {
            // The crown scrolls with the list. Its row runs the full width of the screen: the usual side
            // margins clip a full crown's outer cards.
            Section {
                crown
            }
            .listSectionMargins(.horizontal, 0)
            .listRowInsets(EdgeInsets())
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

            Section {
                ForEach(model.listRows) { media in
                    HallOfFameEditorRow(
                        media: media,
                        isLifted: draggedID == media.id,
                        onChange: { pickerTarget = .change(media) },
                        onDragChanged: { dragRow(media, by: $0) },
                        onDragEnded: endDrag
                    )
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        // Settings tints everything white, which would blank the red button.
                        Button("Delete", systemImage: "trash", role: .destructive) {
                            model.remove(media)
                        }
                        .tint(.red)
                    }
                }

                if model.canAdd {
                    Button {
                        pickerTarget = .add
                    } label: {
                        Label("Add Favorite", systemImage: "plus")
                    }
                }
            } footer: {
                Text(listFooter)
            }

            if !model.hidden.isEmpty {
                Section {
                    ForEach(model.hidden) { media in
                        HallOfFameEditorRow(media: media, isDimmed: true)
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button("Delete", systemImage: "trash", role: .destructive) {
                                    model.remove(media)
                                }
                                .tint(.red)
                            }
                    }
                } header: {
                    Text("Turned off")
                } footer: {
                    Text("These are hidden from your crown and come back when you turn their type on again in Media & Preferences.")
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
    }

    private var listFooter: String {
        guard !model.visible.isEmpty else {
            return "Pick up to \(HallOfFameOrder.limit) favorites from any of your media types."
        }
        return "Top to bottom here is left to right on your crown. With an even number of favorites, "
            + "the first one sits above your photo and the rest fill the row."
    }

    /// The crown exactly as your profile draws it, always fully open, so a reorder shows where every card lands.
    @ViewBuilder
    private var crown: some View {
        let slots = ProfileFavorites.slots(from: model.visible, enabledMediaTypes: profile.preferences.enabledMediaTypes)

        if slots.isEmpty {
            avatar
                .padding(.vertical, 20)
                .frame(maxWidth: .infinity)
        } else {
            // Room for a poster above the photo, which is taller than the album card the profile leaves room for.
            let clearance = HallOfFameCrownLayout.posterTopClearance

            ZStack(alignment: .top) {
                HallOfFameCrownView(slots: slots) { slot in
                    if let item = slot.item {
                        pickerTarget = .change(item)
                    }
                }
                .zIndex(0)

                avatar
                    .zIndex(1)
            }
            .padding(.top, clearance)
            .frame(height: clearance + HallOfFameCrownLayout.crownHeight, alignment: .top)
            .padding(.bottom, HallOfFameCrownLayout.lowestCardEdge(for: slots) - HallOfFameCrownLayout.crownHeight + 8)
            .frame(maxWidth: .infinity)
        }
    }

    private var avatar: some View {
        SettingsAvatar(profile: profile, size: HallOfFameCrownLayout.avatarDiameter)
            .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
    }

    /// Dragging a row's handle moves it up and down only, a row at a time as the finger passes each row, so it
    /// can't leave the list. The crown above follows as it moves; letting go saves.
    private func dragRow(_ media: MediaSummary, by translation: CGFloat) {
        if draggedID != media.id {
            draggedID = media.id
            dragStartIndex = model.listRows.firstIndex(where: { $0.id == media.id }) ?? 0
            model.beginReorder()
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        }
        let steps = Int((translation / hallOfFameRowHeight).rounded())
        let target = min(max(dragStartIndex + steps, 0), model.listRows.count - 1)
        guard model.listRows.firstIndex(where: { $0.id == media.id }) != target else { return }
        withAnimation(reduceMotion ? nil : .snappy(duration: 0.22)) {
            model.moveRow(media, to: target)
        }
        UISelectionFeedbackGenerator().selectionChanged()
    }

    private func endDrag() {
        guard draggedID != nil else { return }
        draggedID = nil
        model.endReorder()
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { model.errorMessage != nil && pickerTarget == nil },
            set: { if !$0 { model.errorMessage = nil } }
        )
    }
}

/// Every row is this tall, so a drag of one row's height moves a row one place.
private let hallOfFameRowHeight: CGFloat = 60

private struct HallOfFameEditorRow: View {
    let media: MediaSummary
    var isDimmed = false
    var isLifted = false
    var onChange: (() -> Void)?
    /// The handle's vertical travel since it was picked up; a row without these has no handle.
    var onDragChanged: ((CGFloat) -> Void)?
    var onDragEnded: (() -> Void)?

    var body: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: media.displayPosterURL,
                title: media.title,
                slot: .diaryEntry,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )
            .scaleEffect(0.72)
            .frame(width: 30, height: 45)

            VStack(alignment: .leading, spacing: 2) {
                Text(media.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)

                Text(typeCaption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .opacity(isDimmed ? 0.45 : 1)

            Spacer(minLength: 8)

            if let onChange {
                Button(action: onChange) {
                    Image(systemName: "pencil")
                        .font(.subheadline.weight(.semibold))
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("Change \(media.title)")
            }

            if let onDragChanged, let onDragEnded {
                Image(systemName: "line.3.horizontal")
                    .font(.body.weight(.medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 36, height: hallOfFameRowHeight)
                    .contentShape(Rectangle())
                    .gesture(HallOfFameHandlePan(onChanged: onDragChanged, onEnded: onDragEnded))
                    .accessibilityLabel("Reorder \(media.title)")
            }
        }
        .frame(height: hallOfFameRowHeight)
        .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 8))
        .listRowBackground(
            Color(uiColor: isLifted ? .tertiarySystemGroupedBackground : .secondarySystemGroupedBackground)
        )
        .accessibilityElement(children: .contain)
    }

    /// "Movies · 1995".
    private var typeCaption: String {
        let type = MediaTypeTheme.theme(for: media.ref.mediaType).displayName
        guard let year = media.releaseDate?.prefix(4), year.count == 4 else { return type }
        return "\(type) · \(year)"
    }
}

/// The handle's drag. A UIKit pan, so the list waits for it: a SwiftUI drag inside a list loses the touch to the
/// list's own scrolling. It reports the finger's vertical travel only.
private struct HallOfFameHandlePan: UIGestureRecognizerRepresentable {
    let onChanged: (CGFloat) -> Void
    let onEnded: () -> Void

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let pan = UIPanGestureRecognizer()
        pan.delegate = context.coordinator
        return pan
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        switch recognizer.state {
        case .began, .changed:
            onChanged(recognizer.translation(in: nil).y)
        case .ended, .cancelled, .failed:
            onEnded()
        default:
            break
        }
    }

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
        Coordinator()
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        /// The list's scrolling (and its swipe to delete) wait until the handle's pan has failed.
        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldBeRequiredToFailBy otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            otherGestureRecognizer.view is UIScrollView
        }
    }
}

enum HallOfFamePickerTarget: Identifiable {
    case add
    case change(MediaSummary)

    var id: String {
        switch self {
        case .add: "add"
        case let .change(media): "change-\(media.id)"
        }
    }
}
