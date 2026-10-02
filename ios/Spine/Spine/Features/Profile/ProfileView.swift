import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

@MainActor
@Observable
final class ProfileViewModel {
    var profile: UserProfile?
    var recentActivityItems: [ActivityItem] = []
    var inProgressItems: [LibraryItem] = []
    var isLoading = true
    var isLoadingInProgress = false
    var isLoadingActivity = false
    var errorMessage: String?
    var activityErrorMessage: String?
    var inProgressErrorMessage: String?
    var hofErrorMessage: String?
    var savingHallOfFameSlots: Set<String> = []

    private let profileRepository: ProfileRepository
    private let trackingRepository: TrackingRepository
    private let activityRepository: ActivityRepository
    private let onUnauthorized: () -> Void
    private let username: String?
    private var requestGeneration = 0

    init(
        profileRepository: ProfileRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        username: String? = nil,
        onUnauthorized: @escaping () -> Void
    ) {
        self.profileRepository = profileRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.username = username
        self.onUnauthorized = onUnauthorized
    }

    func load() async {
        requestGeneration += 1
        let generation = requestGeneration
        isLoading = profile == nil
        errorMessage = nil
        isLoadingInProgress = false
        isLoadingActivity = false
        defer {
            if generation == requestGeneration {
                isLoading = false
            }
        }

        do {
            let loadedProfile: UserProfile
            if let username {
                loadedProfile = try await profileRepository.profile(username: username)
            } else {
                loadedProfile = try await profileRepository.me()
            }
            guard generation == requestGeneration else { return }
            profile = loadedProfile
            if username == nil {
                isLoadingInProgress = inProgressItems.isEmpty
            }
            isLoadingActivity = recentActivityItems.isEmpty
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
            return
        }

        if username == nil {
            await loadInProgressItems(generation: generation)
        }
        await loadRecentActivity(generation: generation)
    }

    func reload() async {
        await load()
    }

    func isSavingHallOfFameSlot(_ mediaType: String) -> Bool {
        savingHallOfFameSlots.contains(mediaType)
    }

    private func loadRecentActivity(generation: Int) async {
        guard let username = profile?.username else {
            recentActivityItems = []
            activityErrorMessage = nil
            isLoadingActivity = false
            return
        }

        isLoadingActivity = true
        activityErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingActivity = false
            }
        }

        do {
            let activity = try await activityRepository.userActivity(username: username, limit: 6)
            guard generation == requestGeneration else { return }
            recentActivityItems = activity
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            activityErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func loadInProgressItems(generation: Int) async {
        let mediaTypes = InProgressLibraryLoader.mediaTypes(from: profile)
        guard !mediaTypes.isEmpty else {
            inProgressItems = []
            inProgressErrorMessage = nil
            isLoadingInProgress = false
            return
        }

        isLoadingInProgress = true
        inProgressErrorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoadingInProgress = false
            }
        }

        do {
            let items = try await InProgressLibraryLoader.load(
                mediaTypes: mediaTypes,
                trackingRepository: trackingRepository,
                limit: 8
            )
            guard generation == requestGeneration else { return }
            inProgressItems = items
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            inProgressErrorMessage = error.localizedDescription
            handleUnauthorized(error)
        }
    }

    private func handleUnauthorized(_ error: Error) {
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }

    @discardableResult
    func setHallOfFameItem(mediaType: String, ref: MediaRef) async -> Bool {
        guard !savingHallOfFameSlots.contains(mediaType) else { return false }
        savingHallOfFameSlots.insert(mediaType)
        hofErrorMessage = nil
        defer { savingHallOfFameSlots.remove(mediaType) }

        do {
            let hof = try await profileRepository.setHallOfFameItem(mediaType: mediaType, ref: ref)
            profile = profile?.replacingHallOfFame(hof)
            return true
        } catch {
            hofErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return false
        }
    }

    @discardableResult
    func clearHallOfFameItem(mediaType: String) async -> Bool {
        guard !savingHallOfFameSlots.contains(mediaType) else { return false }
        savingHallOfFameSlots.insert(mediaType)
        hofErrorMessage = nil
        defer { savingHallOfFameSlots.remove(mediaType) }

        do {
            let hof = try await profileRepository.clearHallOfFameItem(mediaType: mediaType)
            profile = profile?.replacingHallOfFame(hof)
            return true
        } catch {
            hofErrorMessage = error.localizedDescription
            handleUnauthorized(error)
            return false
        }
    }
}

@MainActor
@Observable
final class ProfileSettingsViewModel {
    var profile: UserProfile?
    var settingsOptions = SettingsOptions(dateFormats: [], timeFormats: [], weekStartDays: [], quickWatchDates: [])
    var mediaTypes: [String] = []
    var displayName = ""
    var username = ""
    var bio = ""
    var pronouns = ""
    var location = ""
    var isPrivate = false
    var enabledMediaTypes: Set<String> = []
    var dateFormat = "Y-m-d"
    var timeFormat = "H:i"
    var weekStartDay = "monday"
    var quickWatchDate = "current_date"
    var releaseNotificationsEnabled = true
    var dailyDigestEnabled = true
    var oldPassword = ""
    var newPassword = ""
    var newPasswordConfirm = ""
    var isLoadingOptions = false
    var isSavingProfile = false
    var isSavingAvatar = false
    var isSavingPreferences = false
    var isSavingPassword = false
    var errorMessage: String?
    var successMessage: String?
    var fieldErrors: [String: String] = [:]

    private let profileRepository: ProfileRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(profileRepository: ProfileRepository, mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.profileRepository = profileRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func load(profile: UserProfile?) {
        self.profile = profile
        guard let profile else { return }
        displayName = profile.displayName
        username = profile.username
        bio = profile.bio ?? ""
        pronouns = profile.pronouns ?? ""
        location = profile.location ?? ""
        isPrivate = profile.isPrivate
        enabledMediaTypes = Set(profile.preferences.enabledMediaTypes)
        dateFormat = profile.preferences.dateFormat ?? "Y-m-d"
        timeFormat = profile.preferences.timeFormat ?? "H:i"
        weekStartDay = profile.preferences.weekStartDay ?? "monday"
        quickWatchDate = profile.preferences.quickWatchDate ?? "current_date"
        releaseNotificationsEnabled = profile.preferences.releaseNotificationsEnabled
        dailyDigestEnabled = profile.preferences.dailyDigestEnabled
    }

    func loadOptions() async {
        guard mediaTypes.isEmpty else { return }
        isLoadingOptions = true
        defer { isLoadingOptions = false }

        do {
            let meta = try await mediaRepository.meta()
            mediaTypes = meta.mediaTypes.filter { $0 != "episode" }
            settingsOptions = meta.settingsOptions ?? settingsOptions
        } catch {
            mediaTypes = Array(Set(APIConstants.fallbackMediaTypes).union(enabledMediaTypes)).sorted()
        }
    }

    var hasProfileChanges: Bool {
        guard let profile else { return false }
        return displayName != profile.displayName
            || username != profile.username
            || bio != (profile.bio ?? "")
            || pronouns != (profile.pronouns ?? "")
            || location != (profile.location ?? "")
            || isPrivate != profile.isPrivate
    }

    var hasPreferenceChanges: Bool {
        guard let profile else { return false }
        let preferences = profile.preferences
        return enabledMediaTypes != Set(preferences.enabledMediaTypes)
            || dateFormat != (preferences.dateFormat ?? "Y-m-d")
            || timeFormat != (preferences.timeFormat ?? "H:i")
            || weekStartDay != (preferences.weekStartDay ?? "monday")
            || quickWatchDate != (preferences.quickWatchDate ?? "current_date")
            || releaseNotificationsEnabled != preferences.releaseNotificationsEnabled
            || dailyDigestEnabled != preferences.dailyDigestEnabled
    }

    @discardableResult
    func saveProfile() async -> UserProfile? {
        let trimmedUsername = username.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedUsername.isEmpty else {
            fieldErrors = ["username": "Username is required."]
            return nil
        }

        isSavingProfile = true
        clearMessages()
        defer { isSavingProfile = false }

        do {
            let updated = try await profileRepository.updateProfile(ProfileUpdateRequest(
                username: trimmedUsername,
                displayName: displayName,
                bio: bio,
                pronouns: pronouns,
                location: location,
                isPrivate: isPrivate
            ))
            apply(updated, message: "Profile updated.")
            return updated
        } catch {
            handle(error)
            return nil
        }
    }

    @discardableResult
    func saveAvatar(from item: PhotosPickerItem?) async -> UserProfile? {
        guard let item else { return nil }
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else { return nil }
            let type = item.supportedContentTypes.first
            let mimeType = type?.preferredMIMEType ?? "image/jpeg"
            let fileExtension = type?.preferredFilenameExtension ?? "jpg"
            return await uploadAvatar(imageData: data, fileName: "avatar.\(fileExtension)", mimeType: mimeType)
        } catch {
            handle(error)
            return nil
        }
    }

    @discardableResult
    func uploadAvatar(imageData: Data, fileName: String, mimeType: String) async -> UserProfile? {
        isSavingAvatar = true
        clearMessages()
        defer { isSavingAvatar = false }

        do {
            let avatarUrl = try await profileRepository.uploadAvatar(imageData: imageData, fileName: fileName, mimeType: mimeType)
            guard let updated = profile?.replacingAvatarUrl(avatarUrl) else { return nil }
            apply(updated, message: "Photo updated.")
            return updated
        } catch {
            handle(error)
            return nil
        }
    }

    @discardableResult
    func removeAvatar() async -> UserProfile? {
        isSavingAvatar = true
        clearMessages()
        defer { isSavingAvatar = false }

        do {
            let avatarUrl = try await profileRepository.deleteAvatar()
            guard let updated = profile?.replacingAvatarUrl(avatarUrl) else { return nil }
            apply(updated, message: "Photo removed.")
            return updated
        } catch {
            handle(error)
            return nil
        }
    }

    @discardableResult
    func savePreferences() async -> UserProfile? {
        guard !enabledMediaTypes.isEmpty else {
            fieldErrors = ["enabled_media_types": "Enable at least one media type."]
            return nil
        }

        isSavingPreferences = true
        clearMessages()
        defer { isSavingPreferences = false }

        do {
            let preferences = try await profileRepository.updatePreferences(PreferencesUpdateRequest(
                enabledMediaTypes: enabledMediaTypes.sorted(),
                dateFormat: dateFormat,
                timeFormat: timeFormat,
                weekStartDay: weekStartDay,
                quickWatchDate: quickWatchDate,
                releaseNotificationsEnabled: releaseNotificationsEnabled,
                dailyDigestEnabled: dailyDigestEnabled
            ))
            guard let updated = profile?.replacingPreferences(preferences) else { return nil }
            apply(updated, message: "Preferences updated.")
            return updated
        } catch {
            handle(error)
            return nil
        }
    }

    func changePassword() async -> Bool {
        guard newPassword == newPasswordConfirm else {
            fieldErrors = ["new_password_confirm": "Passwords do not match."]
            return false
        }
        guard newPassword.count >= 8 else {
            fieldErrors = ["new_password": "Password must be at least 8 characters."]
            return false
        }

        isSavingPassword = true
        clearMessages()
        defer { isSavingPassword = false }

        do {
            try await profileRepository.changePassword(PasswordChangeRequest(
                oldPassword: oldPassword,
                newPassword: newPassword,
                newPasswordConfirm: newPasswordConfirm
            ))
            oldPassword = ""
            newPassword = ""
            newPasswordConfirm = ""
            successMessage = "Password updated."
            return true
        } catch {
            handle(error)
            return false
        }
    }

