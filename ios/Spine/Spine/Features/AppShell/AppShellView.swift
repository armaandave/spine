import SwiftUI

struct AppShellView: View {
    @Environment(\.displayScale) private var displayScale
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: AppTab
    @State private var searchFocusRequest = 0
    @State private var requestedLibraryShelf: LibraryShelf?
    @State private var mediaLensStore = MediaLensStore()
    @State private var profileTabImage = ProfileTabImage.fallback
    @State private var appNavigationState = AppNavigationState()

    let session: AppSession

    @MainActor
    init(session: AppSession) {
        self.session = session
        _selectedTab = State(initialValue: session.signedInEntryPoint == .search ? .search : .home)
    }

    private var currentUserId: Int? {
        if case let .signedIn(user) = session.state {
            return user?.id
        }
        return nil
    }

    private var currentUserAvatarURL: URL? {
        guard case let .signedIn(user?) = session.state,
              let avatarUrl = user.avatarUrl?.trimmingCharacters(in: .whitespacesAndNewlines),
              !avatarUrl.isEmpty else { return nil }
        return URL(string: avatarUrl)
    }

    private var tabSelection: Binding<AppTab> {
        Binding(
            get: { selectedTab },
            set: { tab in
                if tab == .search, selectedTab == .search {
                    searchFocusRequest += 1
                }
                selectedTab = tab
            }
        )
    }

