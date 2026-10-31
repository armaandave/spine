import SwiftUI

/// Builds someone's profile page, so any screen can link to one without carrying the profile page's
/// dependencies. `AppShellView` provides it.
struct UserProfileBuilder {
    let build: (String) -> AnyView
}

private struct UserProfileBuilderKey: EnvironmentKey {
    static let defaultValue: UserProfileBuilder? = nil
}

extension EnvironmentValues {
    var userProfileBuilder: UserProfileBuilder? {
        get { self[UserProfileBuilderKey.self] }
        set { self[UserProfileBuilderKey.self] = newValue }
    }
}

struct UserProfileDestination: View {
    @Environment(\.userProfileBuilder) private var builder
    let username: String

    var body: some View {
        if let builder {
            builder.build(username)
        } else {
            ContentUnavailableView("Profile unavailable", systemImage: "person.crop.circle.badge.exclamationmark")
        }
    }
}

struct UserAvatar: View {
    let urlString: String?
    var size: CGFloat = 44

    var body: some View {
        SpineAsyncImage(url: URL(string: urlString ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.fill")
                    .font(.system(size: size * 0.4, weight: .bold))
                    .foregroundStyle(.white.opacity(0.54))
            }
        }
        .frame(width: size, height: size)
        .background(.white.opacity(0.10), in: Circle())
        .clipShape(Circle())
        .accessibilityHidden(true)
    }
}

/// A person's avatar and names; tapping it opens their profile.
struct SocialUserLink: View {
    let user: UserSummary
    var isPrivate = false