    private func apply(_ updated: UserProfile, message: String) {
        profile = updated
        load(profile: updated)
        successMessage = message
        NotificationCenter.default.post(name: .profileDidUpdate, object: nil, userInfo: ["profile": updated])
    }

    private func clearMessages() {
        errorMessage = nil
        successMessage = nil
        fieldErrors = [:]
    }

    private func handle(_ error: Error) {
        fieldErrors = APIValidationMessages.fieldErrors(from: error)
        errorMessage = fieldErrors.isEmpty ? error.localizedDescription : fieldErrors.values.joined(separator: "\n")
        if case APIError.unauthorized = error {
            onUnauthorized()
        }
    }
}

enum APIValidationMessages {
    static func fieldErrors(from error: Error) -> [String: String] {
        guard case let APIError.httpStatus(_, body) = error,
              let data = body?.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return [:]
        }
        if let error = json["error"] as? [String: Any],
           let fields = error["fields"] as? [String: Any] {
            return flatten(fields)
        }
        return flatten(json)
    }

    private static func flatten(_ fields: [String: Any]) -> [String: String] {
        fields.compactMapValues { value in
            if let messages = value as? [String] {
                return messages.first
            }
            if let messages = value as? [Any] {
                return messages.first.map { String(describing: $0) }
            }
            return value as? String
        }
    }
}

private struct ProfileTopSafeAreaInsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private let profileElementSpacing: CGFloat = 8

