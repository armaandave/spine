import SwiftUI

@MainActor
struct RootView: View {
    @State private var session: AppSession
    @State private var onboardingStore: OnboardingStore
    /// The launch screen's logo flying into Home's header, once per launch.
    @State private var launch = LaunchHandoff()

    init() {
        self.init(repositories: .current())
    }

    init(
        repositories: AppRepositories,
        onboardingStore: OnboardingStore? = nil
    ) {
        _session = State(initialValue: AppSession(repositories: repositories))
        _onboardingStore = State(initialValue: onboardingStore ?? OnboardingStore())
    }

    var body: some View {
        Group {
            switch session.state {
            case .checking:
                SessionCheckingView()
                    .spineSoftAppear()
            case .signedOut:
                OnboardingFlowView(session: session, store: onboardingStore)
                    .spineSoftAppear()
            case .signedIn:
                AppShellView(session: session)
                    .spineSoftAppear()
            }
        }
        .environment(launch)
        .overlay {
            if launch.phase != .done {
                LaunchOverlay(handoff: launch, phase: launch.phase)
                    .ignoresSafeArea()
            }
        }
        .onChange(of: launchSession, initial: true) { _, stage in
            launch.sessionChanged(stage)
        }
        .task {
            await session.start()
            if case .signedIn = session.state {
                onboardingStore.markWelcomeSeen()
            }
        }
    }

    private var launchSession: LaunchHandoff.Session {
        switch session.state {
        case .checking: .checking
        case .signedOut: .signedOut
        case .signedIn: .signedIn
        }
    }
}

private struct SessionCheckingView: View {
    var body: some View {
        ZStack {
            OnboardingBackdrop()

            VStack(spacing: 18) {
                SpineWordmark()
                ProgressView()
                    .tint(.white.opacity(0.72))
            }
        }
        // No preferred color scheme: the launch overlay covers this view, and forcing dark here flipped the status bar
        // over the launch screen's plaster for a moment.
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Opening Spine")
    }
}

#Preview {
    RootView()
}
