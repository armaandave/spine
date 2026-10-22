import SwiftUI
import XCTest
@testable import Spine

@MainActor
final class DetailFactRowLayoutTests: XCTestCase {
    private let rowWidth: CGFloat = 361
    /// Where the row's trailing padding starts.
    private var trailingEdge: CGFloat { rowWidth - 16 }

    func testShortValueSitsAtTrailingEdge() throws {
        let lines = try inkLines(of: DetailFactRow(label: "Format", value: "Main game"))

        XCTAssertEqual(lines.count, 1)
        XCTAssertEqual(try XCTUnwrap(lines.first).maxX, trailingEdge, accuracy: 6)
    }

    /// A value too long for its label's line wraps against the trailing edge instead of dropping under the label.
    func testLongValueWrapsAgainstTrailingEdge() throws {
        let lines = try inkLines(of: DetailFactRow(
            label: "Companies",
            value: "Ubisoft Paris, Ubisoft Entertainment, Ubisoft Bucharest, Ubisoft Reflections, Ubisoft Annecy"
        ))

        XCTAssertGreaterThan(lines.count, 1)
        for line in lines {
            XCTAssertEqual(line.maxX, trailingEdge, accuracy: 6)
        }
    }

    /// The horizontal reach of each line of text the row draws, top to bottom.
    private func inkLines(of row: DetailFactRow) throws -> [(minX: CGFloat, maxX: CGFloat)] {
        let renderer = ImageRenderer(content: DetailFactRowView(
            row: row,
            showsDivider: false,
            onPersonSelected: { _ in },
            onCompanySelected: { _ in }
        )
        .frame(width: rowWidth))
        let image = try XCTUnwrap(renderer.cgImage)
        let width = image.width
        let height = image.height

        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        XCTAssertTrue(drawn)

        var lines: [(minX: CGFloat, maxX: CGFloat)] = []
        var previousRowHadInk = false
        for y in 0..<height {
            let inked = (0..<width).filter { pixels[(y * width + $0) * 4 + 3] > 64 }
            guard let first = inked.first, let last = inked.last else {
                previousRowHadInk = false
                continue
            }
            let reach = (minX: CGFloat(first), maxX: CGFloat(last + 1))
            if previousRowHadInk, let line = lines.popLast() {
                lines.append((min(line.minX, reach.minX), max(line.maxX, reach.maxX)))
            } else {
                lines.append(reach)
            }
            previousRowHadInk = true
        }
        return lines
    }
}
