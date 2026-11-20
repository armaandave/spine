import SwiftUI

/// Builds a title's page from anywhere, with the app's repositories, for a poster's hold menu to open: Log opens it with
/// the log up (`RailHandoff.log`). Set by `AppShellView`.
struct TitlePageBuilder {
    let build: (MediaRef, RailHandoff?) -> AnyView
}

extension EnvironmentValues {
    @Entry var titlePageBuilder: TitlePageBuilder? = nil
    /// False inside a picker (choosing a title for the Hall of Fame, a list, a picture), where posters have no hold menu.
    @Entry var posterHoldMenusEnabled = true
    /// True where a tap on a poster opens something other than its title (your own Stats open your logs): the hold
    /// menu adds Go to Title.
    @Entry var posterHoldOffersGoToTitle = false
}

extension View {
    /// Touch and hold this poster for its title's menu (`PosterHoldMenu`): your shelf while the title is in your library,
    /// Watched and Planning (+ Log once it's in progress), Add to List, the rows `place` adds for this place, and Remove
    /// from Library. `viewer` is your own state for the title; nil means it isn't in your library.
    /// `isEnabled` false leaves the poster without a menu for now (a Home tile's side poster, or a title whose state is
    /// still loading). `offersGoToTitle` adds Go to Title, where a tap on the poster opens something else.
    func posterHoldMenu<Place: View>(
        _ media: MediaSummary,
        viewer: UserMediaState?,
        isEnabled: Bool = true,
        offersGoToTitle: Bool = false,
        @ViewBuilder place: @escaping () -> Place
    ) -> some View {
        modifier(PosterHoldMenuModifier(
            media: media,
            viewer: viewer,
            isEnabled: isEnabled,
            offersGoToTitle: offersGoToTitle,
            place: place
        ))
    }

    /// The hold menu with no rows of its own for this place.
    func posterHoldMenu(
        _ media: MediaSummary,
        viewer: UserMediaState?,
        isEnabled: Bool = true,
        offersGoToTitle: Bool = false
    ) -> some View {
        posterHoldMenu(media, viewer: viewer, isEnabled: isEnabled, offersGoToTitle: offersGoToTitle) { EmptyView() }
    }

    /// The hold menu, with your state from the poster's own copy of the title.
    func posterHoldMenu(_ media: MediaSummary) -> some View {
        posterHoldMenu(media, viewer: media.userState)
    }

    /// The hold menu for a poster that may have no title behind it (a row of something else), and nothing then.
    @ViewBuilder
    func posterHoldMenu(ifAny media: MediaSummary?) -> some View {
        if let media {
            posterHoldMenu(media)
        } else {
            self
        }
    }

    /// Shows what the hold menus ask: their questions, notices and the Add to List sheet. `isActive` is false while a
    /// cover over this view shows them instead.
    func posterHoldHost(_ controller: PosterHoldController, isActive: Bool = true) -> some View {
        modifier(PosterHoldHost(controller: controller, isActive: isActive))
    }
}

extension LibraryItem {
    /// Your state for the title with your row's tracking folded in (a show's actions, a book's read, a game's
    /// playthrough), for its poster's hold menu. Only for your own rows: a friend's tracking is theirs.
    var holdViewer: UserMediaState {
        (media.userState ?? UserMediaState(isTracked: true)).applying(tracking)
    }
}

/// A title page the hold menu opened, with what the page should open on it.
private struct PosterHoldOpening: Identifiable {
    let id = UUID()
    let ref: MediaRef
    let handoff: RailHandoff?
}

private struct PosterHoldMenuModifier<Place: View>: ViewModifier {
    @Environment(PosterHoldController.self) private var controller: PosterHoldController?
    @Environment(\.titlePageBuilder) private var titlePage
    @Environment(\.posterHoldMenusEnabled) private var menusEnabled
    @Environment(\.posterHoldOffersGoToTitle) private var placeOffersGoToTitle
    @State private var opening: PosterHoldOpening?

    let media: MediaSummary
    let viewer: UserMediaState?
    let isEnabled: Bool
    let offersGoToTitle: Bool
    let place: () -> Place

    func body(content: Content) -> some View {
        content
            .contextMenu {
                if isEnabled, menusEnabled, let controller {
                    menu(controller)
                }
            }
            // The row of buttons draws its icons in the tint: the app's white disappears on the menu's light glass.
            .tint(.primary)
            .pushDestination(item: $opening) { opening in
                if let titlePage {
                    titlePage.build(opening.ref, opening.handoff)
                }
            }
    }