    var body: some View {
        NavigationLink {
            UserProfileDestination(username: user.username)
        } label: {
            HStack(spacing: 12) {
                UserAvatar(urlString: user.avatarUrl)

                VStack(alignment: .leading, spacing: 2) {
                    Text(user.displayName)
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(.white)
                        .lineLimit(1)

                    HStack(spacing: 4) {
                        Text("@\(user.username)")
                        if isPrivate {
                            Image(systemName: "lock.fill")
                                .font(.caption2)
                                .accessibilityLabel("Private")
                        }
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.52))
                    .lineLimit(1)
                }

                Spacer(minLength: 8)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Follow, Requested or Following, for someone else. Following asks before unfollowing; Requested takes the
/// request back.
struct FollowButton: View {
    enum Size {
        case profile
        case row
    }

    let username: String
    let isPrivate: Bool
    let followedBy: Bool
    @Binding var state: FollowState
    let repository: SocialRepository
    var size: Size = .row
    var onChange: (FollowState) -> Void = { _ in }
    var onUnauthorized: () -> Void = {}

    @State private var isWorking = false
    @State private var confirmsUnfollow = false
    @State private var errorMessage: String?

    var body: some View {
        Button(action: tap) {
            Text(state.title(followedBy: followedBy))
                .socialButtonLabel(size)
        }
        .socialGlass(prominent: state == .none)
        .disabled(isWorking)
        .sensoryFeedback(.impact(weight: .light), trigger: state)
        .accessibilityIdentifier("follow.\(username)")
        .confirmationDialog("Unfollow @\(username)?", isPresented: $confirmsUnfollow, titleVisibility: .visible) {
            Button("Unfollow", role: .destructive) {
                Task { await unfollow() }
            }
        } message: {
            if isPrivate {
                Text("You'll need to ask again to see their profile.")
            }
        }
        .alert("Couldn't update", isPresented: errorBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(errorMessage ?? "")
        }
    }

    private var errorBinding: Binding<Bool> {
        Binding(
            get: { errorMessage != nil },
            set: { if !$0 { errorMessage = nil } }
        )
    }

    private func tap() {
        switch state {
        case .none:
            Task { await follow() }
        case .requested:
            Task { await unfollow() }
        case .following:
            confirmsUnfollow = true
        }
    }

    private func follow() async {
        let previous = state
        state = isPrivate ? .requested : .following
        isWorking = true
        defer { isWorking = false }
        do {
            state = try await repository.follow(username: username)
            NotificationCenter.default.post(name: .followsDidChange, object: nil)
            onChange(state)
        } catch {
            state = previous
            fail(error)
        }
    }

    private func unfollow() async {
        let previous = state
        state = .none
        isWorking = true
        defer { isWorking = false }
        do {
            try await repository.unfollow(username: username)
            NotificationCenter.default.post(name: .followsDidChange, object: nil)
            onChange(state)
        } catch {
            state = previous
            fail(error)
        }
    }

    private func fail(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        } else if !(error is CancellationError) {
            errorMessage = error.localizedDescription
        }
    }
}

@MainActor
@Observable
final class FollowListViewModel {
    var users: [SocialUser] = []
    var requests: [FollowRequest] = []
    var isLoading = true
    var isLoadingNextPage = false
    var errorMessage: String?
    var actionErrorMessage: String?
    /// Bumped on every full load, so the list is rebuilt rather than updated row by row.
    private(set) var revision = 0

    let username: String
    let showsFollowers: Bool
    /// The signed-in user's own followers: requests come first and anyone can be removed.
    let isOwnFollowers: Bool
    let repository: SocialRepository
    private let onUnauthorized: () -> Void
    private var nextPage: String?

    init(
        username: String,
        showsFollowers: Bool,
        isOwnFollowers: Bool,
        repository: SocialRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        self.username = username
        self.showsFollowers = showsFollowers
        self.isOwnFollowers = isOwnFollowers
        self.repository = repository
        self.onUnauthorized = onUnauthorized
    }

    var hasMorePages: Bool {
        nextPage != nil
    }

    func load() async {
        isLoading = users.isEmpty && requests.isEmpty
        errorMessage = nil
        defer { isLoading = false }
        do {
            if isOwnFollowers {
                requests = try await repository.followRequests()
            }
            let loaded = try await repository.followList(username: username, followers: showsFollowers, page: nil)
            users = loaded.results
            revision += 1
            nextPage = loaded.next
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    func loadNextPageIfNeeded(after user: SocialUser) async {
        guard user.id == users.last?.id, let nextPage, !isLoadingNextPage else { return }
        isLoadingNextPage = true
        defer { isLoadingNextPage = false }
        do {
            let loaded = try await repository.followList(username: username, followers: showsFollowers, page: nextPage)
            var seen = Set(users.map(\.id))
            users += loaded.results.filter { seen.insert($0.id).inserted }
            self.nextPage = loaded.next
        } catch {
            handleUnauthorized(error)
        }
    }

    /// The request stays until the server answers, so its button isn't pulled out from under the tap.
    func answer(_ request: FollowRequest, accept: Bool) async {
        do {
            try await repository.answerFollowRequest(id: request.id, accept: accept)
            requests.removeAll { $0.id == request.id }
            if accept {
                await load()
            }
        } catch {
            fail(error)
        }
    }

    func remove(_ user: SocialUser) async {
        guard let index = users.firstIndex(of: user) else { return }
        users.remove(at: index)
        do {
            try await repository.removeFollower(username: user.username)
        } catch {
            users.insert(user, at: min(index, users.count))
            fail(error)
        }
    }

    private func fail(_ error: Error) {
        handleUnauthorized(error)
        if !(error is CancellationError) {
            actionErrorMessage = error.localizedDescription
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

/// Someone's followers, or the people they follow. On your own Followers page, requests come first.
struct FollowListView: View {
    @State private var viewModel: FollowListViewModel
    @State private var removing: SocialUser?
    private let currentUserId: Int?
    private let onUnauthorized: () -> Void

    init(
        username: String,
        showsFollowers: Bool,
        isOwnProfile: Bool,
        currentUserId: Int?,
        repository: SocialRepository,
        onUnauthorized: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: FollowListViewModel(
            username: username,
            showsFollowers: showsFollowers,
            isOwnFollowers: isOwnProfile && showsFollowers,
            repository: repository,
            onUnauthorized: onUnauthorized
        ))
        self.currentUserId = currentUserId
        self.onUnauthorized = onUnauthorized
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                content
                    .spineContentTransition(value: contentPhase)
                    .padding(.horizontal, 16)
                    .padding(.top, 6)
                    .padding(.bottom, 96)
            }
            .refreshable {
                await viewModel.load()
            }
        }
        .navigationTitle(viewModel.showsFollowers ? "Followers" : "Following")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbarBackground(SpinePalette.pageBackground, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .task {
            await viewModel.load()
        }
        .confirmationDialog(
            "Remove @\(removing?.username ?? "")?",
            isPresented: removingBinding,
            titleVisibility: .visible,
            presenting: removing
        ) { user in
            Button("Remove", role: .destructive) {
                Task { await viewModel.remove(user) }
            }
        } message: { user in
            Text("\(user.displayName) won't be told they were removed.")
        }
        .alert("Couldn't update", isPresented: actionErrorBinding) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.actionErrorMessage ?? "")
        }
    }

    @ViewBuilder
    private var content: some View {
        if viewModel.isLoading {
            ProgressView()
                .tint(.white)
                .frame(maxWidth: .infinity, minHeight: 360)
        } else if let error = viewModel.errorMessage, viewModel.users.isEmpty, viewModel.requests.isEmpty {
            ContentUnavailableView {
                Label("Could not load", systemImage: "exclamationmark.triangle")
            } description: {
                Text(error)
            } actions: {
                Button("Retry") {
                    Task { await viewModel.load() }
                }
            }
            .foregroundStyle(.white)
            .frame(minHeight: 360)
        } else {
            LazyVStack(alignment: .leading, spacing: 0) {
                if !viewModel.requests.isEmpty {
                    sectionTitle("Requests")
                    // A request's id can equal a follower's user id, and one lazy stack holds both lists.
                    ForEach(viewModel.requests, id: \.rowID) { request in
                        requestRow(request)
                        if request.id != viewModel.requests.last?.id {
                            ActivityFeedDivider()
                        }
                    }
                    if !viewModel.users.isEmpty {
                        sectionTitle("Followers")
                            .padding(.top, 18)
                    }
                }

                if viewModel.users.isEmpty, viewModel.requests.isEmpty {
                    ContentUnavailableView(
                        viewModel.showsFollowers ? "No followers yet" : "Not following anyone yet",
                        systemImage: "person.2"
                    )
                    .foregroundStyle(.white)
                    .frame(minHeight: 360)
                }

                ForEach(viewModel.users, id: \.rowID) { user in
                    userRow(user)
                        .task {
                            await viewModel.loadNextPageIfNeeded(after: user)
                        }
                    if user.id != viewModel.users.last?.id {
                        ActivityFeedDivider()
                    }
                }

                if viewModel.isLoadingNextPage {
                    ProgressView()
                        .tint(.white)
                        .frame(maxWidth: .infinity, minHeight: 60)
                }
            }
            // A glass button inserted after approving a request kept a washed-out look until the page reloaded.
            .id(viewModel.revision)
        }
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(.footnote.weight(.bold))
            .foregroundStyle(.white.opacity(0.52))
            .textCase(.uppercase)
            .padding(.vertical, 8)
    }

    private func requestRow(_ request: FollowRequest) -> some View {
        HStack(spacing: 8) {
            SocialUserLink(user: request.fromUser)

            Button {
                Task { await viewModel.answer(request, accept: true) }
            } label: {
                Text("Approve")
                    .socialButtonLabel(.row)
            }
            .socialGlass(prominent: true)

            Button {
                Task { await viewModel.answer(request, accept: false) }
            } label: {
                Text("Decline")
                    .socialButtonLabel(.row)
            }
            .socialGlass(prominent: false)
        }
        .sensoryFeedback(.impact(weight: .light), trigger: viewModel.requests.count)
        .padding(.vertical, 10)
    }

    private func userRow(_ user: SocialUser) -> some View {
        HStack(spacing: 8) {
            SocialUserLink(user: user.summary, isPrivate: user.isPrivate)

            if viewModel.isOwnFollowers {
                Button {
                    removing = user
                } label: {
                    Text("Remove")
                        .socialButtonLabel(.row)
                }
                .socialGlass(prominent: false)
            } else if user.id != currentUserId {
                FollowButton(
                    username: user.username,
                    isPrivate: user.isPrivate,
                    followedBy: user.viewerRelationship?.followedBy ?? false,
                    state: followState(for: user),
                    repository: viewModel.repository,
                    onUnauthorized: onUnauthorized
                )
            }
        }
        .padding(.vertical, 10)
    }

    /// Writes a row's Follow button back into its row, so it survives scrolling away and back.
    private func followState(for user: SocialUser) -> Binding<FollowState> {
        Binding(
            get: { viewModel.users.first(where: { $0.id == user.id })?.followState ?? .none },
            set: { state in
                guard let index = viewModel.users.firstIndex(where: { $0.id == user.id }) else { return }
                var relationship = viewModel.users[index].viewerRelationship
                    ?? SocialRelationship(following: false, requested: false, followedBy: false)
                relationship.following = state == .following
                relationship.requested = state == .requested
                viewModel.users[index].viewerRelationship = relationship
            }
        )
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: !viewModel.users.isEmpty || !viewModel.requests.isEmpty,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var removingBinding: Binding<Bool> {
        Binding(
            get: { removing != nil },
            set: { if !$0 { removing = nil } }
        )
    }

    private var actionErrorBinding: Binding<Bool> {
        Binding(
            get: { viewModel.actionErrorMessage != nil },
            set: { if !$0 { viewModel.actionErrorMessage = nil } }
        )
    }
}

/// People matching a search, under Search's Users lens.
struct UserSearchResults: View {
    let query: String
    let users: [SocialUser]
    let isLoading: Bool

    var body: some View {
        if !users.isEmpty {
            List(users) { user in
                SocialUserLink(user: user.summary, isPrivate: user.isPrivate)
                    .listRowInsets(EdgeInsets(top: 10, leading: 16, bottom: 10, trailing: 12))
                    .listRowBackground(Color.clear)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .scrollDismissesKeyboard(.interactively)
        } else if query.isEmpty {
            ContentUnavailableView(
                "Find people",
                systemImage: "person.2",
                description: Text("Search by name or username.")
            )
        } else if !isLoading {
            ContentUnavailableView.search(text: query)
        } else {
            Color.clear
        }
    }
}

private extension Text {
    func socialButtonLabel(_ size: FollowButton.Size) -> some View {
        font(size == .profile ? .footnote.weight(.semibold) : .caption.weight(.semibold))
            .lineLimit(1)
            .padding(.horizontal, size == .profile ? 16 : 11)
            .frame(height: size == .profile ? 30 : 26)
    }
}

private extension View {
    /// A small Liquid Glass capsule. The glass goes on the button, as on the app's other glass buttons (glass in
    /// the label swallowed taps), and lifts a little behind the main action.
    func socialGlass(prominent: Bool) -> some View {
        buttonStyle(.plain)
            .contentShape(.capsule)
            .glassEffect(
                prominent ? .regular.tint(.white.opacity(0.24)).interactive() : .regular.interactive(),
                in: .capsule
            )
    }
}

private extension FollowRequest {
    var rowID: String { "request-\(id)" }
}

private extension SocialUser {
    var rowID: String { "user-\(id)" }
}

struct SocialActionMessage: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}

/// The … menu's actions on someone's profile that ask first.
enum ProfileSocialAction: Identifiable {
    case block
    case report
    case removeFollower

    var id: Self { self }

    func title(username: String) -> String {
        switch self {
        case .block: "Block @\(username)?"
        case .report: "Report @\(username)?"
        case .removeFollower: "Remove @\(username)?"
        }
    }

    var confirmTitle: String {
        switch self {
        case .block: "Block"
        case .report: "Report"
        case .removeFollower: "Remove"
        }
    }

    func message(displayName: String) -> String {
        switch self {
        case .block: "\(displayName) won't be able to find you or see your profile, and you'll stop following each other."
        case .report: "Spine's owner will take a look at this profile."
        case .removeFollower: "\(displayName) won't be told they were removed."
        }
    }
}
