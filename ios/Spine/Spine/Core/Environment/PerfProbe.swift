import Foundation

/// The counters the perf gates read. The raw names are written to the probe file and read by
/// scripts/ios/perf/report.py and the tests, so they must not change.
enum PerfCounter: String, CaseIterable {
    case homeBody, feedCardBody, feedCardAppear, tilesBody, glowBody
}

/// Debug-only view counters for the Home performance gates, so a gate can prove that rows do not re-render
/// while scrolling without depending on how loaded the machine is.
///
/// Call-site convention: the first statement of a View body is `let _ = PerfCounters.bump(.feedCardBody)`;
/// `feedCardAppear` is bumped in `onAppear`. `bump` exists in Release as an empty function, so call sites need no
/// `#if`. In Debug it counts only while the probe is active (SPINE_PERF_PROBE_FILE is set), so normal Debug runs
/// pay one lock and a flag check.
enum PerfCounters {
    @inline(__always)
    static func bump(_ counter: PerfCounter) {
        #if DEBUG
        PerfCounterStore.shared.bump(counter)
        #endif
    }
}

#if DEBUG
import Darwin
import UIKit

/// The counting and snapshot logic the probe and the tests share.
nonisolated final class PerfCounterStore: @unchecked Sendable {
    static let shared = PerfCounterStore()

    private let lock = NSLock()
    private var active = false
    private var values: [PerfCounter: Int] = [:]
    private var reported: [PerfCounter: Int] = [:]

    var isActive: Bool {
        lock.lock()
        defer { lock.unlock() }
        return active
    }

    func setActive(_ value: Bool) {
        lock.lock()
        active = value
        lock.unlock()
    }

    /// Does nothing while inactive.
    func bump(_ counter: PerfCounter) {
        lock.lock()
        if active { values[counter, default: 0] += 1 }
        lock.unlock()
    }

    /// Cumulative values of the counters that changed since the previous snapshot, in `PerfCounter.allCases` order.
    func snapshotChanged() -> [(counter: PerfCounter, value: Int)] {
        lock.lock()
        defer { lock.unlock() }
        var changed: [(counter: PerfCounter, value: Int)] = []
        for counter in PerfCounter.allCases {
            let value = values[counter, default: 0]
            guard value != reported[counter, default: 0] else { continue }
            reported[counter] = value
            changed.append((counter, value))
        }
        return changed
    }

    /// `C <pid> <epoch seconds> <name> <cumulative>`
    static func line(pid: Int32, epoch: Double, counter: PerfCounter, value: Int) -> String {
        String(format: "C %d %.3f %@ %d\n", pid, epoch, counter.rawValue, value)
    }
}

/// Frame, CPU and memory sampler for the Home performance harness (scripts/ios/perf-harness.sh).
///
/// The simulator rejects every XCTMetric except the clock ("Invalid parameter not satisfying: _data"), and Instruments'
/// Animation Hitches is "not supported on this platform", so the app measures itself. It does nothing unless the launch
/// environment names a file in SPINE_PERF_PROBE_FILE, and it is compiled out of Release builds.
///
/// A CADisplayLink ticks every frame; a tick later than 1.5 frames after the last one is a hitch, and its overshoot
/// (interval minus one frame) is the hitch time, as Apple defines it. Every 100 ms the probe appends one line:
///
///     S <pid> <epoch seconds> <frames> <hitches> <hitch ms> <longest frame ms> <footprint KB> <cpu ms (cumulative)>
///
/// scripts/ios/perf/report.py slices those lines by the tests' PERFMARK windows. Each flush also appends, for every
/// `PerfCounter` that changed since the previous flush, one line
///
///     C <pid> <epoch seconds> <name> <cumulative count>
enum PerfProbe {
    private static var sampler: Sampler?

    static func startIfRequested() {
        guard sampler == nil,
              let path = ProcessInfo.processInfo.environment["SPINE_PERF_PROBE_FILE"], !path.isEmpty,
              let sampler = Sampler(path: path) else { return }
        self.sampler = sampler
        PerfCounterStore.shared.setActive(true)
        sampler.start()
    }

    private final class Sampler: NSObject {
        private let handle: FileHandle
        private var link: CADisplayLink?
        private var lastTimestamp: CFTimeInterval = 0
        private var bucketStart: CFTimeInterval = 0
        private var frames = 0
        private var hitches = 0
        private var hitchMs = 0.0
        private var longestMs = 0.0
        private let pid = ProcessInfo.processInfo.processIdentifier

        init?(path: String) {
            if !FileManager.default.fileExists(atPath: path) {
                FileManager.default.createFile(atPath: path, contents: nil)
            }
            guard let handle = FileHandle(forWritingAtPath: path) else { return nil }
            handle.seekToEndOfFile()
            self.handle = handle
            super.init()
        }

        func start() {
            let link = CADisplayLink(target: self, selector: #selector(tick(_:)))
            link.add(to: .main, forMode: .common)
            self.link = link
        }

        @objc private func tick(_ link: CADisplayLink) {
            let now = link.timestamp
            let frame = link.targetTimestamp - link.timestamp
            if lastTimestamp > 0, frame > 0 {
                let interval = now - lastTimestamp
                frames += 1
                longestMs = max(longestMs, interval * 1000)
                if interval > frame * 1.5 {
                    hitches += 1
                    hitchMs += (interval - frame) * 1000
                }
            }
            lastTimestamp = now
            if bucketStart == 0 { bucketStart = now }
            guard now - bucketStart >= 0.1 else { return }
            flush()
            bucketStart = now
        }

        private func flush() {
            let epoch = Date().timeIntervalSince1970
            var text = String(
                format: "S %d %.3f %d %d %.1f %.1f %d %d\n",
                pid, epoch, frames, hitches, hitchMs, longestMs, footprintKB(), cpuMs()
            )
            for change in PerfCounterStore.shared.snapshotChanged() {
                text += PerfCounterStore.line(pid: pid, epoch: epoch, counter: change.counter, value: change.value)
            }
            try? handle.write(contentsOf: Data(text.utf8))
            frames = 0
            hitches = 0
            hitchMs = 0
            longestMs = 0
        }

        private func footprintKB() -> Int {
            var info = task_vm_info_data_t()
            var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
            let result = withUnsafeMutablePointer(to: &info) {
                $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                    task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
                }
            }
            return result == KERN_SUCCESS ? Int(info.phys_footprint / 1024) : 0
        }

        private func cpuMs() -> Int {
            var usage = rusage()
            getrusage(RUSAGE_SELF, &usage)
            let user = Double(usage.ru_utime.tv_sec) * 1000 + Double(usage.ru_utime.tv_usec) / 1000
            let system = Double(usage.ru_stime.tv_sec) * 1000 + Double(usage.ru_stime.tv_usec) / 1000
            return Int(user + system)
        }
    }
}
#endif
