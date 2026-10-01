import Foundation
import UIKit

@MainActor
@Observable
final class AppSession {
    enum State {
        case checking
        case signedOut
        case signedIn(AuthUser?)
    }

    enum SignedInEntryPoint: Equatable {
        case home
        case search
    }

    /// How the launch sequence (refresh the session, then load the profile) ended.
    private enum LaunchOutcome {
        case profile(UserProfile)
        /// The server rejected the session: it is over.
        case rejected
        /// Nothing came back that says anything about the session (offline, timeout, server trouble): keep it.
        case unavailable
        /// The session ended while the launch was still running.
        case abandoned
    }

    var state: State = .checking
    var errorMessage: String?
    private(set) var signedInEntryPoint: SignedInEntryPoint = .home

    let repositories: AppRepositories
    let letterboxdImportCoordinator: LetterboxdImportCoordinator
    let storygraphImportCoordinator: StoryGraphImportCoordinator
    let goodreadsImportCoordinator: GoodreadsImportCoordinator
    let myAnimeListImportCoordinator: MyAnimeListImportCoordinator

    @ObservationIgnored private let userCache: SignedInUserCache
    /// How long a launch with a remembered user waits for the launch (refresh, then profile) before opening the
    /// shell anyway.
    @ObservationIgnored private let launchShellDelay: Duration
    @ObservationIgnored private var profileTask: Task<Void, Never>?
    /// The launch sequence (refresh, then load the profile), which carries on if the shell opens before it is done.
    @ObservationIgnored private var launchTask: Task<LaunchOutcome, Never>?
    /// Waits for a launch that outlasted `launchShellDelay` to finish, after the shell is already open.
    @ObservationIgnored private var launchWatcher: Task<Void, Never>?
    @ObservationIgnored private var activationObserver: (any NSObjectProtocol)?
    /// Set once a request has got through since signing in (a login, a profile, any authenticated response), proof
    /// that the network works. A coordinator that resumes a running import before then would flip it to failed on
    /// the first network error.
    @ObservationIgnored private var hasConfirmedConnectivity = false
    /// Set once a fresh profile has been shown this session (or, at sign-in, the user the server just returned).
    /// Until then the shell shows the remembered user and keeps retrying the profile, whatever else has got through.
    @ObservationIgnored private var hasFreshProfile = false
    @ObservationIgnored private var connectivityObserver: (any NSObjectProtocol)?
    /// True from the moment a session starts ending until the app shows the signed-out state. While it is set,
    /// nothing may bring the session back to life (a late profile, an activation retry) and a second sign-out
    /// has nothing left to do.
    @ObservationIgnored private var isSigningOut = false

    init(
        repositories: AppRepositories,
        defaults: UserDefaults = .standard,
        launchShellDelay: Duration = .seconds(2)
    ) {
        self.repositories = repositories
        self.launchShellDelay = launchShellDelay
        self.userCache = SignedInUserCache(defaults: defaults)
        self.letterboxdImportCoordinator = LetterboxdImportCoordinator(
            importRepository: repositories.imports,
            defaults: defaults
        )
        self.storygraphImportCoordinator = StoryGraphImportCoordinator(
            importRepository: repositories.imports,
            defaults: defaults
        )
        self.goodreadsImportCoordinator = GoodreadsImportCoordinator(
            importRepository: repositories.imports,
            defaults: defaults
        )
        self.myAnimeListImportCoordinator = MyAnimeListImportCoordinator(
            importRepository: repositories.imports,
            defaults: defaults
        )
        self.letterboxdImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
        self.storygraphImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
        self.goodreadsImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
        self.myAnimeListImportCoordinator.onUnauthorized = { [weak self] in
            Task { await self?.logout() }
        }
        // The shell resumes running imports whenever the app becomes active, which would bypass the wait for
        // a first successful request. The coordinators ask before they resume.
        self.letterboxdImportCoordinator.canResume = { [weak self] in self?.hasConfirmedConnectivity ?? false }
        self.storygraphImportCoordinator.canResume = { [weak self] in self?.hasConfirmedConnectivity ?? false }
        self.goodreadsImportCoordinator.canResume = { [weak self] in self?.hasConfirmedConnectivity ?? false }
        self.myAnimeListImportCoordinator.canResume = { [weak self] in self?.hasConfirmedConnectivity ?? false }
    }