struct ProfileView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var viewModel: ProfileViewModel
    @State private var selectedRef: MediaRef?
    @State private var selectedPerson: PersonRef?
    @State private var isSettingsPresented = false
    @State private var hofPickerSlot: FavoriteSlot?
    @State private var heroScrollOffset: CGFloat = 0
    @State private var topSafeAreaInset: CGFloat = 0
    @State private var isProfileBackdropSearchPresented = false

    private let profileRepository: ProfileRepository
    private let mediaRepository: MediaRepository
    private let trackingRepository: TrackingRepository
    private let activityRepository: ActivityRepository
    private let diaryRepository: DiaryRepository
    private let listRepository: ListRepository
    private let peopleRepository: PeopleRepository
    private let importCoordinator: LetterboxdImportCoordinator?
    private let storygraphImportCoordinator: StoryGraphImportCoordinator?
    private let goodreadsImportCoordinator: GoodreadsImportCoordinator?
    private let myAnimeListImportCoordinator: MyAnimeListImportCoordinator?
    private let currentUserId: Int?
    private let onLogout: () -> Void
    private let onOpenDiary: () -> Void
    private let onOpenLibrary: (LibraryShelf) -> Void
    private let selectedTab: AppTab
    private let onSelectTab: (AppTab) -> Void
    private let onUnauthorized: () -> Void
    private let username: String?
    private let isPushedProfile: Bool

    private var isOwnProfile: Bool {
        username == nil
    }

    init(
        profileRepository: ProfileRepository,
        diaryRepository: DiaryRepository,
        mediaRepository: MediaRepository,
        trackingRepository: TrackingRepository,
        activityRepository: ActivityRepository,
        listRepository: ListRepository,
        peopleRepository: PeopleRepository,
        importCoordinator: LetterboxdImportCoordinator? = nil,
        storygraphImportCoordinator: StoryGraphImportCoordinator? = nil,
        goodreadsImportCoordinator: GoodreadsImportCoordinator? = nil,
        myAnimeListImportCoordinator: MyAnimeListImportCoordinator? = nil,
        currentUserId: Int? = nil,
        onLogout: @escaping () -> Void,
        onOpenDiary: @escaping () -> Void,
        onOpenLibrary: @escaping (LibraryShelf) -> Void,
        selectedTab: AppTab = .profile,
        onSelectTab: @escaping (AppTab) -> Void = { _ in },
        username: String? = nil,
        isPushedProfile: Bool = false,
        onUnauthorized: @escaping () -> Void = {}
    ) {
        _viewModel = State(initialValue: ProfileViewModel(
            profileRepository: profileRepository,
            trackingRepository: trackingRepository,
            activityRepository: activityRepository,
            username: username,
            onUnauthorized: onUnauthorized
        ))
        self.profileRepository = profileRepository
        self.mediaRepository = mediaRepository
        self.trackingRepository = trackingRepository
        self.activityRepository = activityRepository
        self.diaryRepository = diaryRepository
        self.listRepository = listRepository
        self.peopleRepository = peopleRepository
        self.importCoordinator = importCoordinator
        self.storygraphImportCoordinator = storygraphImportCoordinator
        self.goodreadsImportCoordinator = goodreadsImportCoordinator
        self.myAnimeListImportCoordinator = myAnimeListImportCoordinator
        self.currentUserId = currentUserId
        self.onLogout = onLogout
        self.onOpenDiary = onOpenDiary
        self.onOpenLibrary = onOpenLibrary
        self.selectedTab = selectedTab
        self.onSelectTab = onSelectTab
        self.onUnauthorized = onUnauthorized
        self.username = username
        self.isPushedProfile = isPushedProfile
    }

    var body: some View {
        if isOwnProfile && !isPushedProfile {
            NavigationStack {
                profileScreen
            }
        } else {
            profileScreen
        }
    }

    private var profileScreen: some View {
        profileContent
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .task {
                if viewModel.profile == nil {
                    await viewModel.load()
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: .letterboxdImportDidSucceed)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .storygraphImportDidSucceed)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .goodreadsImportDidSucceed)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .myAnimeListImportDidSucceed)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
            }
            .onReceive(NotificationCenter.default.publisher(for: .mediaStateDidChange)) { _ in
                Swift.Task<Void, Never> { await viewModel.reload() }
            }
            .fullScreenCover(item: $selectedRef, onDismiss: { selectedRef = nil }) { ref in
                MediaDetailView(
                    ref: ref,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    listRepository: listRepository,
                    peopleRepository: peopleRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
            .fullScreenCover(item: $selectedPerson, onDismiss: { selectedPerson = nil }) { ref in
                PersonDetailView(
                    ref: ref,
                    peopleRepository: peopleRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    listRepository: listRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            }
            .sheet(isPresented: $isSettingsPresented) {
                if let importCoordinator,
                   let storygraphImportCoordinator,
                   let goodreadsImportCoordinator,
                   let myAnimeListImportCoordinator {
                    ProfileSettingsSheet(
                        profile: viewModel.profile,
                        profileRepository: profileRepository,
                        mediaRepository: mediaRepository,
                        onProfileUpdated: { updated in
                            viewModel.profile = updated
                            Swift.Task<Void, Never> { await viewModel.reload() }
                        },
                        onUnauthorized: onUnauthorized,
                        importCoordinator: importCoordinator,
                        storygraphImportCoordinator: storygraphImportCoordinator,
                        goodreadsImportCoordinator: goodreadsImportCoordinator,
                        myAnimeListImportCoordinator: myAnimeListImportCoordinator,
                        onLogout: onLogout
                    )
                }
            }
            .sheet(item: $hofPickerSlot) { slot in
                HallOfFamePickerSheet(
                    slot: slot,
                    mediaRepository: mediaRepository,
                    isSaving: viewModel.isSavingHallOfFameSlot(slot.id),
                    errorMessage: viewModel.hofErrorMessage,
                    onSelect: { media in
                        await viewModel.setHallOfFameItem(mediaType: slot.id, ref: media.ref)
                    },
                    onClear: clearHallOfFameAction(for: slot),
                    onUnauthorized: onUnauthorized
                )
            }
            .fullScreenCover(isPresented: $isProfileBackdropSearchPresented) {
                ProfileBackdropSearchView(
                    mediaRepository: mediaRepository,
                    profileRepository: profileRepository,
                    currentBackdropURL: viewModel.profile?.profileBackdropUrl,
                    onUnauthorized: onUnauthorized
                ) { response in
                    applyProfileBackdrop(response)
                }
            }
            .alert("Hall of Fame Update Failed", isPresented: hofErrorBinding) {
                Button("OK") {
                    viewModel.hofErrorMessage = nil
                }
            } message: {
                Text(viewModel.hofErrorMessage ?? "")
            }
    }

    private var profileContent: some View {
        ZStack(alignment: .top) {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                Group {
                    if viewModel.isLoading, viewModel.profile == nil {
                        ProgressView()
                            .tint(.white)
                            .frame(maxWidth: .infinity, minHeight: 520)
                    } else if let error = viewModel.errorMessage, viewModel.profile == nil {
                        ContentUnavailableView("Could not load profile", systemImage: "exclamationmark.triangle", description: Text(error))
                            .foregroundStyle(.white)
                            .padding(.top, 120)
                    } else if let profile = viewModel.profile {
                        VStack(alignment: .leading, spacing: 0) {
                            hero(
                                profile,
                                collapseProgress: reduceMotion ? 0 : ProfileHeroCollapse.progress(for: heroScrollOffset),
                                layoutProgress: reduceMotion ? 0 : ProfileHeroCollapse.layoutProgress(for: heroScrollOffset)
                            )
                            if isOwnProfile {
                                inProgressSection
                                    .padding(.horizontal, 16)
                                    .padding(.top, 20)
                            }
                            activitySection
                                .padding(.horizontal, 16)
                                .padding(.top, 20)
                            if isOwnProfile {
                                profileMenuSection(profile.counts)
                                    .padding(.horizontal, 16)
                                    .padding(.top, 20)
                            }
                        }
                        .padding(.bottom, 100)
                    }
                }
                .spineContentTransition(value: contentPhase)
            }
            .scrollContentBackground(.hidden)
            .ignoresSafeArea(edges: .top)
            .refreshable {
                await viewModel.reload()
            }
            .onScrollGeometryChange(for: CGFloat.self) { geometry in
                max(0, geometry.contentOffset.y)
            } action: { _, offset in
                heroScrollOffset = offset
            }

            if isPushedProfile {
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .heavy))
                        .foregroundStyle(.white)
                        .frame(width: 42, height: 42)
                        .background(.black.opacity(0.34), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Back")
                .padding(.top, topSafeAreaInset + 6)
                .padding(.leading, 16)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }

            if isOwnProfile {
                settingsButton
                    .padding(.top, topSafeAreaInset + 6)
                    .padding(.trailing, 16)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
            } else if !isPushedProfile {
                EmptyView()
            }
        }
        .background {
            GeometryReader { proxy in
                Color.clear.preference(key: ProfileTopSafeAreaInsetKey.self, value: proxy.safeAreaInsets.top)
            }
        }
        .onPreferenceChange(ProfileTopSafeAreaInsetKey.self) { topSafeAreaInset = $0 }
    }

    private func clearHallOfFameAction(for slot: FavoriteSlot) -> (() async -> Bool)? {
        guard slot.item != nil else { return nil }
        return {
            await viewModel.clearHallOfFameItem(mediaType: slot.id)
        }
    }

    private var hofErrorBinding: Binding<Bool> {
        Binding(
            get: { viewModel.hofErrorMessage != nil && hofPickerSlot == nil },
            set: { if !$0 { viewModel.hofErrorMessage = nil } }
        )
    }

    private var settingsButton: some View {
        Button {
            isSettingsPresented = true
        } label: {
            Image(systemName: "gearshape.fill")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.34), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Settings")
    }

    private func hero(_ profile: UserProfile, collapseProgress: CGFloat, layoutProgress: CGFloat) -> some View {
        let allSlots = favoriteSlots(from: profile)
        let backdropURL = profileBackdropURL(from: profile)
        let musicClearance = HallOfFameCrownLayout.aboveMusicClearance(
            for: allSlots,
            collapseProgress: layoutProgress
        )
        let crownHeight = ProfileHeroBackdropLayout.crownHeight(for: layoutProgress) + musicClearance
        let backdropContentOffset = backdropURL == nil ? 0 : ProfileHeroBackdropLayout.contentTopOffset

        return VStack(spacing: profileElementSpacing) {
            VStack(spacing: 8) {
                ZStack(alignment: .top) {
                    HallOfFameCrownView(
                        slots: allSlots,
                        savingSlotIDs: viewModel.savingHallOfFameSlots,
                        collapseProgress: collapseProgress
                    ) { slot in
                        if let item = slot.item {
                            selectedRef = item.ref
                        }
                    } onEmptyTap: { slot in
                        if isOwnProfile {
                            hofPickerSlot = slot
                        }
                    } onFilledLongPress: { slot in
                        if isOwnProfile {
                            hofPickerSlot = slot
                        }
                    }
                    .offset(y: -21 * collapseProgress)
                    .zIndex(0)

                    avatar(profile)
                        .zIndex(1)
                }
                .padding(.top, musicClearance)
                .frame(height: crownHeight)

                if allSlots.isEmpty {
                    Text("No favorites yet")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.4))
                }
            }

            VStack(spacing: 0) {
                Text(profile.displayName)
                    .font(.system(size: 34, weight: .black))
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.72)
                    .shadow(color: .black.opacity(backdropURL == nil ? 0 : 0.32), radius: 12, y: 6)

                HStack(spacing: 8) {
                    Text("@\(profile.username)")
                    if profile.isPrivate {
                        Label("Private", systemImage: "lock.fill")
                            .labelStyle(.titleAndIcon)
                    }
                }
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white.opacity(0.62))
                .shadow(color: .black.opacity(backdropURL == nil ? 0 : 0.26), radius: 8, y: 4)
            }
            .padding(.top, 3)

            if let bio = profile.bio?.trimmedNonEmpty {
                Text(bio)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(.white.opacity(0.78))
                    .multilineTextAlignment(.center)
                    .lineLimit(4)
                    .padding(.horizontal, 10)
                    .shadow(color: .black.opacity(backdropURL == nil ? 0 : 0.22), radius: 8, y: 4)
            }

            if let location = profile.location?.trimmedNonEmpty {
                Label(location, systemImage: "mappin.and.ellipse")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))
                    .shadow(color: .black.opacity(backdropURL == nil ? 0 : 0.22), radius: 8, y: 4)
            }

            statsGrid(profile.counts)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 20)
        .padding(.top, backdropURL == nil ? topSafeAreaInset + 28 : topSafeAreaInset + 74 + backdropContentOffset)
        .background(alignment: .top) {
            if let backdropURL {
                ProfileBackdropArtwork(urlString: backdropURL)
                    .frame(height: topSafeAreaInset + ProfileHeroBackdropLayout.backdropHeight)
                    .offset(y: BackdropLayout.topOffset)
                    .onLongPressGesture {
                        guard isOwnProfile else { return }
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        isProfileBackdropSearchPresented = true
                    }
            }
        }
    }

    private func profileBackdropURL(from profile: UserProfile) -> String? {
        if let profileBackdropUrl = profile.profileBackdropUrl?.trimmedNonEmpty {
            return profileBackdropUrl
        }
        guard let movie = profile.hof["movie"] ?? nil else { return nil }
        return movie.displayBackdropURL
    }

    private func applyProfileBackdrop(_ response: ProfileBackdropSaveResponse) {
        guard let updated = viewModel.profile?.replacingProfileBackdrop(response) else { return }
        viewModel.profile = updated
    }

    private func avatar(_ profile: UserProfile) -> some View {
        SpineAsyncImage(url: URL(string: profile.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image
                    .resizable()
                    .scaledToFill()
            } else {
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .foregroundStyle(.white.opacity(0.36))
                    .padding(10)
            }
        }
        .frame(width: 128, height: 128)
        .background(.white.opacity(0.08), in: Circle())
        .clipShape(Circle())
        .overlay {
            Circle()
                .stroke(.white.opacity(0.18), lineWidth: 1)
        }
        .shadow(color: .black.opacity(0.44), radius: 22, y: 12)
        .accessibilityLabel(profile.displayName)
    }

    private struct ProfileBackdropArtwork: View {
        let urlString: String

        var body: some View {
            GeometryReader { proxy in
                ZStack {
                    SpineAsyncImage(url: URL(string: urlString)) { phase in
                        switch phase {
                        case let .success(image):
                            image
                                .resizable()
                                .scaledToFill()
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .clipped()
                        default:
                            Color.clear
                        }
                    }

                    LinearGradient(
                        stops: [
                            .init(color: .black.opacity(0.42), location: 0),
                            .init(color: .black.opacity(0.18), location: 0.36),
                            .init(color: .clear, location: 0.72),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                }
                .mask(
                    LinearGradient(
                        stops: [
                            .init(color: .white, location: 0),
                            .init(color: .white, location: 0.52),
                            .init(color: .white.opacity(0.35), location: 0.78),
                            .init(color: .clear, location: 1),
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
            }
            .clipped()
        }
    }

    private enum ProfileHeroBackdropLayout {
        static let backdropHeight: CGFloat = 352.34375
        static let contentTopOffset: CGFloat = 44

        static func crownHeight(for collapseProgress: CGFloat) -> CGFloat {
            286 - 178 * collapseProgress
        }
    }

    private func statsGrid(_ counts: ProfileCounts) -> some View {
        GlassEffectContainer(spacing: 3.5) {
            HStack(spacing: 7) {
                if isOwnProfile {
                    Button(action: onOpenDiary) {
                        ProfileStatChip(
                            value: counts.diaryEntries,
                            title: "Logs",
                            systemName: "calendar",
                            isInteractive: true
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("profile.logs")
                    .accessibilityHint("Opens Diary")
                } else {
                    ProfileStatChip(value: counts.diaryEntries, title: "Logs", systemName: "calendar")
                }

                ProfileStatChip(value: counts.followers, title: "Followers", systemName: "person.2")
                ProfileStatChip(value: counts.following, title: "Following", systemName: "person.crop.circle.badge.checkmark")

                if isOwnProfile {
                    NavigationLink {
                        ProfileListsView(
                            profileRepository: profileRepository,
                            listRepository: listRepository,
                            peopleRepository: peopleRepository,
                            mediaRepository: mediaRepository,
                            trackingRepository: trackingRepository,
                            diaryRepository: diaryRepository,
                            activityRepository: activityRepository,
                            importCoordinator: importCoordinator,
                            storygraphImportCoordinator: storygraphImportCoordinator,
                            goodreadsImportCoordinator: goodreadsImportCoordinator,
                            myAnimeListImportCoordinator: myAnimeListImportCoordinator,
                            currentUserId: currentUserId ?? viewModel.profile?.id,
                            onLogout: onLogout,
                            onOpenDiary: onOpenDiary,
                            onOpenLibrary: onOpenLibrary,
                            selectedTab: selectedTab,
                            onSelectTab: onSelectTab,
                            onUnauthorized: onUnauthorized
                        )
                    } label: {
                        ProfileStatChip(
                            value: counts.lists,
                            title: "Lists",
                            systemName: "list.bullet.rectangle",
                            isInteractive: true
                        )
                    }
                    .buttonStyle(.plain)
                } else {
                    ProfileStatChip(value: counts.lists, title: "Lists", systemName: "list.bullet.rectangle")
                }
            }
        }
    }

    private var activitySection: some View {
        ProfileSection(title: "Recent Activity") {
            Group {
                if viewModel.isLoadingActivity, viewModel.recentActivityItems.isEmpty {
                    ProfileRailLoadingView()
                } else if let activityError = viewModel.activityErrorMessage, viewModel.recentActivityItems.isEmpty {
                    EmptyProfileCard(title: activityError, systemName: "exclamationmark.triangle")
                } else if ProfileRecentActivityRailModel.items(from: viewModel.recentActivityItems).isEmpty {
                    EmptyProfileCard(title: "No activity yet", systemName: "bolt")
                } else {
                    RecentActivityRail(items: viewModel.recentActivityItems) { item in
                        switch item.subject {
                        case let .media(media):
                            selectedRef = media.ref
                        case let .person(person):
                            selectedPerson = person.ref
                        }
                    }
                }
            }
            .spineContentTransition(value: activityPhase)
        }
    }

    private func profileMenuSection(_ counts: ProfileCounts) -> some View {
        let shape = RoundedRectangle(cornerRadius: ProfileMenuRow.groupCornerRadius, style: .continuous)

        return VStack(spacing: 0) {
            ForEach(Array(ProfileMenuDestination.allCases.enumerated()), id: \.element) { index, destination in
                profileMenuLink(
                    destination,
                    count: destination.count(from: counts),
                    showsDivider: index != ProfileMenuDestination.allCases.count - 1
                )
            }
        }
        .clipShape(shape)
        .modifier(ProfileMenuGroupSurface(shape: shape))
    }

    @ViewBuilder
    private func profileMenuLink(_ destination: ProfileMenuDestination, count: Int?, showsDivider: Bool) -> some View {
        switch destination {
        case .library:
            Button {
                onOpenLibrary(.tracked)
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .diary:
            Button {
                onOpenDiary()
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .stats:
            NavigationLink {
                StatsView(
                    profileRepository: profileRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    listRepository: listRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                ProfileMenuRow(title: destination.title, count: nil, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .reviews:
            NavigationLink {
                ProfileReviewsView(
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .lists:
            NavigationLink {
                ProfileListsView(
                    profileRepository: profileRepository,
                    listRepository: listRepository,
                    peopleRepository: peopleRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    diaryRepository: diaryRepository,
                    activityRepository: activityRepository,
                    importCoordinator: importCoordinator,
                    storygraphImportCoordinator: storygraphImportCoordinator,
                    goodreadsImportCoordinator: goodreadsImportCoordinator,
                    myAnimeListImportCoordinator: myAnimeListImportCoordinator,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    onLogout: onLogout,
                    onOpenDiary: onOpenDiary,
                    onOpenLibrary: onOpenLibrary,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .planned:
            Button {
                onOpenLibrary(.planning)
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .likes:
            NavigationLink {
                ProfileLikesView(
                    profileRepository: profileRepository,
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        case .tags:
            NavigationLink {
                ProfileTagsView(
                    diaryRepository: diaryRepository,
                    mediaRepository: mediaRepository,
                    trackingRepository: trackingRepository,
                    currentUserId: currentUserId ?? viewModel.profile?.id,
                    selectedTab: selectedTab,
                    onSelectTab: onSelectTab,
                    onUnauthorized: onUnauthorized
                )
            } label: {
                ProfileMenuRow(title: destination.title, count: count, showsDivider: showsDivider)
            }
            .buttonStyle(ProfileMenuRowButtonStyle())
        }
    }

    private var inProgressSection: some View {
        ProfileSection(title: "In Progress") {
            Group {
                if viewModel.isLoadingInProgress, viewModel.inProgressItems.isEmpty {
                    ProfileRailLoadingView()
                } else if let inProgressError = viewModel.inProgressErrorMessage, viewModel.inProgressItems.isEmpty {
                    EmptyProfileCard(title: inProgressError, systemName: "exclamationmark.triangle")
                } else if viewModel.inProgressItems.isEmpty {
                    EmptyProfileCard(title: "Nothing in progress yet", systemName: "play.circle")
                } else {
                    InProgressRail(items: viewModel.inProgressItems) { item in
                        selectedRef = item.media.ref
                    }
                }
            }
            .spineContentTransition(value: inProgressPhase)
        }
    }

    private var contentPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoading,
            hasContent: viewModel.profile != nil,
            hasError: viewModel.errorMessage != nil
        )
    }

    private var activityPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingActivity,
            hasContent: !viewModel.recentActivityItems.isEmpty,
            hasError: viewModel.activityErrorMessage != nil
        )
    }

    private var inProgressPhase: SpineContentPhase {
        .resolve(
            isLoading: viewModel.isLoadingInProgress,
            hasContent: !viewModel.inProgressItems.isEmpty,
            hasError: viewModel.inProgressErrorMessage != nil
        )
    }

    private func favoriteSlots(from profile: UserProfile) -> [FavoriteSlot] {
        ProfileFavorites.slots(from: profile.hof, enabledMediaTypes: profile.preferences.enabledMediaTypes)
    }

    private func favoriteSlotRank(_ key: String) -> Int {
        ProfileFavorites.rank(key)
    }
}

struct ProfileFavorites {
    private static let defaultSlotKeys = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]

    static func slots(from hof: [String: MediaSummary?], enabledMediaTypes: [String] = []) -> [FavoriteSlot] {
        let enabled = enabledMediaTypes.filter { defaultSlotKeys.contains($0) }
        let keys = enabled.isEmpty ? defaultSlotKeys : enabled
        return keys.sorted { lhs, rhs in
            let leftRank = rank(lhs)
            let rightRank = rank(rhs)
            return leftRank == rightRank ? lhs < rhs : leftRank < rightRank
        }.map { key in
            FavoriteSlot(id: key, title: key.profileSlotTitle, item: hof[key] ?? nil)
        }
    }

    static func rank(_ key: String) -> Int {
        let normalized = key.lowercased()
        let order = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music", "boardgame"]
        return order.firstIndex { normalized.contains($0) } ?? order.count
    }
}

struct FavoriteSlot: Identifiable {
    let id: String
    let title: String
    let item: MediaSummary?
}

enum ProfileHeroCollapse {
    static let scrollDistance: CGFloat = 260
    static let layoutScrollDistance: CGFloat = 340

    static func progress(for scrollOffset: CGFloat) -> CGFloat {
        easedProgress(for: scrollOffset, over: scrollDistance)
    }

    static func layoutProgress(for scrollOffset: CGFloat) -> CGFloat {
        clampedProgress(for: scrollOffset, over: layoutScrollDistance)
    }

    private static func easedProgress(for scrollOffset: CGFloat, over distance: CGFloat) -> CGFloat {
        let progress = clampedProgress(for: scrollOffset, over: distance)
        return progress * progress * (3 - 2 * progress)
    }

    private static func clampedProgress(for scrollOffset: CGFloat, over distance: CGFloat) -> CGFloat {
        min(1, max(0, scrollOffset / distance))
    }
}

@MainActor
@Observable
private final class HallOfFamePickerViewModel {
    var query = ""
    var results: [MediaSummary] = []
    var isLoading = false
    var errorMessage: String?
    var resultRevision = 0

    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void
    private var requestGeneration = 0

    init(mediaRepository: MediaRepository, onUnauthorized: @escaping () -> Void) {
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
    }

    func invalidateSearch() {
        requestGeneration += 1
        isLoading = false
    }

    func search(mediaType: String) async {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        requestGeneration += 1
        let generation = requestGeneration
        guard !trimmed.isEmpty else {
            results = []
            errorMessage = nil
            isLoading = false
            return
        }

        isLoading = true
        errorMessage = nil
        defer {
            if generation == requestGeneration {
                isLoading = false
            }
        }

        do {
            let found = try await mediaRepository.search(query: trimmed, mediaType: mediaType)
            guard generation == requestGeneration,
                  trimmed == query.trimmingCharacters(in: .whitespacesAndNewlines) else { return }
            results = found
            resultRevision += 1
        } catch is CancellationError {
            return
        } catch {
            guard generation == requestGeneration else { return }
            results = []
            errorMessage = error.localizedDescription
            if case APIError.unauthorized = error {
                onUnauthorized()
            }
        }
    }
}

private struct HallOfFamePickerSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: HallOfFamePickerViewModel
    @State private var savingMediaID: String?
    @State private var isClearing = false

    let slot: FavoriteSlot
    let isSaving: Bool
    let errorMessage: String?
    let onSelect: (MediaSummary) async -> Bool
    let onClear: (() async -> Bool)?

    init(
        slot: FavoriteSlot,
        mediaRepository: MediaRepository,
        isSaving: Bool,
        errorMessage: String?,
        onSelect: @escaping (MediaSummary) async -> Bool,
        onClear: (() async -> Bool)?,
        onUnauthorized: @escaping () -> Void
    ) {
        self.slot = slot
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.onSelect = onSelect
        self.onClear = onClear
        _viewModel = State(initialValue: HallOfFamePickerViewModel(mediaRepository: mediaRepository, onUnauthorized: onUnauthorized))
    }

    var body: some View {
        NavigationStack {
            List {
                if let errorMessage {
                    Section {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    }
                }

                if let current = slot.item {
                    Section("Current") {
                        HallOfFamePickerRow(media: current)
                        if onClear != nil {
                            Button(role: .destructive) {
                                Swift.Task<Void, Never> { await clear() }
                            } label: {
                                if isClearing {
                                    Label("Removing", systemImage: "clock")
                                } else {
                                    Label("Remove Favorite", systemImage: "trash")
                                }
                            }
                            .disabled(isSaving || isClearing)
                        }
                    }
                }

                Section("Results") {
                    if viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        ContentUnavailableView("Search \(slot.title)", systemImage: "magnifyingglass")
                            .listRowBackground(Color.clear)
                    } else if !viewModel.results.isEmpty {
                        ForEach(viewModel.results) { media in
                            Button {
                                Swift.Task<Void, Never> { await select(media) }
                            } label: {
                                HStack(spacing: 10) {
                                    HallOfFamePickerRow(media: media)
                                    if savingMediaID == media.id {
                                        ProgressView()
                                    }
                                }
                            }
                            .buttonStyle(.plain)
                            .disabled(isSaving || savingMediaID != nil)
                        }
                    } else if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView()
                            Spacer()
                        }
                    } else if let error = viewModel.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.red)
                    } else if viewModel.results.isEmpty {
                        ContentUnavailableView("No Results", systemImage: "magnifyingglass")
                            .listRowBackground(Color.clear)
                    }
                }
                .spineContentTransition(value: resultsTransitionKey)
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle("\(slot.title) Favorite")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $viewModel.query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search \(slot.title)")
            .task(id: viewModel.query) {
                viewModel.invalidateSearch()
                try? await Task.sleep(for: .milliseconds(300))
                guard !Task.isCancelled else { return }
                await viewModel.search(mediaType: slot.id)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }

    private var resultsPhase: SpineContentPhase {
        let hasQuery = !viewModel.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return .resolve(
            isLoading: hasQuery && viewModel.isLoading,
            hasContent: hasQuery && !viewModel.results.isEmpty,
            hasError: hasQuery && viewModel.errorMessage != nil
        )
    }

    private var resultsTransitionKey: ResultsTransitionKey {
        ResultsTransitionKey(phase: resultsPhase, revision: viewModel.resultRevision)
    }

    private struct ResultsTransitionKey: Hashable {
        let phase: SpineContentPhase
        let revision: Int
    }

    private func select(_ media: MediaSummary) async {
        savingMediaID = media.id
        let didSave = await onSelect(media)
        savingMediaID = nil
        if didSave {
            dismiss()
        }
    }

    private func clear() async {
        guard let onClear else { return }
        isClearing = true
        let didClear = await onClear()
        isClearing = false
        if didClear {
            dismiss()
        }
    }
}

private struct HallOfFamePickerRow: View {
    let media: MediaSummary

    var body: some View {
        HStack(spacing: 12) {
            MediaArtwork(
                url: media.displayPosterURL,
                title: media.title,
                slot: .searchRow,
                mediaType: media.ref.mediaType,
                orientation: media.posterOrientation
            )

            VStack(alignment: .leading, spacing: 4) {
                Text(media.title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if let subtitle = media.searchResultSubtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 0)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

}

private struct ProfileStatChip: View {
    private static let cornerRadius: CGFloat = 10.5

    let value: Int
    let title: String
    let systemName: String
    var isInteractive = false

    var body: some View {
        glassSurface
            .overlay {
                RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
                    .strokeBorder(.white.opacity(0.18), lineWidth: 1)
            }
            .shadow(color: .black.opacity(0.12), radius: 3.5, y: 1.75)
            .contentShape(RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(value.formatted()) \(title)")
    }

    @ViewBuilder
    private var glassSurface: some View {
        if isInteractive {
            content
                .glassEffect(
                    .regular.tint(.white.opacity(0.06)).interactive(),
                    in: RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
                )
        } else {
            content
                .glassEffect(
                    .regular.tint(.white.opacity(0.06)),
                    in: RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
                )
        }
    }

    private var content: some View {
        VStack(spacing: 2.5) {
            HStack(spacing: 3.5) {
                Image(systemName: systemName)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.56))

                Text(value.formatted())
                    .font(.system(size: 15.5, weight: .bold))
                    .foregroundStyle(.white.opacity(0.94))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.62)
                    .contentTransition(.numericText())
            }

            Text(title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.white.opacity(0.5))
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity, minHeight: 56)
        .background(
            .black.opacity(0.15),
            in: RoundedRectangle(cornerRadius: Self.cornerRadius, style: .continuous)
        )
    }
}

enum ProfileMenuDestination: CaseIterable, Hashable {
    case library
    case diary
    case reviews
    case lists
    case planned
    case likes
    case tags
    case stats

    var title: String {
        switch self {
        case .library: "Library"
        case .diary: "Diary"
        case .stats: "Stats"
        case .reviews: "Reviews"
        case .lists: "Lists"
        case .planned: "Planned"
        case .likes: "Likes"
        case .tags: "Tags"
        }
    }

    func count(from counts: ProfileCounts) -> Int? {
        switch self {
        case .library: counts.libraryItems
        case .diary: counts.diaryEntries
        case .stats: nil
        case .reviews: counts.reviews
        case .lists: counts.lists
        case .planned: counts.plannedItems
        case .likes: counts.likedItems
        case .tags: counts.tags
        }
    }
}

struct ProfileMenuRow: View {
    static let groupCornerRadius: CGFloat = 22
    static let horizontalPadding: CGFloat = 16
    static let minimumHeight: CGFloat = 48

    let title: String
    let count: Int?
    var showsDivider = true

    var body: some View {
        HStack(spacing: 12) {
            Text(title)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(.white.opacity(0.78))
                .lineLimit(1)
                .minimumScaleFactor(0.78)

            Spacer(minLength: 12)

            if let count {
                Text(count.formatted())
                    .font(.system(size: 16, weight: .medium))
                    .foregroundStyle(.white.opacity(0.42))
                    .lineLimit(1)
                    .minimumScaleFactor(0.78)
            }

            Image(systemName: "chevron.right")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white.opacity(0.26))
        }
        .frame(minHeight: Self.minimumHeight)
        .padding(.horizontal, Self.horizontalPadding)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            if showsDivider {
                Rectangle()
                    .fill(.white.opacity(0.12))
                    .frame(height: 0.5)
                    .padding(.leading, Self.horizontalPadding)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(count.map { "\(title), \($0)" } ?? title)
    }
}

/// Inset-grouped surface for the profile menu: Liquid Glass on iOS 26, a
/// lighter dark card with a faint border before that.
private struct ProfileMenuGroupSurface: ViewModifier {
    let shape: RoundedRectangle

    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            content
                .glassEffect(.regular, in: shape)
        } else {
            content
                .background(.white.opacity(0.06), in: shape)
                .overlay {
                    shape.strokeBorder(.white.opacity(0.06), lineWidth: 1)
                }
        }
    }
}

/// Fills the whole row while pressed; the group clips it to its rounded corners.
private struct ProfileMenuRowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        ProfileMenuRowPressHighlight(configuration: configuration)
    }
}

private struct ProfileMenuRowPressHighlight: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    let configuration: ButtonStyleConfiguration

    var body: some View {
        configuration.label
            .background {
                Color.white
                    .opacity(configuration.isPressed ? 0.12 : 0)
            }
            .animation(
                configuration.isPressed ? nil : SpineMotion.animation(reduceMotion: reduceMotion),
                value: configuration.isPressed
            )
    }
}

private struct ProfileSection<Content: View>: View {
    let title: String
    let action: (() -> Void)?
    @ViewBuilder let content: () -> Content

    init(
        title: String,
        action: (() -> Void)? = nil,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.title = title
        self.action = action
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: profileElementSpacing) {
            HStack {
                if let action {
                    Button(action: action) {
                        HStack(spacing: 5) {
                            sectionTitle
                            Image(systemName: "chevron.right")
                                .font(.system(size: 10, weight: .black))
                                .foregroundStyle(.white.opacity(0.58))
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(title)
                } else {
                    sectionTitle
                }
                Spacer()
            }

            content()
        }
    }

    private var sectionTitle: some View {
        Text(title.uppercased())
            .font(.system(size: 13, weight: .black))
            .foregroundStyle(.white.opacity(0.58))
            .tracking(0.8)
    }
}

struct ProfileRecentActivityRailItem: Identifiable {
    enum Subject {
        case media(MediaSummary)
        case person(ActivityPersonSnapshot)
    }

    let activity: ActivityItem
    let subject: Subject

    var id: Int { activity.id }
}

enum ProfileRecentActivityRailModel {
    static func items(from activities: [ActivityItem]) -> [ProfileRecentActivityRailItem] {
        activities.compactMap { activity in
            if let person = activity.person {
                return ProfileRecentActivityRailItem(activity: activity, subject: .person(person))
            }
            if let media = activity.media {
                return ProfileRecentActivityRailItem(activity: activity, subject: .media(media))
            }
            return nil
        }
    }

    static func progressDeltaText(for activity: ActivityItem, media: MediaSummary) -> String? {
        guard
            activity.type == "progress_updated",
            let previous = activity.object.previous,
            let current = activity.object.current
        else {
            return nil
        }

        return ProgressChangeState(
            id: activity.object.id,
            previous: previous,
            current: current,
            createdAt: activity.createdAt
        )
        .compactDeltaText(preferredMode: ProgressDisplayPreferences.mode(for: media.ref))
    }

    static func rating(for activity: ActivityItem) -> String? {
        clean(activity.object.rating)
    }

    static func isLikedDiary(_ activity: ActivityItem) -> Bool {
        isDiary(activity) && activity.object.liked == true
    }

    static func fallbackLabel(for activity: ActivityItem) -> String? {
        switch activity.type {
        case "list_created":
            clean(activity.object.name)
        case "list_item_added":
            clean(activity.object.name) ?? "List"
        default:
            nil
        }
    }

    static func isDiary(_ activity: ActivityItem) -> Bool {
        activity.type == "diary_created" || activity.type == "diary_updated"
    }

    private static func clean(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct RecentActivityRail: View {
    let items: [ActivityItem]
    let action: (ProfileRecentActivityRailItem) -> Void

    private var visibleItems: [ProfileRecentActivityRailItem] {
        ProfileRecentActivityRailModel.items(from: items)
    }

    var body: some View {
        GeometryReader { proxy in
            let itemWidth = PosterSlot.profileRail.size.width
            let minimumSpacing: CGFloat = 4
            let maximumVisibleCount = min(6, visibleItems.count)
            let visibleCount = max(1, min(maximumVisibleCount, Int((proxy.size.width + minimumSpacing) / (itemWidth + minimumSpacing))))
            let spacing = visibleCount > 1
                ? min(10, max(minimumSpacing, (proxy.size.width - itemWidth * CGFloat(visibleCount)) / CGFloat(visibleCount - 1)))
                : 0
            HStack(alignment: .top, spacing: spacing) {
                ForEach(Array(visibleItems.prefix(visibleCount))) { item in
                    RecentActivityPoster(item: item) {
                        action(item)
                    }
                    .frame(width: itemWidth)
                }
                Spacer(minLength: 0)
            }
        }
        .frame(height: PosterSlot.profileRail.size.height + 7 + 12.5)
    }
}

private struct InProgressRail: View {
    let items: [LibraryItem]
    let action: (LibraryItem) -> Void

    var body: some View {
        GeometryReader { proxy in
            let spacing: CGFloat = 10
            let itemWidth = PosterSlot.profileRail.size.width
            let visibleCount = max(3, min(5, Int((proxy.size.width + spacing) / (itemWidth + spacing))))
            HStack(alignment: .top, spacing: spacing) {
                ForEach(Array(items.prefix(visibleCount))) { item in
                    InProgressPoster(item: item) {
                        action(item)
                    }
                    .frame(width: itemWidth)
                }
                Spacer(minLength: 0)
            }
        }
        .frame(height: PosterSlot.profileRail.size.height)
    }
}

private struct InProgressPoster: View {
    let item: LibraryItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            MediaArtwork(
                url: item.media.displayPosterURL,
                title: item.media.title,
                slot: .profileRail,
                mediaType: item.media.ref.mediaType,
                orientation: item.media.posterOrientation
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("View \(item.media.title)")
    }
}

private struct ProfileRailLoadingView: View {
    var body: some View {
        HStack(spacing: 10) {
            ForEach(0..<5, id: \.self) { _ in
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(.white.opacity(0.08))
                    .frame(width: PosterSlot.profileRail.size.width, height: PosterSlot.profileRail.size.height)
            }
            Spacer(minLength: 0)
        }
        .redacted(reason: .placeholder)
    }
}

private struct RecentActivityPoster: View {
    let item: ProfileRecentActivityRailItem
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            subjectContent
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityHint(accessibilityHint)
    }

    @ViewBuilder
    private var subjectContent: some View {
        switch item.subject {
        case let .media(media):
            VStack(alignment: .leading, spacing: 7) {
                MediaArtwork(
                    url: media.displayPosterURL,
                    title: media.title,
                    slot: .profileRail,
                    mediaType: media.ref.mediaType,
                    orientation: media.posterOrientation
                )
                mediaMetadataLine(media)
            }
        case let .person(person):
            VStack(alignment: .center, spacing: 3) {
                PersonArtwork(
                    urlString: person.profileUrl,
                    name: person.name,
                    size: 64
                )
                Text(person.name)
                    .font(.system(size: 11.5, weight: .heavy, design: .rounded))
                    .foregroundStyle(.white.opacity(0.92))
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .frame(width: PosterSlot.profileRail.size.width, height: 25, alignment: .top)
                if let department = ActivityFeedPresentation.clean(person.knownForDepartment) {
                    Text(department)
                        .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(1)
                        .frame(width: PosterSlot.profileRail.size.width)
                }
                if let listName = ActivityFeedPresentation.listName(for: item.activity) {
                    Text(listName)
                        .font(.system(size: 9.5, weight: .bold, design: .rounded))
                        .foregroundStyle(.white.opacity(0.5))
                        .lineLimit(1)
                        .frame(width: PosterSlot.profileRail.size.width)
                }
            }
        }
    }

    @ViewBuilder
    private func mediaMetadataLine(_ media: MediaSummary) -> some View {
        if let progressDeltaText = ProfileRecentActivityRailModel.progressDeltaText(for: item.activity, media: media) {
            Text(progressDeltaText)
                .font(.system(size: 12.5, weight: .bold))
                .foregroundStyle(.white.opacity(0.54))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .frame(width: PosterSlot.profileRail.size.width, height: 12.5, alignment: .leading)
        } else if ProfileRecentActivityRailModel.isDiary(item.activity),
                  ProfileRecentActivityRailModel.rating(for: item.activity) != nil || ProfileRecentActivityRailModel.isLikedDiary(item.activity) {
            HStack(spacing: 5) {
                if let rating = ProfileRecentActivityRailModel.rating(for: item.activity) {
                    ProfileStarRating(
                        rating: rating,
                        reservesWidth: !ProfileRecentActivityRailModel.isLikedDiary(item.activity),
                        mediaType: item.activity.media?.ref.mediaType
                    )
                }

                if ProfileRecentActivityRailModel.isLikedDiary(item.activity) {
                    Image(systemName: "heart.fill")
                        .font(.system(size: 11.25, weight: .bold))
                        .foregroundStyle(.pink)
                        .accessibilityLabel("Liked")
                }
            }
            .frame(width: PosterSlot.profileRail.size.width, height: 12.5, alignment: .leading)
        } else if let label = ProfileRecentActivityRailModel.fallbackLabel(for: item.activity) {
            Text(label)
                .font(.system(size: 12.5, weight: .bold))
                .foregroundStyle(.white.opacity(0.54))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
                .frame(width: PosterSlot.profileRail.size.width, height: 12.5, alignment: .leading)
        }
    }

    private var accessibilityLabel: String {
        switch item.subject {
        case let .media(media):
            "View \(media.title)"
        case let .person(person):
            "View \(person.name)"
        }
    }

    private var accessibilityHint: String {
        switch item.subject {
        case .media:
            "Opens media details"
        case .person:
            "Opens person details"
        }
    }
}

private struct ProfileStarRating: View {
    let rating: String?
    var reservesWidth = true
    var mediaType: String?

    var body: some View {
        HStack(spacing: 1) {
            ForEach(Array(symbolNames.enumerated()), id: \.offset) { _, symbolName in
                Image(systemName: symbolName)
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.yellow.opacity(0.92))
            }
        }
        .frame(width: reservesWidth ? PosterSlot.profileRail.size.width : nil, height: 12.5, alignment: .leading)
        .accessibilityHidden(value == nil)
        .accessibilityLabel(value.map { "Rating \($0) out of 5 stars" } ?? "")
    }

    private var value: Double? {
        guard let rating, let raw = Double(rating) else { return nil }
        return ["movie", "music", "book"].contains(mediaType) ? raw : raw / 2
    }

    private var symbolNames: [String] {
        guard let value else { return [] }
        let fullStars = Int(value.rounded(.down))
        let hasHalfStar = value - Double(fullStars) >= 0.5
        return Array(repeating: "star.fill", count: fullStars)
            + (hasHalfStar ? ["star.leadinghalf.filled"] : [])
    }
}

private struct EmptyProfileCard: View {
    let title: String
    let systemName: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: systemName)
                .font(.system(size: 15, weight: .bold))
            Text(title)
                .font(.system(size: 14, weight: .semibold))
                .lineLimit(3)
        }
        .foregroundStyle(.white.opacity(0.48))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private enum ImportStatusSource: Hashable, Identifiable {
    case letterboxd
    case storygraph
    case goodreads
    case myAnimeList

    var id: Self { self }
}

private struct ProfileSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProfileSettingsViewModel
    @State private var importStatusSource: ImportStatusSource?
    @State private var isLogoutConfirmationPresented = false

    let profile: UserProfile?
    let onProfileUpdated: (UserProfile) -> Void
    let importCoordinator: LetterboxdImportCoordinator
    let storygraphImportCoordinator: StoryGraphImportCoordinator
    let goodreadsImportCoordinator: GoodreadsImportCoordinator
    let myAnimeListImportCoordinator: MyAnimeListImportCoordinator
    let onLogout: () -> Void
    private let profileRepository: ProfileRepository
    private let mediaRepository: MediaRepository
    private let onUnauthorized: () -> Void

    init(
        profile: UserProfile?,
        profileRepository: ProfileRepository,
        mediaRepository: MediaRepository,
        onProfileUpdated: @escaping (UserProfile) -> Void,
        onUnauthorized: @escaping () -> Void,
        importCoordinator: LetterboxdImportCoordinator,
        storygraphImportCoordinator: StoryGraphImportCoordinator,
        goodreadsImportCoordinator: GoodreadsImportCoordinator,
        myAnimeListImportCoordinator: MyAnimeListImportCoordinator,
        onLogout: @escaping () -> Void
    ) {
        self.profile = profile
        self.onProfileUpdated = onProfileUpdated
        self.importCoordinator = importCoordinator
        self.storygraphImportCoordinator = storygraphImportCoordinator
        self.goodreadsImportCoordinator = goodreadsImportCoordinator
        self.myAnimeListImportCoordinator = myAnimeListImportCoordinator
        self.onLogout = onLogout
        self.profileRepository = profileRepository
        self.mediaRepository = mediaRepository
        self.onUnauthorized = onUnauthorized
        _viewModel = State(initialValue: ProfileSettingsViewModel(
            profileRepository: profileRepository,
            mediaRepository: mediaRepository,
            onUnauthorized: onUnauthorized
        ))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SpinePageBackground()

                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 28) {
                        if let currentProfile {
                            settingsHero(currentProfile)
                            accountLinks(currentProfile)
                        }

                        SettingsGroup(title: "Imports") {
                            SettingsCard {
                                importSectionContent
                            }
                        }

                        SettingsGroup(title: "Spine") {
                            SettingsCard {
                                NavigationLink {
                                    SettingsAboutView()
                                } label: {
                                    SettingsNavigationRow(
                                        title: "About Spine",
                                        detail: "Version, service, and data credits",
                                        systemName: "info.circle.fill"
                                    )
                                }
                                .buttonStyle(.plain)
                            }
                        }

                        Button(role: .destructive) {
                            isLogoutConfirmationPresented = true
                        } label: {
                            Label {
                                Text("Sign Out")
                            } icon: {
                                Image(systemName: "rectangle.portrait.and.arrow.right")
                                    .foregroundStyle(.white.opacity(0.85))
                            }
                                .font(.headline)
                                .foregroundStyle(.red.opacity(0.9))
                                .frame(maxWidth: .infinity, minHeight: 24)
                        }
                        .tint(.red.opacity(0.09))
                        .buttonStyle(.bordered)
                        .controlSize(.large)

                        Text("Your library stays on your Spine account.")
                            .font(.footnote)
                            .foregroundStyle(.white.opacity(0.38))
                            .frame(maxWidth: .infinity)
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 36)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .task {
                viewModel.load(profile: profile)
                await viewModel.loadOptions()
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .fullScreenCover(item: $importStatusSource) { source in
                switch source {
                case .letterboxd:
                    LetterboxdImportUploadView(
                        coordinator: importCoordinator,
                        onDone: { importStatusSource = nil }
                    )
                case .storygraph:
                    StoryGraphImportUploadView(
                        coordinator: storygraphImportCoordinator,
                        onDone: { importStatusSource = nil }
                    )
                case .goodreads:
                    GoodreadsImportUploadView(
                        coordinator: goodreadsImportCoordinator,
                        onDone: { importStatusSource = nil }
                    )
                case .myAnimeList:
                    MyAnimeListImportUploadView(
                        coordinator: myAnimeListImportCoordinator,
                        onDone: { importStatusSource = nil }
                    )
                }
            }
            .confirmationDialog(
                "Sign out of Spine?",
                isPresented: $isLogoutConfirmationPresented,
                titleVisibility: .visible
            ) {
                Button("Sign Out", role: .destructive) {
                    dismiss()
                    onLogout()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("You can sign in again at any time.")
            }
        }
        .preferredColorScheme(.dark)
        .tint(.white)
    }

    private var currentProfile: UserProfile? {
        viewModel.profile ?? profile
    }

    private func settingsHero(_ profile: UserProfile) -> some View {
        let backdropURL = profile.profileBackdropUrl?.trimmedNonEmpty ?? profile.profileBackdropItem?.displayBackdropURL
        let shape = RoundedRectangle(cornerRadius: 24, style: .continuous)

        return GeometryReader { proxy in
            ZStack(alignment: .bottomLeading) {
                if let backdropURL {
                    SpineAsyncImage(url: URL(string: backdropURL)) { phase in
                        if case let .success(image) = phase {
                            image.resizable().scaledToFill()
                        } else {
                            SettingsHeroFallback()
                        }
                    }
                    .frame(width: proxy.size.width, height: proxy.size.height)
                    .clipped()
                } else {
                    SettingsHeroFallback()
                }

                LinearGradient(
                    colors: [.clear, .black.opacity(0.84)],
                    startPoint: .top,
                    endPoint: .bottom
                )

                HStack(alignment: .bottom, spacing: 14) {
                    SettingsAvatar(profile: profile, size: 74)

                    VStack(alignment: .leading, spacing: 4) {
                        Text(profile.displayName)
                            .font(.title2.bold())
                            .foregroundStyle(.white)
                            .lineLimit(1)

                        Text("@\(profile.username)")
                            .font(.subheadline.weight(.medium))
                            .foregroundStyle(.white.opacity(0.58))

                        Label(profile.isPrivate ? "Private" : "Public", systemImage: profile.isPrivate ? "lock.fill" : "globe")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.78))
                    }

                    Spacer(minLength: 0)
                }
                .padding(18)
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
        .frame(maxWidth: .infinity)
        .frame(height: 210)
        .clipShape(shape)
        .overlay { shape.strokeBorder(.white.opacity(0.14), lineWidth: 1) }
        .accessibilityElement(children: .combine)
    }

    private func accountLinks(_ profile: UserProfile) -> some View {
        SettingsGroup(title: "Your space") {
            SettingsCard {
                VStack(spacing: 0) {
                    NavigationLink {
                        SettingsProfileView(
                            viewModel: viewModel,
                            fallbackProfile: profile,
                            profileRepository: profileRepository,
                            mediaRepository: mediaRepository,
                            onProfileUpdated: onProfileUpdated,
                            onUnauthorized: onUnauthorized
                        )
                    } label: {
                        SettingsNavigationRow(
                            title: "Profile",
                            detail: "Photo, name, bio, and privacy",
                            systemName: "person.crop.circle.fill"
                        )
                    }
                    .buttonStyle(.plain)

                    SettingsDivider()

                    NavigationLink {
                        SettingsPreferencesView(viewModel: viewModel, onProfileUpdated: onProfileUpdated)
                    } label: {
                        SettingsNavigationRow(
                            title: "Media & preferences",
                            detail: "Media types and logging",
                            systemName: "slider.horizontal.3"
                        )
                    }
                    .buttonStyle(.plain)

                    SettingsDivider()

                    NavigationLink {
                        SettingsPasswordView(viewModel: viewModel)
                    } label: {
                        SettingsNavigationRow(
                            title: "Password",
                            detail: "Update your sign-in password",
                            systemName: "key.fill"
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    @ViewBuilder
    private var importSectionContent: some View {
        if importCoordinator.phase == .idle &&
            storygraphImportCoordinator.phase == .idle &&
            goodreadsImportCoordinator.phase == .idle &&
            myAnimeListImportCoordinator.phase == .idle {
            NavigationLink {
                LetterboxdImportView(coordinator: importCoordinator)
            } label: {
                SettingsNavigationRow(
                    title: "Letterboxd",
                    detail: "Movies, diary, lists, and likes",
                    importSource: "Letterboxd"
                )
            }
            .buttonStyle(.plain)

            NavigationLink {
                MyAnimeListImportView(coordinator: myAnimeListImportCoordinator)
            } label: {
                SettingsNavigationRow(
                    title: "MyAnimeList",
                    detail: "Anime, manga, and watch history",
                    importSource: "MyAnimeList"
                )
            }
            .buttonStyle(.plain)

            NavigationLink {
                StoryGraphImportView(coordinator: storygraphImportCoordinator)
            } label: {
                SettingsNavigationRow(
                    title: "StoryGraph",
                    detail: "Books and reading history",
                    importSource: "StoryGraph"
                )
            }
            .buttonStyle(.plain)

            NavigationLink {
                GoodreadsImportView(coordinator: goodreadsImportCoordinator)
            } label: {
                SettingsNavigationRow(
                    title: "Goodreads",
                    detail: "Books and reading history",
                    importSource: "Goodreads"
                )
            }
            .buttonStyle(.plain)
        } else if importCoordinator.phase != .idle {
            VStack(alignment: .leading, spacing: 12) {
                letterboxdImportStatus
            }
            .padding(16)
        } else if myAnimeListImportCoordinator.phase != .idle {
            VStack(alignment: .leading, spacing: 12) {
                myAnimeListImportStatus
            }
            .padding(16)
        } else if storygraphImportCoordinator.phase != .idle {
            VStack(alignment: .leading, spacing: 12) {
                storygraphImportStatus
            }
            .padding(16)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                goodreadsImportStatus
            }
            .padding(16)
        }
    }

    @ViewBuilder
    private var letterboxdImportStatus: some View {
        switch importCoordinator.phase {
        case .idle:
            EmptyView()
        case let .uploading(_, progress):
            Button {
                importStatusSource = .letterboxd
            } label: {
                HStack(spacing: 12) {
                    ProgressView(value: progress)
                        .frame(width: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Uploading...")
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case let .processing(_, statusLabel, _):
            Button {
                importStatusSource = .letterboxd
            } label: {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(statusLabel)
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            letterboxdCheckStatusButton
        case let .succeeded(message):
            importResultRow(systemName: "checkmark.circle.fill", message: message)
            Button("Dismiss") {
                importCoordinator.clearFinishedJob()
            }
        case let .failed(message):
            importResultRow(systemName: "exclamationmark.triangle.fill", message: message)
            if importCoordinator.canCheckStatus {
                letterboxdCheckStatusButton
            }
            NavigationLink {
                LetterboxdImportView(coordinator: importCoordinator)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
            }
            Button("Dismiss") {
                importCoordinator.clearFinishedJob()
            }
        }
    }

    @ViewBuilder
    private var myAnimeListImportStatus: some View {
        switch myAnimeListImportCoordinator.phase {
        case .idle:
            EmptyView()
        case let .uploading(_, progress):
            Button {
                importStatusSource = .myAnimeList
            } label: {
                HStack(spacing: 12) {
                    ProgressView(value: progress)
                        .frame(width: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Uploading...")
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case let .processing(_, statusLabel, _):
            Button {
                importStatusSource = .myAnimeList
            } label: {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(statusLabel)
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            myAnimeListCheckStatusButton
        case let .succeeded(message):
            importResultRow(systemName: "checkmark.circle.fill", message: message)
            Button("Dismiss") {
                myAnimeListImportCoordinator.clearFinishedJob()
            }
        case let .failed(message):
            importResultRow(systemName: "exclamationmark.triangle.fill", message: message)
            if myAnimeListImportCoordinator.canCheckStatus {
                myAnimeListCheckStatusButton
            }
            NavigationLink {
                MyAnimeListImportView(coordinator: myAnimeListImportCoordinator)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
            }
            Button("Dismiss") {
                myAnimeListImportCoordinator.clearFinishedJob()
            }
        }
    }

    @ViewBuilder
    private var storygraphImportStatus: some View {
        switch storygraphImportCoordinator.phase {
        case .idle:
            EmptyView()
        case let .uploading(_, progress):
            Button {
                importStatusSource = .storygraph
            } label: {
                HStack(spacing: 12) {
                    ProgressView(value: progress)
                        .frame(width: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Uploading...")
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case let .processing(_, statusLabel, _):
            Button {
                importStatusSource = .storygraph
            } label: {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(statusLabel)
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            storygraphCheckStatusButton
        case let .succeeded(message):
            importResultRow(systemName: "checkmark.circle.fill", message: message)
            Button("Dismiss") {
                storygraphImportCoordinator.clearFinishedJob()
            }
        case let .failed(message):
            importResultRow(systemName: "exclamationmark.triangle.fill", message: message)
            if storygraphImportCoordinator.canCheckStatus {
                storygraphCheckStatusButton
            }
            NavigationLink {
                StoryGraphImportView(coordinator: storygraphImportCoordinator)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
            }
            Button("Dismiss") {
                storygraphImportCoordinator.clearFinishedJob()
            }
        }
    }

    @ViewBuilder
    private var goodreadsImportStatus: some View {
        switch goodreadsImportCoordinator.phase {
        case .idle:
            EmptyView()
        case let .uploading(_, progress):
            Button {
                importStatusSource = .goodreads
            } label: {
                HStack(spacing: 12) {
                    ProgressView(value: progress)
                        .frame(width: 44)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Uploading...")
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        case let .processing(_, statusLabel, _):
            Button {
                importStatusSource = .goodreads
            } label: {
                HStack(spacing: 12) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 3) {
                        Text(statusLabel)
                        Text("Tap for details")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            goodreadsCheckStatusButton
        case let .succeeded(message):
            importResultRow(systemName: "checkmark.circle.fill", message: message)
            Button("Dismiss") {
                goodreadsImportCoordinator.clearFinishedJob()
            }
        case let .failed(message):
            importResultRow(systemName: "exclamationmark.triangle.fill", message: message)
            if goodreadsImportCoordinator.canCheckStatus {
                goodreadsCheckStatusButton
            }
            NavigationLink {
                GoodreadsImportView(coordinator: goodreadsImportCoordinator)
            } label: {
                Label("Try Again", systemImage: "arrow.clockwise")
            }
            Button("Dismiss") {
                goodreadsImportCoordinator.clearFinishedJob()
            }
        }
    }

    private var letterboxdCheckStatusButton: some View {
        Button {
            importCoordinator.checkStatusOnce()
        } label: {
            if importCoordinator.isCheckingStatus {
                Label("Checking Status", systemImage: "clock.arrow.circlepath")
            } else {
                Label("Check Status", systemImage: "arrow.clockwise")
            }
        }
        .disabled(importCoordinator.isCheckingStatus)
    }

    private var myAnimeListCheckStatusButton: some View {
        Button {
            myAnimeListImportCoordinator.checkStatusOnce()
        } label: {
            if myAnimeListImportCoordinator.isCheckingStatus {
                Label("Checking Status", systemImage: "clock.arrow.circlepath")
            } else {
                Label("Check Status", systemImage: "arrow.clockwise")
            }
        }
        .disabled(myAnimeListImportCoordinator.isCheckingStatus)
    }

    private var storygraphCheckStatusButton: some View {
        Button {
            storygraphImportCoordinator.checkStatusOnce()
        } label: {
            if storygraphImportCoordinator.isCheckingStatus {
                Label("Checking Status", systemImage: "clock.arrow.circlepath")
            } else {
                Label("Check Status", systemImage: "arrow.clockwise")
            }
        }
        .disabled(storygraphImportCoordinator.isCheckingStatus)
    }

    private var goodreadsCheckStatusButton: some View {
        Button {
            goodreadsImportCoordinator.checkStatusOnce()
        } label: {
            if goodreadsImportCoordinator.isCheckingStatus {
                Label("Checking Status", systemImage: "clock.arrow.circlepath")
            } else {
                Label("Check Status", systemImage: "arrow.clockwise")
            }
        }
        .disabled(goodreadsImportCoordinator.isCheckingStatus)
    }

    private func importResultRow(systemName: String, message: String) -> some View {
        Label {
            Text(message)
                .lineLimit(2)
        } icon: {
            Image(systemName: systemName)
                .foregroundStyle(.white)
        }
    }
}

private struct SettingsProfileView: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: ProfileSettingsViewModel
    @State private var selectedPhotoItem: PhotosPickerItem?
    @State private var isBackdropSearchPresented = false

    let fallbackProfile: UserProfile
    let profileRepository: ProfileRepository
    let mediaRepository: MediaRepository
    let onProfileUpdated: (UserProfile) -> Void
    let onUnauthorized: () -> Void

    private var profile: UserProfile {
        viewModel.profile ?? fallbackProfile
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 24) {
                    profilePhotoSection
                    identitySection
                    privacySection
                    SettingsStatusMessage(viewModel: viewModel)

                    SettingsSaveButton(
                        title: "Save Profile",
                        isSaving: viewModel.isSavingProfile,
                        isDisabled: !viewModel.hasProfileChanges
                    ) {
                        if let updated = await viewModel.saveProfile() {
                            onProfileUpdated(updated)
                            dismiss()
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 36)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: selectedPhotoItem) { _, item in
            Task {
                if let updated = await viewModel.saveAvatar(from: item) {
                    onProfileUpdated(updated)
                }
                selectedPhotoItem = nil
            }
        }
        .fullScreenCover(isPresented: $isBackdropSearchPresented) {
            ProfileBackdropSearchView(
                mediaRepository: mediaRepository,
                profileRepository: profileRepository,
                currentBackdropURL: profile.profileBackdropUrl,
                onUnauthorized: onUnauthorized
            ) { response in
                applyBackdrop(response)
            }
        }
    }

    private var profilePhotoSection: some View {
        let isSavingAvatar = viewModel.isSavingAvatar

        return SettingsGroup(title: "How you appear") {
            SettingsCard {
                VStack(spacing: 18) {
                    HStack(spacing: 16) {
                        SettingsAvatar(profile: profile, size: 82)

                        VStack(alignment: .leading, spacing: 10) {
                            PhotosPicker(selection: $selectedPhotoItem, matching: .images) {
                                Label(isSavingAvatar ? "Uploading" : "Change Photo", systemImage: "camera.fill")
                                    .font(.subheadline.weight(.semibold))
                            }
                            .disabled(viewModel.isSavingAvatar)

                            if profile.avatarUrl != nil {
                                Button("Remove Photo", role: .destructive) {
                                    Task {
                                        if let updated = await viewModel.removeAvatar() {
                                            onProfileUpdated(updated)
                                        }
                                    }
                                }
                                .font(.subheadline.weight(.semibold))
                                .disabled(viewModel.isSavingAvatar)
                            }
                        }

                        Spacer(minLength: 0)
                    }

                    SettingsDivider()

                    Button {
                        isBackdropSearchPresented = true
                    } label: {
                        SettingsActionRow(
                            title: "Choose Profile Backdrop",
                            systemName: "photo.on.rectangle.angled"
                        )
                    }
                    .buttonStyle(.plain)

                    if profile.profileBackdropUrl != nil {
                        SettingsDivider()

                        Button(role: .destructive) {
                            Task { await removeBackdrop() }
                        } label: {
                            SettingsActionRow(
                                title: "Remove Profile Backdrop",
                                systemName: "trash.fill",
                                tint: .red
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(16)
            }
        }
    }

    private var identitySection: some View {
        SettingsGroup(title: "Identity") {
            SettingsCard {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 6) {
                        SettingsTextField(title: "Display name", text: $viewModel.displayName)
                        SettingsFieldError(viewModel: viewModel, keys: ["display_name", "displayName"])
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        SettingsTextField(title: "Username", text: $viewModel.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        SettingsFieldError(viewModel: viewModel, keys: ["username"])
                    }
                    if let email = profile.email?.trimmedNonEmpty {
                        SettingsReadOnlyField(title: "Email", value: email)
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        SettingsTextField(title: "Location", text: $viewModel.location)
                        SettingsFieldError(viewModel: viewModel, keys: ["location"])
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        SettingsTextField(title: "Bio", text: $viewModel.bio, axis: .vertical)
                        SettingsFieldError(viewModel: viewModel, keys: ["bio"])
                    }
                }
                .padding(16)
            }
        }
    }

    private var privacySection: some View {
        SettingsGroup(title: "Privacy") {
            SettingsCard {
                Toggle(isOn: $viewModel.isPrivate) {
                    SettingsControlLabel(
                        title: "Private Account",
                        detail: "You approve new followers.",
                        systemName: "lock.fill"
                    )
                }
                .tint(.green)
                .padding(16)

                SettingsFieldError(viewModel: viewModel, keys: ["is_private", "profile_private"])
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
            }
        }
    }

    private func applyBackdrop(_ response: ProfileBackdropSaveResponse) {
        let updated = profile.replacingProfileBackdrop(response)
        viewModel.load(profile: updated)
        onProfileUpdated(updated)
    }

    private func removeBackdrop() async {
        do {
            applyBackdrop(try await profileRepository.clearProfileBackdrop())
        } catch {
            viewModel.errorMessage = error.localizedDescription
        }
    }
}

private struct SettingsPreferencesView: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: ProfileSettingsViewModel
    let onProfileUpdated: (UserProfile) -> Void

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 24) {
                    mediaTypesSection
                    loggingSection
                    SettingsStatusMessage(viewModel: viewModel)

                    SettingsSaveButton(
                        title: "Save Preferences",
                        isSaving: viewModel.isSavingPreferences,
                        isDisabled: !viewModel.hasPreferenceChanges
                    ) {
                        if let updated = await viewModel.savePreferences() {
                            onProfileUpdated(updated)
                            dismiss()
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 36)
            }
        }
        .navigationTitle("Media & Preferences")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var mediaTypesSection: some View {
        SettingsGroup(title: "Your media") {
            SettingsCard {
                if viewModel.isLoadingOptions {
                    ProgressView("Loading media types")
                        .frame(maxWidth: .infinity, minHeight: 80)
                } else {
                    ForEach(viewModel.mediaTypes, id: \.self) { mediaType in
                        let theme = MediaTypeTheme.theme(for: mediaType)
                        Toggle(isOn: Binding(
                            get: { viewModel.enabledMediaTypes.contains(mediaType) },
                            set: { enabled in
                                if enabled {
                                    viewModel.enabledMediaTypes.insert(mediaType)
                                } else {
                                    viewModel.enabledMediaTypes.remove(mediaType)
                                }
                            }
                        )) {
                            HStack(spacing: 12) {
                                SettingsIcon(systemName: theme.symbolName)
                                Text(theme.displayName)
                                    .foregroundStyle(.white)
                            }
                        }
                        .tint(.green)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)

                        if mediaType != viewModel.mediaTypes.last {
                            SettingsDivider()
                        }
                    }
                }

                SettingsFieldError(viewModel: viewModel, keys: ["enabled_media_types"])
            }
        }
    }

    private var loggingSection: some View {
        SettingsGroup(title: "Logging") {
            SettingsCard {
                SettingsChoicePicker(
                    title: "Week starts",
                    systemName: "calendar",
                    selection: $viewModel.weekStartDay,
                    choices: viewModel.settingsOptions.weekStartDays
                )
                SettingsDivider()
                SettingsChoicePicker(
                    title: "Quick log date",
                    systemName: "bolt",
                    selection: $viewModel.quickWatchDate,
                    choices: viewModel.settingsOptions.quickWatchDates
                )
            }
        }
    }
}

private struct SettingsPasswordView: View {
    @Environment(\.dismiss) private var dismiss
    @Bindable var viewModel: ProfileSettingsViewModel

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "key.fill")
                            .font(.system(size: 30, weight: .semibold))
                            .foregroundStyle(.white)

                        Text("Keep your account secure.")
                            .font(.system(size: 28, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)

                        Text("Use at least eight characters for your new password.")
                            .font(.body)
                            .foregroundStyle(.white.opacity(0.52))
                    }

                    SettingsCard {
                        VStack(spacing: 14) {
                            SettingsSecureField(title: "Current password", text: $viewModel.oldPassword)
                            SettingsFieldError(viewModel: viewModel, keys: ["old_password"])
                            SettingsSecureField(title: "New password", text: $viewModel.newPassword)
                            SettingsFieldError(viewModel: viewModel, keys: ["new_password"])
                            SettingsSecureField(title: "Confirm new password", text: $viewModel.newPasswordConfirm)
                            SettingsFieldError(viewModel: viewModel, keys: ["new_password_confirm"])
                        }
                        .padding(16)
                    }

                    SettingsStatusMessage(viewModel: viewModel)

                    SettingsSaveButton(
                        title: "Update Password",
                        isSaving: viewModel.isSavingPassword,
                        isDisabled: viewModel.oldPassword.isEmpty || viewModel.newPassword.isEmpty || viewModel.newPasswordConfirm.isEmpty
                    ) {
                        if await viewModel.changePassword() {
                            dismiss()
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 36)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        .navigationTitle("Password")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct SettingsAboutView: View {
    private var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    private var build: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—"
    }

    var body: some View {
        ZStack {
            SpinePageBackground()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(spacing: 10) {
                        SpineWordmark()
                            .frame(maxWidth: .infinity)

                        Text("One home for everything you watch, read, play, and hear.")
                            .font(.title3.bold())
                            .multilineTextAlignment(.center)
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity)
                    }
                    .padding(.vertical, 12)

                    SettingsGroup(title: "App") {
                        SettingsCard {
                            VStack(spacing: 0) {
                                SettingsValueRow(title: "Version", value: "\(version) (\(build))")
                                SettingsDivider()
                                SettingsValueRow(title: "API Service", value: AppConfig.apiBaseURL.host() ?? AppConfig.apiBaseURL.absoluteString)
                                SettingsDivider()
                                SettingsValueRow(title: "API Path", value: AppConfig.apiPrefix)
                            }
                        }
                    }

                    SettingsGroup(title: "Data sources") {
                        SettingsCard {
                            VStack(spacing: 0) {
                                SettingsCreditLink(
                                    title: "MusicBrainz",
                                    detail: "Music metadata",
                                    url: URL(string: "https://musicbrainz.org/")!
                                )
                                SettingsDivider()
                                SettingsCreditLink(
                                    title: "Cover Art Archive",
                                    detail: "Album artwork",
                                    url: URL(string: "https://coverartarchive.org/")!
                                )
                                SettingsDivider()
                                SettingsCreditLink(
                                    title: "Steam",
                                    detail: "Game review data",
                                    url: URL(string: "https://store.steampowered.com/")!
                                )
                            }
                        }
                    }

                    Text("©2026 Valve Corporation. Steam and the Steam logo are trademarks or registered trademarks of Valve Corporation.")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.38))
                }
                .padding(.horizontal, 18)
                .padding(.top, 18)
                .padding(.bottom, 36)
            }
        }
        .navigationTitle("About")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct SettingsGroup<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            Text(title)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(.white.opacity(0.46))
                .padding(.leading, 3)

            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct SettingsCard<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 20, style: .continuous)

        VStack(spacing: 0) {
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(uiColor: .secondarySystemGroupedBackground), in: shape)
    }
}

private struct SettingsNavigationRow: View {
    let title: String
    let detail: String
    var systemName: String = ""
    var importSource: String?

    var body: some View {
        HStack(spacing: 12) {
            if let importSource {
                SettingsImportLogo(source: importSource)
            } else {
                SettingsIcon(systemName: systemName)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.white)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.46))
                    .lineLimit(2)
            }

            Spacer(minLength: 8)

            Image(systemName: "chevron.right")
                .font(.caption.bold())
                .foregroundStyle(.white.opacity(0.28))
        }
        .padding(14)
        .contentShape(Rectangle())
    }
}

private struct SettingsActionRow: View {
    let title: String
    let systemName: String
    var tint: Color = .white

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: systemName)
                .font(.body.weight(.semibold))
                .foregroundStyle(.white.opacity(0.8))
                .frame(width: 24)
            Text(title)
                .font(.body.weight(.semibold))
                .foregroundStyle(tint)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption.bold())
                .foregroundStyle(.white.opacity(0.26))
        }
        .contentShape(Rectangle())
    }
}

private struct SettingsControlLabel: View {
    let title: String
    let detail: String
    let systemName: String

    var body: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: systemName)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.white)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.46))
            }
        }
    }
}

private struct SettingsIcon: View {
    let systemName: String

    var body: some View {
        Image(systemName: systemName)
            .symbolRenderingMode(.monochrome)
            .font(.system(size: 19, weight: .regular))
            .foregroundStyle(.white.opacity(0.85))
            .frame(width: 24, height: 28)
            .accessibilityHidden(true)
    }
}

private struct SettingsDivider: View {
    var body: some View {
        Rectangle()
            .fill(.white.opacity(0.075))
            .frame(height: 1)
            .padding(.leading, 50)
    }
}

private struct SettingsAvatar: View {
    let profile: UserProfile
    let size: CGFloat

    var body: some View {
        SpineAsyncImage(url: URL(string: profile.avatarUrl ?? "")) { phase in
            if case let .success(image) = phase {
                image.resizable().scaledToFill()
            } else {
                Image(systemName: "person.crop.circle.fill")
                    .resizable()
                    .foregroundStyle(.white.opacity(0.34))
                    .padding(size * 0.1)
            }
        }
        .frame(width: size, height: size)
        .background(.white.opacity(0.08), in: Circle())
        .clipShape(Circle())
        .overlay { Circle().strokeBorder(.white.opacity(0.24), lineWidth: 1) }
        .accessibilityLabel(profile.displayName)
    }
}

private struct SettingsHeroFallback: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.33, green: 0.22, blue: 0.52),
                    Color(red: 0.08, green: 0.23, blue: 0.27),
                    SpinePalette.pageBackground,
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            Image(systemName: "square.stack.3d.up.fill")
                .font(.system(size: 76, weight: .bold))
                .foregroundStyle(.white.opacity(0.09))
                .offset(x: 100, y: -22)
        }
    }
}

