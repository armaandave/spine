#if DEBUG
import Darwin
import Foundation
import UIKit

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
/// scripts/ios/perf/report.py slices those lines by the tests' PERFMARK windows.
enum PerfProbe {
    private static var sampler: Sampler?

    static func startIfRequested() {
        guard sampler == nil,
              let path = ProcessInfo.processInfo.environment["SPINE_PERF_PROBE_FILE"], !path.isEmpty,
              let sampler = Sampler(path: path) else { return }
        self.sampler = sampler
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
            let line = String(
                format: "S %d %.3f %d %d %.1f %.1f %d %d\n",
                pid, Date().timeIntervalSince1970, frames, hitches, hitchMs, longestMs, footprintKB(), cpuMs()
            )
            try? handle.write(contentsOf: Data(line.utf8))
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