    func start() async {
        let hasTokens: Bool
        do {
            hasTokens = try repositories.auth.hasStoredTokens
        } catch {
            // The Keychain can't be read right now, most likely because the device is locked. That says nothing
            // about whether there is a session, so the answer is neither the login screen nor a forgotten user:
            // open the shell as it was left, and let the profile request try again whenever the app is active.
            SessionLog.note("Launch: the Keychain couldn't be read, so the session is kept and retried later")
            enterShellWithoutFreshProfile()
            return
        }
        guard hasTokens else {
            SessionLog.note("Launch: no stored session")
            userCache.clear()
            signedInEntryPoint = .home
            state = .signedOut
            return
        }

        // Unstructured, so it carries on if the shell opens before it is done. Requests the shell makes
        // meanwhile that get rejected join this same refresh rather than starting another.
        let launch = Task { await self.runLaunchSequence() }
        launchTask = launch
        let remembered = cachedUser()
        if remembered != nil, !(await waitBounded(for: launch, upTo: launchShellDelay)) {
            // A slow network. The user is known, so there is no reason to keep them on the splash screen: the
            // shell opens with them now, and whatever the launch finds out, it applies when it is done.
            signedInEntryPoint = .home
            state = .signedIn(remembered)
            observeActivation()
            observeConnectivity()
            launchWatcher = Task { [weak self] in
                let outcome = await launch.value
                // `endSession` cancels the launch, and a session that ended meanwhile must not be revived, or
                // another one ended, by what the launch finds out late.
                guard !launch.isCancelled, !Task.isCancelled else { return }
                await self?.finishLaunch(outcome)
            }
            return
        }
        let outcome = await launch.value
        guard !launch.isCancelled else { return }
        await finishLaunch(outcome)
    }

    /// Refreshes the session and loads the profile: what a launch needs before it can show the signed-in user.
    private func runLaunchSequence() async -> LaunchOutcome {
        do {
            try await repositories.auth.refresh()
        } catch APIError.unauthorized {
            // The server rejected the refresh token, so the session really is over.
            return .rejected
        } catch {
            // Temporary failure (offline, timeout, server hiccup): the tokens are intact, so stay signed in.
            return .unavailable
        }
        guard !Task.isCancelled else { return .abandoned }
        do {
            return .profile(try await repositories.profile.me())
        } catch APIError.unauthorized {
            // The refresh worked, yet the profile request was refused even after refreshing again: over.
            return .rejected
        } catch {
            // The refresh worked but the profile request didn't: handle it the same way.
            return .unavailable
        }
    }

    private func finishLaunch(_ outcome: LaunchOutcome) async {
        switch outcome {
        case let .profile(profile):
            signedInEntryPoint = .home
            adopt(profile)
        case .rejected:
            await endSession(errorMessage: APIError.unauthorized.localizedDescription)
        case .unavailable:
            // The shell opens now if it hasn't yet, and the profile keeps being retried.
            if case .checking = state {
                enterShellWithoutFreshProfile()
            } else {
                loadProfileInBackground()
            }
        case .abandoned:
            break
        }
    }

