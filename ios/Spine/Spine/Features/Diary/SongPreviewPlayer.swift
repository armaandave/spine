import AVFoundation
import Combine
import SwiftUI

/// Plays a song's 30-second preview, one at a time across the app. The `.playback` session keeps it audible with the
/// silent switch on. It's taken only on a tap, so opening a page never interrupts other music.
@MainActor
@Observable
final class SongPreviewPlayer {
    static let shared = SongPreviewPlayer()

    /// The preview that is loading or playing.
    private(set) var currentURL: URL?
    private(set) var isLoading = false
    /// A preview that wouldn't play: its button stays away until the page opens again, so it's never a dead one.
    private(set) var failedURL: URL?

    @ObservationIgnored private var player: AVPlayer?
    @ObservationIgnored private var observers = Set<AnyCancellable>()

    func toggle(_ url: URL) {
        if currentURL == url {
            stop()
        } else {
            play(url)
        }
    }

    func stop() {
        guard player != nil else { return }
        release()
        releaseSession()
    }

    /// The page closed: nothing keeps playing, and its next opening tries a failed preview again.
    func close() {
        stop()
        failedURL = nil
    }

    private func play(_ url: URL) {
        release()
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback)
            try session.setActive(true)
        } catch {
            // Something like a phone call holds the audio. The preview itself is fine.
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            return
        }

        let item = AVPlayerItem(url: url)
        let player = AVPlayer(playerItem: item)
        self.player = player
        currentURL = url
        isLoading = true

        item.publisher(for: \.status)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] status in
                if status == .failed { self?.fail(url) }
            }
            .store(in: &observers)
        NotificationCenter.default.publisher(for: AVPlayerItem.failedToPlayToEndTimeNotification, object: item)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.fail(url) }
            .store(in: &observers)
        player.publisher(for: \.timeControlStatus)
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] status in self?.update(status, url: url) }
            .store(in: &observers)
        player.play()
    }

    private func update(_ status: AVPlayer.TimeControlStatus, url: URL) {
        switch status {
        case .playing:
            isLoading = false
        case .waitingToPlayAtSpecifiedRate:
            isLoading = true
        case .paused:
            // It reached the end, or a call or another app took the audio. It doesn't pick up by itself.
            if player?.currentItem?.status == .failed {
                fail(url)
            } else {
                stop()
            }
        @unknown default:
            break
        }
    }

    private func fail(_ url: URL) {
        failedURL = url
        stop()
        UINotificationFeedbackGenerator().notificationOccurred(.error)
    }

    /// Lets music the preview interrupted pick up again. The player's audio ends a moment after it's released, and the
    /// session refuses while audio is still running, so a refusal gets one more try.
    private func releaseSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            Task { [weak self] in
                try? await Task.sleep(for: .milliseconds(500))
                guard self?.player == nil else { return }
                try? session.setActive(false, options: .notifyOthersOnDeactivation)
            }
        }
    }

    private func release() {
        observers.removeAll()
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        currentURL = nil
        isLoading = false
    }
}

/// A small round button at the start of a favorite song's bar: tap to play its preview, tap again to stop. It sits on
/// the bar's glass, so it has none of its own.
struct SongPreviewButton: View {
    let url: URL
    let title: String
    let player: SongPreviewPlayer

    private var isCurrent: Bool {
        player.currentURL == url
    }

    var body: some View {
        Button {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            player.toggle(url)
        } label: {
            Group {
                if isCurrent, player.isLoading {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: isCurrent ? "stop.fill" : "play.fill")
                        .font(.system(size: 11, weight: .bold))
                }
            }
            .frame(width: 28, height: 28)
            .background(.quaternary, in: .circle)
            // The full height of the bar is the tap target.
            .padding(8)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isCurrent ? "Stop preview of \(title)" : "Play preview of \(title)")
        .accessibilityIdentifier("diary-log.favorite.preview")
    }
}
