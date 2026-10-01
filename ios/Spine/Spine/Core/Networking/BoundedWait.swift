import Foundation

/// Waits for `task` to finish, but no longer than `timeout`, and says whether it finished. The task keeps running
/// when the timeout wins; callers that don't want that cancel it.
///
/// `task.value` doesn't react to the waiting task being cancelled, so this can't be a task group (a group waits
/// for all its children). It resumes one continuation from whichever of the task and the timer comes first.
nonisolated func waitBounded<Success: Sendable, Failure: Error>(
    for task: Task<Success, Failure>,
    upTo timeout: Duration
) async -> Bool {
    await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
        let once = OneShotResume(continuation)
        let timer = Task {
            try? await Task.sleep(for: timeout)
            once.resume(returning: false)
        }
        Task {
            _ = await task.result
            once.resume(returning: true)
            timer.cancel()
        }
    }
}

private nonisolated final class OneShotResume: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Bool, Never>?

    init(_ continuation: CheckedContinuation<Bool, Never>) {
        self.continuation = continuation
    }

    func resume(returning value: Bool) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(returning: value)
    }
}