    var body: some View {
        TabView(selection: tabSelection) {
            HomeView(
                profileRepository: session.repositories.profile,
                mediaRepository: session.repositories.media,
                trackingRepository: session.repositories.tracking,
                diaryRepository: session.repositories.diary,
                activityRepository: session.repositories.activity,
                listRepository: session.repositories.lists,
                peopleRepository: session.repositories.people,
                currentUserId: currentUserId,
                selectedTab: selectedTab,
                onSelectTab: { selectedTab = $0 },
                onUnauthorized: unauthorized
            )
            .ignoresSafeArea(.container, edges: .bottom)
            .background { TabBarImageConfigurator() }
            .tabItem {
                Image(systemName: "house")
                    .environment(\.symbolVariants, .none)
                    .accessibilityLabel("Home")
            }
            .tag(AppTab.home)

            LazyTab(isSelected: selectedTab == .search) {
                SearchView(
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    diaryRepository: session.repositories.diary,
                    listRepository: session.repositories.lists,
                    peopleRepository: session.repositories.people,
                    mediaLensStore: mediaLensStore,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    focusRequest: searchFocusRequest,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .ignoresSafeArea(.container, edges: .bottom)
            .background { TabBarImageConfigurator() }
            .tabItem {
                Image(systemName: "magnifyingglass")
                    .accessibilityLabel("Search")
            }
            .tag(AppTab.search)

            LazyTab(isSelected: selectedTab == .library) {
                LibraryView(
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    diaryRepository: session.repositories.diary,
                    listRepository: session.repositories.lists,
                    mediaLensStore: mediaLensStore,
                    currentUserId: currentUserId,
                    requestedShelf: $requestedLibraryShelf,
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .ignoresSafeArea(.container, edges: .bottom)
            .background { TabBarImageConfigurator() }
            .tabItem {
                Image(systemName: "books.vertical")
                    .environment(\.symbolVariants, .none)
                    .accessibilityLabel("Library")
            }
            .tag(AppTab.library)

            LazyTab(isSelected: selectedTab == .diary) {
                DiaryView(
                    diaryRepository: session.repositories.diary,
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    currentUserId: currentUserId,
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .ignoresSafeArea(.container, edges: .bottom)
            .background { TabBarImageConfigurator() }
            .tabItem {
                Image(systemName: "calendar")
                    .environment(\.symbolVariants, .none)
                    .accessibilityLabel("Diary")
            }
            .tag(AppTab.diary)

            LazyTab(isSelected: selectedTab == .profile) {
                ProfileView(
                    profileRepository: session.repositories.profile,
                    diaryRepository: session.repositories.diary,
                    mediaRepository: session.repositories.media,
                    trackingRepository: session.repositories.tracking,
                    activityRepository: session.repositories.activity,
                    listRepository: session.repositories.lists,
                    peopleRepository: session.repositories.people,
                    importCoordinator: session.letterboxdImportCoordinator,
                    storygraphImportCoordinator: session.storygraphImportCoordinator,
                    goodreadsImportCoordinator: session.goodreadsImportCoordinator,
                    myAnimeListImportCoordinator: session.myAnimeListImportCoordinator,
                    currentUserId: currentUserId,
                    onLogout: {
                        Task { await session.logout() }
                    },
                    onOpenDiary: {
                        selectedTab = .diary
                    },
                    onOpenLibrary: { shelf in
                        requestedLibraryShelf = shelf
                        selectedTab = .library
                    },
                    selectedTab: selectedTab,
                    onSelectTab: { selectedTab = $0 },
                    onUnauthorized: unauthorized
                )
            }
            .ignoresSafeArea(.container, edges: .bottom)
            .background { TabBarImageConfigurator() }
            .tabItem {
                Image(uiImage: profileTabImage)
                    .accessibilityLabel("Profile")
            }
            .tag(AppTab.profile)
        }
        .tint(.white)
        .scrollEdgeEffectStyle(.soft, for: .bottom)
        .tabBarMinimizeBehavior(.never)
        .environment(\.appNavigationState, appNavigationState)
        .onChange(of: appNavigationState.returnHomeRequest) {
            selectedTab = .home
        }
        .onChange(of: scenePhase) {
            guard scenePhase == .active else { return }
            session.letterboxdImportCoordinator.resumeIfNeeded()
            session.storygraphImportCoordinator.resumeIfNeeded()
            session.goodreadsImportCoordinator.resumeIfNeeded()
            session.myAnimeListImportCoordinator.resumeIfNeeded()
        }
        .task {
            guard session.signedInEntryPoint == .search else { return }
            session.markSignedInEntryPointHandled()
            await Task.yield()
            searchFocusRequest += 1
        }
        .task(id: currentUserAvatarURL) {
            await loadProfileTabImage(from: currentUserAvatarURL)
        }
    }

    private func loadProfileTabImage(from url: URL?) async {
        profileTabImage = ProfileTabImage.fallback
        guard let url else { return }

        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            try Task.checkCancellation()
            guard let response = response as? HTTPURLResponse,
                  (200 ... 299).contains(response.statusCode),
                  let image = ProfileTabImage.avatar(from: data, scale: displayScale) else { return }
            profileTabImage = image
        } catch is CancellationError {
            return
        } catch {
            return
        }
    }

    private func unauthorized() {
        Task { await session.logout() }
    }
}

enum ProfileTabImage {
    static let fallback = UIImage(systemName: "person.crop.circle")!
        .withRenderingMode(.alwaysTemplate)

    static func avatar(from data: Data, scale: CGFloat) -> UIImage? {
        guard let source = UIImage(data: data),
              source.size.width > 0,
              source.size.height > 0 else { return nil }

        let size = CGSize(width: 30, height: 30)
        let format = UIGraphicsImageRendererFormat()
        format.scale = max(scale, 1)
        format.opaque = false
        let image = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            UIBezierPath(ovalIn: CGRect(origin: .zero, size: size)).addClip()
            let ratio = max(size.width / source.size.width, size.height / source.size.height)
            let drawSize = CGSize(width: source.size.width * ratio, height: source.size.height * ratio)
            source.draw(in: CGRect(
                x: (size.width - drawSize.width) / 2,
                y: (size.height - drawSize.height) / 2,
                width: drawSize.width,
                height: drawSize.height
            ))
        }
        guard image.cgImage != nil else { return nil }
        return image.withRenderingMode(.alwaysOriginal)
    }
}

enum AppTab: Hashable {
    case home
    case search
    case library
    case diary
    case profile
}

struct TabBarImageConfigurator: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> ImageViewController {
        ImageViewController()
    }

    func updateUIViewController(_ controller: ImageViewController, context: Context) {
        DispatchQueue.main.async { [weak controller] in
            controller?.configureImages()
        }
    }

    final class ImageViewController: UIViewController {
        func configureImages() {
            // SwiftUI must retain its delegate to update selection and load lazy tabs.
            guard let tabBarController else { return }
            NativeTabImages.configure(items: tabBarController.tabBar.items ?? [])
        }

        override func didMove(toParent parent: UIViewController?) {
            super.didMove(toParent: parent)
            configureImages()
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            configureImages()
        }
    }
}

enum NativeTabImages {
    // Supply both images together. UIKit owns the selector and image transitions.
    private static let pairs: [(image: UIImage?, selectedImage: UIImage?)] = [
        (UIImage(systemName: "house"), UIImage(systemName: "house.fill")),
        (UIImage(systemName: "magnifyingglass"), UIImage(named: "TabSearchFilled")?.withRenderingMode(.alwaysTemplate)),
        (UIImage(systemName: "books.vertical"), UIImage(systemName: "books.vertical.fill")),
    ]

    static func configure(items: [UITabBarItem]) {
        for (index, item) in items.enumerated() {
            if index < pairs.count {
                let pair = pairs[index]
                if item.image != pair.image {
                    item.image = pair.image
                }
                if item.selectedImage != pair.selectedImage {
                    item.selectedImage = pair.selectedImage
                }
            } else if item.selectedImage != item.image {
                // Diary keeps its shape; Profile keeps its original avatar rendering.
                item.selectedImage = item.image
            }
        }
    }
}

private struct LazyTab<Content: View>: View {
    let isSelected: Bool
    @ViewBuilder let content: () -> Content

    @State private var hasLoaded = false

    var body: some View {
        Group {
            if isLoaded {
                content()
            } else {
                Color.clear
            }
        }
        .onChange(of: isSelected, initial: true) {
            if isSelected {
                hasLoaded = true
            }
        }
    }

    private var isLoaded: Bool {
        isSelected || hasLoaded
    }
}

#Preview {
    AppShellView(session: AppSession(repositories: .live()))
}
