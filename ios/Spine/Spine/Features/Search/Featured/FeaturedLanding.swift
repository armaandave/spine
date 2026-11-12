import SwiftUI

/// What Search shows while the field is idle: with "All Media", one big word per kind of list; with one media type,
/// that type's lists grouped by kind.
struct FeaturedLanding: View {
    let store: FeaturedListsStore
    let selectedMediaType: String
    let enabledMediaTypes: [String]
    let onSelect: (FeaturedListSelection) -> Void

    var body: some View {
        if selectedMediaType == APIConstants.allMedia {
            SearchListsLanding(store: store, enabledMediaTypes: enabledMediaTypes, onSelect: onSelect)
        } else {
            FeaturedTypeLayer(store: store, mediaType: selectedMediaType, onSelect: onSelect)
                .id(selectedMediaType)
        }
    }
}

extension View {
    /// Loads a featured layer when it appears, revalidates it when the app returns from the background, and polls
    /// it while it's on screen and one of its lists is live, or an upcoming window is still to start.
    func featuredLayerLifecycle(store: FeaturedListsStore, mediaType: String?) -> some View {
        modifier(FeaturedLayerLifecycle(store: store, mediaType: mediaType))
    }
}

private struct FeaturedLayerLifecycle: ViewModifier {
    @Environment(\.scenePhase) private var scenePhase
    @State private var wasInBackground = false

    let store: FeaturedListsStore
    let mediaType: String?

    func body(content: Content) -> some View {
        content
            .task(id: mediaType) {
                await store.load(mediaType: mediaType)
            }
            .task(id: pollID) {
                guard pollID.polls else { return }
                await store.pollWhileLive(mediaType: mediaType)
            }
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .background:
                    wasInBackground = true
                case .active where wasInBackground:
                    wasInBackground = false
                    // The normal staleness rule, so every layer still in the navigation stack doesn't refetch on
                    // each app switch; a live list always refetches.
                    Task { await store.load(mediaType: mediaType, force: store.hasLiveList(mediaType: mediaType)) }
                default:
                    break
                }
            }
    }

    private var pollID: PollID {
        // An upcoming window counts, so polling starts when it does without waiting for a re-render.
        PollID(mediaType: mediaType, polls: scenePhase == .active && store.nextPollDelay(mediaType: mediaType) != nil)
    }

    private struct PollID: Hashable {
        let mediaType: String?
        let polls: Bool
    }
}

struct FeaturedErrorState: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Could not load featured lists", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
        } actions: {
            Button("Try Again", action: retry)
                .buttonStyle(.bordered)
                .tint(.white)
        }
    }
}

struct FeaturedEmptyPrompt: View {
    var body: some View {
        ContentUnavailableView(
            "Search Spine",
            systemImage: "magnifyingglass",
            description: Text("Enter a title to find media.")
        )
    }
}