    @ViewBuilder
    private func menu(_ controller: PosterHoldController) -> some View {
        let menu = PosterHoldMenu(ref: media.ref, viewer: controller.viewerState(for: media.ref, poster: viewer))
        // A section's title doesn't show over the row of buttons, so the shelf is its own quiet line.
        if let shelf = menu.shelf {
            Text(shelf)
        }
        quickRow(menu, controller)
        Section {
            Button("Add to List…", systemImage: "text.badge.plus") {
                controller.addToList = media
            }
        }
        Section {
            if offersGoToTitle || placeOffersGoToTitle {
                Button("Go to Title", systemImage: "arrow.forward") {
                    open(nil)
                }
            }
            place()
        }
        if menu.offersRemoval {
            Section {
                Button("Remove from Library", systemImage: "trash", role: .destructive) {
                    controller.run(.removeFromLibrary, on: media.ref, open: open)
                }
            }
        }
    }

    /// The buttons along the top: Watched and Planning, or + Log.
    private func quickRow(_ menu: PosterHoldMenu, _ controller: PosterHoldController) -> some View {
        ControlGroup {
            ForEach(Array(menu.quick.enumerated()), id: \.offset) { _, quick in
                switch quick {
                case let .watched(word, isFilled):
                    Button(word, systemImage: isFilled ? "eye.fill" : "eye") {
                        controller.run(.watched(wasFilled: isFilled), on: media.ref, open: open)
                    }
                    .accessibilityValue(isFilled ? "On" : "")
                case let .planning(isFilled):
                    Button("Planning", systemImage: isFilled ? "bookmark.fill" : "bookmark") {
                        controller.run(.planning(wasFilled: isFilled), on: media.ref, open: open)
                    }
                    .accessibilityValue(isFilled ? "On" : "")
                case .log:
                    Button("Log", systemImage: "plus") {
                        open(.log)
                    }
                }
            }
        }
    }

    private func open(_ handoff: RailHandoff?) {
        opening = PosterHoldOpening(ref: media.ref, handoff: handoff)
    }
}

/// The hold menus' questions (Remove from Library, a show's eye that asks first), notices and the Add to List sheet,
/// shown once at the top of the app.
private struct PosterHoldHost: ViewModifier {
    let controller: PosterHoldController
    let isActive: Bool

    /// The question up: the title model's own (a show or anime), else the menu's.
    private enum Asking: Identifiable {
        case tv(TVPageConfirmation, MediaDetailViewModel)
        case menu(PosterHoldController.Question)

        var id: UUID {
            switch self {
            case let .tv(confirmation, _): confirmation.id
            case let .menu(question): question.id
            }
        }

        var prompt: String {
            switch self {
            case let .tv(confirmation, _): confirmation.prompt
            case let .menu(question): question.prompt
            }
        }
    }

    private var asking: Asking? {
        guard isActive else { return nil }
        if let model = controller.active, let confirmation = model.tvConfirmation {
            return .tv(confirmation, model)
        }
        return controller.question.map(Asking.menu)
    }

    private var message: String? {
        guard isActive else { return nil }
        if let notice = controller.notice { return notice }
        guard let model = controller.active else { return nil }
        return model.tvNotice ?? model.quickActionErrorMessage ?? model.likeErrorMessage
    }

    func body(content: Content) -> some View {
        content
            .sheet(item: Binding(
                get: { isActive ? controller.addToList : nil },
                set: { controller.addToList = $0 }
            )) { media in
                AddToListSheet(
                    target: .media(media.ref),
                    initialMedia: media,
                    listRepository: controller.listRepository,
                    mediaRepository: controller.mediaRepository,
                    peopleRepository: controller.peopleRepository,
                    onUnauthorized: controller.onUnauthorized
                )
            }
            .confirmationDialog(
                asking?.prompt ?? "",
                isPresented: Binding(get: { asking != nil }, set: { if !$0 { dismissQuestion() } }),
                titleVisibility: .visible,
                presenting: asking
            ) { asking in
                switch asking {
                case let .tv(confirmation, model):
                    Button(confirmation.confirmTitle, role: confirmation.isDestructive ? .destructive : nil) {
                        model.confirmTV(confirmation)
                    }
                case let .menu(question):
                    Button(question.confirmTitle, role: .destructive) {
                        controller.question = nil
                        question.confirm()
                    }
                }
                Button("Cancel", role: .cancel) { dismissQuestion() }
            }
            .alert(
                message ?? "",
                isPresented: Binding(get: { message != nil }, set: { if !$0 { clearMessages() } })
            ) {
                Button("OK", role: .cancel) { clearMessages() }
            }
    }

    private func dismissQuestion() {
        controller.active?.tvConfirmation = nil
        controller.question = nil
    }

    private func clearMessages() {
        controller.notice = nil
        controller.active?.tvNotice = nil
        controller.active?.quickActionErrorMessage = nil
        controller.active?.likeErrorMessage = nil
    }
}