private struct SettingsTextField: View {
    let title: String
    @Binding var text: String
    var axis: Axis = .horizontal

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title.uppercased())
                .font(.caption2.bold())
                .tracking(0.7)
                .foregroundStyle(.white.opacity(0.42))
            TextField(title, text: $text, axis: axis)
                .lineLimit(axis == .vertical ? 3...6 : 1...1)
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(.black.opacity(0.22), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(.white.opacity(0.09), lineWidth: 1)
                }
        }
    }
}

private struct SettingsSecureField: View {
    let title: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title.uppercased())
                .font(.caption2.bold())
                .tracking(0.7)
                .foregroundStyle(.white.opacity(0.42))
            SecureField(title, text: $text)
                .textContentType(.password)
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(.black.opacity(0.22), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(.white.opacity(0.09), lineWidth: 1)
                }
        }
    }
}

private struct SettingsReadOnlyField: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title.uppercased())
                .font(.caption2.bold())
                .tracking(0.7)
                .foregroundStyle(.white.opacity(0.42))
            Text(value)
                .foregroundStyle(.white.opacity(0.55))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .background(.black.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }
}

private struct SettingsFieldError: View {
    let viewModel: ProfileSettingsViewModel
    let keys: [String]

    var body: some View {
        if let message = keys.lazy.compactMap({ viewModel.fieldErrors[$0] }).first {
            Text(message)
                .font(.footnote)
                .foregroundStyle(.red)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct SettingsStatusMessage: View {
    let viewModel: ProfileSettingsViewModel

    var body: some View {
        if let error = viewModel.errorMessage?.trimmedNonEmpty {
            SettingsMessageCard(message: error, systemName: "exclamationmark.triangle.fill", tint: .red)
        } else if let success = viewModel.successMessage?.trimmedNonEmpty {
            SettingsMessageCard(message: success, systemName: "checkmark.circle.fill", tint: .green)
        }
    }
}

private struct SettingsMessageCard: View {
    let message: String
    let systemName: String
    let tint: Color

    var body: some View {
        Label {
            Text(message)
        } icon: {
            Image(systemName: systemName)
                .foregroundStyle(.white)
        }
        .font(.footnote.weight(.medium))
        .foregroundStyle(tint)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(tint.opacity(0.09), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

private struct SettingsSaveButton: View {
    let title: String
    let isSaving: Bool
    let isDisabled: Bool
    let action: () async -> Void

    var body: some View {
        Button {
            Task { await action() }
        } label: {
            HStack(spacing: 10) {
                if isSaving {
                    ProgressView()
                        .tint(.black)
                }
                Text(isSaving ? "Saving" : title)
            }
            .font(.headline)
            .foregroundStyle(isDisabled ? .white.opacity(0.38) : .black)
            .frame(maxWidth: .infinity, minHeight: 24)
        }
        .tint(isDisabled ? .white.opacity(0.08) : .white)
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(isDisabled || isSaving)
    }
}

private struct SettingsChoicePicker: View {
    let title: String
    let systemName: String
    @Binding var selection: String
    let choices: [PreferenceChoice]

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                label
                Spacer(minLength: 8)
                menu
            }
            VStack(alignment: .leading, spacing: 10) {
                label
                menu
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
        }
        .padding(14)
    }

    private var label: some View {
        HStack(spacing: 12) {
            SettingsIcon(systemName: systemName)
            Text(title)
                .foregroundStyle(.white)
                .fixedSize()
        }
    }

    private var menu: some View {
        Menu {
            Picker(title, selection: $selection) {
                if choices.isEmpty {
                    Text(selection).tag(selection)
                } else {
                    ForEach(choices) { choice in
                        Text(choice.label).tag(choice.value)
                    }
                }
            }
        } label: {
            HStack(spacing: 5) {
                Text(choices.first(where: { $0.value == selection })?.label ?? selection)
                    .fixedSize(horizontal: true, vertical: false)
                Image(systemName: "chevron.up.chevron.down")
                    .font(.caption2)
            }
            .font(.subheadline)
            .foregroundStyle(.white.opacity(0.62))
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityLabel(title)
    }
}

private struct SettingsValueRow: View {
    let title: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title)
                .font(.body.weight(.semibold))
                .foregroundStyle(.white)
            Spacer()
            Text(value)
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.48))
                .multilineTextAlignment(.trailing)
        }
        .padding(16)
    }
}

private struct SettingsCreditLink: View {
    let title: String
    let detail: String
    let url: URL

    var body: some View {
        Link(destination: url) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.46))
                }
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.caption.bold())
                    .foregroundStyle(.white.opacity(0.38))
            }
            .padding(16)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

private extension String {
    var trimmedNonEmpty: String? {
        let trimmed = trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    var profileSlotTitle: String {
        replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
    }
}
