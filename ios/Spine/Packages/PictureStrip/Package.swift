// swift-tools-version: 6.0
import PackageDescription

// Grows the strip that sits behind the status bar above a picture (Home's picture, backdrops, episode stills).
// It is its own package only so it can be compiled optimized in every build: the patch search is tight loops that
// take seconds unoptimized (Spine Dev's Debug build) and tens of milliseconds optimized.
// Xcode Cloud starts a TestFlight build only for changes under ios/Spine/Spine, Spine.xcodeproj or Configs, not this
// folder: a change made only here ships no new build.
let package = Package(
    name: "PictureStrip",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "PictureStrip", targets: ["PictureStrip"]),
    ],
    targets: [
        .target(
            name: "PictureStrip",
            swiftSettings: [.unsafeFlags(["-O"])]
        ),
    ]
)