    func login(usernameOrEmail: String, password: String) async {
        errorMessage = nil
        do {
            let user = try await repositories.auth.login(usernameOrEmail: usernameOrEmail, password: password)
            userCache.save(user)
            signedInEntryPoint = .home
            state = .signedIn(user)
            hasFreshProfile = true
            confirmConnectivity()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func register(username: String, email: String, password: String) async {
        errorMessage = nil
        do {
            let user = try await repositories.auth.register(username: username, email: email, password: password)
            userCache.save(user)
            signedInEntryPoint = .search
            state = .signedIn(user)
            hasFreshProfile = true
            confirmConnectivity()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Signs out, for the Sign Out button and for every screen that reports its session over (a 401 the app
    /// couldn't recover from). Doing it twice, or while it is happening, does nothing the second time.
    func logout() async {
        await endSession()
    }

    func clearError() {
        errorMessage = nil
    }

    func markSignedInEntryPointHandled() {
        signedInEntryPoint = .home
    }

    /// Loads the profile again if none has got through yet this session. The app calls it whenever it becomes
    /// active, which is when a launch that started offline is most likely to have a network now.
    func retryProfileIfNeeded() {
        guard !isSigningOut, case .signedIn = state, !hasFreshProfile else { return }
        loadProfileInBackground()
    }

    // MARK: - Session

    /// Enters the shell without waiting for the profile request, which can take ~20 s on a bad network. It opens
    /// with the remembered user, so ownership-dependent UI (edit and delete on your own logs, list ownership, the
    /// profile tab image) works offline, and the fresh profile replaces it whenever it arrives.
    private func enterShellWithoutFreshProfile() {
        signedInEntryPoint = .home
        state = .signedIn(cachedUser())
        observeActivation()
        observeConnectivity()
        loadProfileInBackground()
    }

    /// The remembered user, unless the stored tokens were issued to somebody else (a backup restored the Keychain
    /// and the app's defaults from different moments, say). Showing another account's name and owner-only
    /// controls would be worse than showing none, so a cache that contradicts the tokens is forgotten.
    private func cachedUser() -> AuthUser? {
        guard let user = userCache.load() else { return nil }
        if let tokenUserID = repositories.auth.storedUserID, tokenUserID != user.id {
            SessionLog.note("Launch: the remembered user isn't the one the stored session belongs to, so it is ignored")
            userCache.clear()
            return nil
        }
        return user
    }

    /// A profile got through: remember the user and show them.
    private func adopt(_ profile: UserProfile) {
        guard !isSigningOut else { return }
        let user = AuthUser(profile: profile)
        userCache.save(user)
        state = .signedIn(user)
        hasFreshProfile = true
        confirmConnectivity()
    }

    /// Something reached the server, so running imports can be resumed and start polling.
    private func confirmConnectivity() {
        guard !hasConfirmedConnectivity else { return }
        hasConfirmedConnectivity = true
        stopObservingConnectivity()
        letterboxdImportCoordinator.resumeIfNeeded()
        storygraphImportCoordinator.resumeIfNeeded()
        goodreadsImportCoordinator.resumeIfNeeded()
        myAnimeListImportCoordinator.resumeIfNeeded()
    }

    /// The one way a session ends, whoever decided it: the user, the launch refresh, the profile request, a
    /// screen or an import coordinator. The tokens go first, in one step through the refresher; then the
    /// remembered user, running imports and the shell all go, at once. Telling the server happens afterwards,
    /// in the background, so a slow network can't hold the sign-out up or leave a window where another sign-in
    /// could be undone by it.
    ///
    /// Single-flight: while one is under way, and once the app is signed out, another does nothing. Otherwise a
    /// late `onUnauthorized` could sign out somebody who had just signed in again.
    private func endSession(errorMessage: String? = nil) async {
        guard !isSigningOut else { return }
        if case .signedOut = state { return }
        isSigningOut = true
        defer { isSigningOut = false }

        profileTask?.cancel()
        profileTask = nil
        launchTask?.cancel()
        launchTask = nil
        launchWatcher?.cancel()
        launchWatcher = nil
        if let activationObserver {
            NotificationCenter.default.removeObserver(activationObserver)
            self.activationObserver = nil
        }
        stopObservingConnectivity()
        hasConfirmedConnectivity = false
        hasFreshProfile = false

        await repositories.auth.logout()

        userCache.clear()
        letterboxdImportCoordinator.clearFinishedJob()
        storygraphImportCoordinator.clearFinishedJob()
        goodreadsImportCoordinator.clearFinishedJob()
        myAnimeListImportCoordinator.clearFinishedJob()
        signedInEntryPoint = .home
        self.errorMessage = errorMessage
        state = .signedOut
    }

    // MARK: - Background profile

    private func loadProfileInBackground() {
        guard profileTask == nil else { return }
        profileTask = Task { [weak self] in
            await self?.loadProfile()
            self?.profileTask = nil
        }
    }

    private func loadProfile() async {
        do {
            let profile = try await repositories.profile.me()
            guard !Task.isCancelled, case .signedIn = state else { return }
            adopt(profile)
        } catch APIError.unauthorized {
            // The request already tried to refresh and the server rejected the session for good.
            guard !Task.isCancelled, case .signedIn = state else { return }
            await endSession()
        } catch {
            // Still offline, or the server is struggling: try again the next time the app becomes active.
        }
    }

    /// While the shell runs on the remembered user, any authenticated response confirms the network as well, so
    /// running imports needn't wait for a profile that may not come (a `/me/` that keeps failing while everything
    /// else works).
    private func observeConnectivity() {
        guard !hasConfirmedConnectivity, connectivityObserver == nil else { return }
        connectivityObserver = NotificationCenter.default.addObserver(
            forName: .authenticatedRequestSucceeded,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.isSigningOut, case .signedIn = self.state else { return }
                self.confirmConnectivity()
            }
        }
    }

    private func stopObservingConnectivity() {
        guard let connectivityObserver else { return }
        NotificationCenter.default.removeObserver(connectivityObserver)
        self.connectivityObserver = nil
    }

    private func observeActivation() {
        guard activationObserver == nil else { return }
        activationObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.retryProfileIfNeeded() }
        }
    }
}

private extension AuthUser {
    init(profile: UserProfile) {
        self.init(
            id: profile.id,
            username: profile.username,
            displayName: profile.displayName,
            isPrivate: profile.isPrivate,
            avatarUrl: profile.avatarUrl
        )
    }
}
