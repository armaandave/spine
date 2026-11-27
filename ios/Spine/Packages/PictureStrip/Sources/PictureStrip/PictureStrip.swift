import CoreGraphics
import Foundation

/// A strip grown above a picture from the picture's own top area, so it reads as more of the picture rather than a
/// copy of it: small patches of that area are laid from the picture's top edge upward, each one whose edges best match
/// what is already there, and joined along the path where the two differ least (image quilting, Efros and Freeman).
/// Nothing is mirrored, blurred or stretched. Plain tops (sky, dark, walls, smoke, grain) carry on convincingly. A busy
/// top (faces, a sun's rim, a crowded room right at the edge) can't be carried on this way without pasted pieces or
/// repeats, so when the patches match too poorly (`busyLimit`) there is no strip, and the page shows the picture the
/// way it always has.
public enum PictureStrip {
    /// Part of every cache key a strip is stored under (the finished pictures are kept on disk): raise it whenever the
    /// strip comes out differently, or phones keep showing strips grown the old way.
    public static let version = 3

    /// The patches are chosen on a copy this many times smaller, then cut from the picture at full size.
    static let searchScale = 6
    /// A patch and how much it overlaps its neighbors, on the smaller copy (48 and 12 pixels at full size).
    static let block = 8
    static let overlap = 2
    /// How many places in the picture's top area each patch is chosen from.
    static let candidateCount = 1200
    /// A patch is picked at random among those within this much of the best match, so the strip doesn't repeat.
    static let tolerance: Float = 0.12
    /// The patches come from the picture's top edge only, this share of the strip's height deep (at least a patch and
    /// a half): what is right there is what would carry on above it. Deeper down are the faces and objects, which read
    /// as pasted pieces when copied into the strip.
    static let sourceShare = 0.6
    /// How much a patch's overall color must agree with what is already there, besides its edges matching, so the
    /// strip doesn't turn into blocks of different tones.
    static let toneWeight: Float = 0.6
    /// How poorly the patches' edges may match what is already there, on average (summed squared difference of the
    /// three channels on the smaller copy, about 8 levels in 255 per channel), before the top counts as too busy. Plain
    /// tops measured 0.5 to 125 (sky, smoke, walls, dark scenes), busy ones 457 to 1,050 (a kitchen, hair in close-up,
    /// a sun's rim behind hats).
    static let busyLimit: Float = 400

    /// The picture with `band` rows grown above it, or nil when its top is too busy (`busyLimit`) or the picture can't
    /// be read. `sharpRows`: how far down the picture is still sharp; the patches come only from there. The same
    /// picture always grows the same strip.
    public static func grow(_ picture: CGImage, band requestedBand: Int, sharpRows: Int) -> CGImage? {
        let width = picture.width
        let height = picture.height
        let band = max(1, requestedBand)
        guard let pixels = Pixels(picture) else { return nil }

        let scale = searchScale
        let lowWidth = width / scale
        let blockF = block * scale
        let overlapF = overlap * scale
        // The area the patches come from: the sharp top edge of the picture (`sourceShare`).
        let sourceRows = min(sharpRows, height, max(blockF * 3 / 2, Int(Double(band) * sourceShare)))
        let lowSourceRows = sourceRows / scale
        // Too small to grow anything from: no strip.
        guard lowWidth > block * 2, lowSourceRows > block + overlap else { return nil }

        // The smaller copy of the source area, and the strip grown on it first.
        let low = pixels.downsampled(by: scale, rows: lowSourceRows * scale)
        let lowBand = (band + scale - 1) / scale
        let canvasRows = lowBand + overlap
        var canvas = [Float](repeating: 0, count: canvasRows * lowWidth * 3)
        var filled = [Bool](repeating: false, count: canvasRows * lowWidth)
        for row in 0..<overlap {
            for x in 0..<lowWidth {
                for c in 0..<3 {
                    canvas[((lowBand + row) * lowWidth + x) * 3 + c] = low[(row * lowWidth + x) * 3 + c]
                }
                filled[(lowBand + row) * lowWidth + x] = true
            }
        }

        var random = SplitMix(seed: UInt64(truncatingIfNeeded: width &* 73_856_093 ^ height &* 19_349_663))
        let candidates = (0..<candidateCount).map { _ in
            (y: Int(random.next() % UInt64(lowSourceRows - block + 1)),
             x: Int(random.next() % UInt64(lowWidth - block + 1)))
        }
        // Each candidate's overall color, for the tone check.
        let candidateMeans: [(Float, Float, Float)] = candidates.map { candidate in
            var r: Float = 0, g: Float = 0, b: Float = 0
            for row in 0..<block {
                for column in 0..<block {
                    let i = ((candidate.y + row) * lowWidth + candidate.x + column) * 3
                    r += low[i]; g += low[i + 1]; b += low[i + 2]
                }
            }
            let n = Float(block * block)
            return (r / n, g / n, b / n)
        }

        struct Placement { let x, y, width, height, sourceX, sourceY: Int }
        var placements: [Placement] = []
        var bestTotal: Float = 0
        var bestCount = 0
        let step = block - overlap
        var errors = [Float](repeating: 0, count: candidates.count)
        var y = canvasRows - block
        while y > -block + overlap {
            let top = max(y, 0)
            let blockHeight = y + block - top
            var x = 0
            while x < lowWidth {
                let blockWidth = min(block, lowWidth - x)
                // The overall color of what is already there under this patch.
                var tr: Float = 0, tg: Float = 0, tb: Float = 0
                var known = 0
                for row in 0..<blockHeight {
                    for column in 0..<blockWidth where filled[(top + row) * lowWidth + x + column] {
                        let a = ((top + row) * lowWidth + x + column) * 3
                        tr += canvas[a]; tg += canvas[a + 1]; tb += canvas[a + 2]
                        known += 1
                    }
                }
                if known > 0 {
                    tr /= Float(known); tg /= Float(known); tb /= Float(known)
                }
                // How well each candidate matches what is already there: its edges, then its overall tone.
                var best = Float.greatestFiniteMagnitude
                for (index, candidate) in candidates.enumerated() {
                    var sum: Float = 0
                    var count = 0
                    for row in 0..<blockHeight {
                        let canvasRow = (top + row) * lowWidth
                        let sourceRow = (candidate.y + block - blockHeight + row) * lowWidth
                        for column in 0..<blockWidth where filled[canvasRow + x + column] {
                            let a = (canvasRow + x + column) * 3
                            let b = (sourceRow + candidate.x + column) * 3
                            let dr = canvas[a] - low[b], dg = canvas[a + 1] - low[b + 1], db = canvas[a + 2] - low[b + 2]
                            sum += dr * dr + dg * dg + db * db
                            count += 1
                        }
                    }
                    var error = count > 0 ? sum / Float(count) : 0
                    if known > 0 {
                        let mean = candidateMeans[index]
                        let dr = mean.0 - tr, dg = mean.1 - tg, db = mean.2 - tb
                        error += toneWeight * (dr * dr + dg * dg + db * db)
                    }
                    errors[index] = error
                    best = min(best, error)
                }
                let limit = best * (1 + tolerance) + 1e-6
                bestTotal += best
                bestCount += 1
                let good = errors.indices.filter { errors[$0] <= limit }
                let chosen = candidates[good[Int(random.next() % UInt64(good.count))]]
                let sourceY = chosen.y + block - blockHeight
                // Into the small strip: new pixels as they are, the overlap half and half, for the next match.
                for row in 0..<blockHeight {
                    for column in 0..<blockWidth {
                        let cell = (top + row) * lowWidth + x + column
                        let source = ((sourceY + row) * lowWidth + chosen.x + column) * 3
                        let weight: Float = filled[cell] ? 0.5 : 1
                        for c in 0..<3 {
                            canvas[cell * 3 + c] = canvas[cell * 3 + c] * (1 - weight) + low[source + c] * weight
                        }
                        filled[cell] = true
                    }
                }
                placements.append(Placement(
                    x: x, y: top, width: blockWidth, height: blockHeight, sourceX: chosen.x, sourceY: sourceY
                ))
                x += step
            }
            y -= step
        }

        // Too busy to carry on: no strip.
        if bestTotal / Float(max(bestCount, 1)) > busyLimit {
            return nil
        }

        // The same patches cut from the picture at full size, each joined along its least different path.
        let fullRows = lowBand * scale + overlapF
        var strip = StripCanvas(width: width, rows: fullRows)
        for row in 0..<overlapF {
            strip.setRow(fullRows - overlapF + row, from: pixels, row: row)
        }
        for placement in placements {
            let x = placement.x * scale
            let top = placement.y * scale
            let blockWidth = min(placement.width * scale, width - x)
            let blockHeight = min(placement.height * scale, fullRows - top)
            guard blockWidth > 0, blockHeight > 0 else { continue }
            strip.place(
                from: pixels, sourceX: placement.sourceX * scale, sourceY: placement.sourceY * scale,
                x: x, y: top, width: blockWidth, height: blockHeight,
                leftOverlap: placement.x > 0 ? min(overlapF, blockWidth) : 0,
                bottomOverlap: min(overlapF, blockHeight)
            )
        }
        // The few columns past the last whole patch (a width that isn't a multiple of the search scale).
        strip.fillGapsFromTheLeft()

        // The strip's lower `band` rows, then the blended join rows, then the rest of the picture.
        let first = fullRows - overlapF - band
        return pixels.withBand(band, strip: strip, firstRow: first, joinRows: overlapF)
    }
}

// MARK: - Pixels

struct Pixels {
    let width: Int
    let height: Int
    /// RGBA, 8 bits each, sRGB.
    let data: [UInt8]

    init?(_ image: CGImage) {
        let w = image.width
        let h = image.height
        var bytes = [UInt8](repeating: 0, count: w * h * 4)
        let made = bytes.withUnsafeMutableBytes { raw -> Bool in
            guard let context = CGContext(
                data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return false }
            context.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard made else { return nil }
        width = w
        height = h
        data = bytes
    }

    /// The first `rows` rows, `factor` times smaller, as RGB floats.
    func downsampled(by factor: Int, rows: Int) -> [Float] {
        let lowWidth = width / factor
        let lowRows = rows / factor
        var out = [Float](repeating: 0, count: lowWidth * lowRows * 3)
        let area = Float(factor * factor)
        data.withUnsafeBufferPointer { source in
            for y in 0..<lowRows {
                for x in 0..<lowWidth {
                    var r: Float = 0, g: Float = 0, b: Float = 0
                    for dy in 0..<factor {
                        var i = ((y * factor + dy) * width + x * factor) * 4
                        for _ in 0..<factor {
                            r += Float(source[i]); g += Float(source[i + 1]); b += Float(source[i + 2])
                            i += 4
                        }
                    }
                    let o = (y * lowWidth + x) * 3
                    out[o] = r / area; out[o + 1] = g / area; out[o + 2] = b / area
                }
            }
        }
        return out
    }

    /// The picture with the grown strip on top: the strip's rows from `firstRow`, the join rows as blended in the strip,
    /// then the picture below them.
    func withBand(_ band: Int, strip: StripCanvas, firstRow: Int, joinRows: Int) -> CGImage? {
        var out = [UInt8](repeating: 255, count: width * (height + band) * 4)
        for row in 0..<(band + joinRows) {
            strip.write(row: firstRow + row, into: &out, at: row)
        }
        let start = joinRows * width * 4
        out.replaceSubrange((band + joinRows) * width * 4..<(band + height) * width * 4, with: data[start..<data.count])
        return Pixels.image(out, width: width, height: height + band)
    }

    static func image(_ bytes: [UInt8], width: Int, height: Int) -> CGImage? {
        var bytes = bytes
        return bytes.withUnsafeMutableBytes { raw in
            CGContext(
                data: raw.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )?.makeImage()
        }
    }
}

// MARK: - The strip at full size

struct StripCanvas {
    let width: Int
    let rows: Int
    /// RGB floats.
    var color: [Float]
    var filled: [Bool]

    init(width: Int, rows: Int) {
        self.width = width
        self.rows = rows
        color = [Float](repeating: 0, count: width * rows * 3)
        filled = [Bool](repeating: false, count: width * rows)
    }

    mutating func setRow(_ row: Int, from pixels: Pixels, row sourceRow: Int) {
        for x in 0..<width {
            let s = (sourceRow * width + x) * 4
            let o = (row * width + x) * 3
            color[o] = Float(pixels.data[s]); color[o + 1] = Float(pixels.data[s + 1]); color[o + 2] = Float(pixels.data[s + 2])
            filled[row * width + x] = true
        }
    }

    /// Lays a patch of the picture at (x, y), keeping what is already there beyond the least different paths through
    /// its left and bottom overlaps, softened by two pixels (a smooth sky shows a hard cut as a faint dashed line).
    mutating func place(
        from pixels: Pixels, sourceX: Int, sourceY: Int, x: Int, y: Int, width w: Int, height h: Int,
        leftOverlap: Int, bottomOverlap: Int
    ) {
        func patch(_ row: Int, _ column: Int, _ c: Int) -> Float {
            Float(pixels.data[((sourceY + row) * pixels.width + sourceX + column) * 4 + c])
        }
        func here(_ row: Int, _ column: Int, _ c: Int) -> Float {
            color[((y + row) * width + x + column) * 3 + c]
        }
        func difference(_ row: Int, _ column: Int) -> Float {
            var sum: Float = 0
            for c in 0..<3 {
                let d = patch(row, column, c) - here(row, column, c)
                sum += d * d
            }
            return sum
        }

        // 1 = take the patch, 0 = keep what is there.
        var take = [Float](repeating: 1, count: w * h)
        if leftOverlap > 1 {
            let path = Self.leastPath(length: h, across: leftOverlap) { along, across in difference(along, across) }
            for row in 0..<h {
                for column in 0..<path[row] { take[row * w + column] = 0 }
            }
        }
        if bottomOverlap > 1 {
            let path = Self.leastPath(length: w, across: bottomOverlap) { along, across in
                difference(h - bottomOverlap + across, along)
            }
            for column in 0..<w {
                for row in (h - bottomOverlap + path[column] + 1)..<h { take[row * w + column] = 0 }
            }
        }
        // Soften the cut, and always take the patch where nothing is there yet.
        var soft = take
        for row in 0..<h {
            for column in 0..<w {
                var sum: Float = 0
                var count: Float = 0
                for dy in -3...3 {
                    for dx in -3...3 {
                        let r = row + dy, c = column + dx
                        if r >= 0, r < h, c >= 0, c < w { sum += take[r * w + c]; count += 1 }
                    }
                }
                soft[row * w + column] = filled[(y + row) * width + x + column] ? sum / count : 1
            }
        }
        for row in 0..<h {
            for column in 0..<w {
                let t = soft[row * w + column]
                let o = ((y + row) * width + x + column) * 3
                for c in 0..<3 {
                    color[o + c] = patch(row, column, c) * t + color[o + c] * (1 - t)
                }
                filled[(y + row) * width + x + column] = true
            }
        }
    }

    /// The cheapest path along `length` steps through a strip `across` wide (moving at most one step sideways each
    /// time): for each step, where the path crosses.
    static func leastPath(length: Int, across: Int, cost: (Int, Int) -> Float) -> [Int] {
        var total = [Float](repeating: 0, count: length * across)
        for j in 0..<across { total[j] = cost(0, j) }
        if length > 1 {
            for i in 1..<length {
                for j in 0..<across {
                    var best = total[(i - 1) * across + j]
                    if j > 0 { best = min(best, total[(i - 1) * across + j - 1]) }
                    if j < across - 1 { best = min(best, total[(i - 1) * across + j + 1]) }
                    total[i * across + j] = cost(i, j) + best
                }
            }
        }
        var path = [Int](repeating: 0, count: length)
        var j = 0
        for k in 1..<across where total[(length - 1) * across + k] < total[(length - 1) * across + j] { j = k }
        path[length - 1] = j
        if length > 1 {
            for i in stride(from: length - 2, through: 0, by: -1) {
                var bestJ = j
                for k in max(0, j - 1)...min(across - 1, j + 1) where total[i * across + k] < total[i * across + bestJ] {
                    bestJ = k
                }
                j = bestJ
                path[i] = j
            }
        }
        return path
    }

    mutating func fillGapsFromTheLeft() {
        for row in 0..<rows {
            for x in 1..<width where !filled[row * width + x] {
                let o = (row * width + x) * 3
                for c in 0..<3 { color[o + c] = color[o - 3 + c] }
                filled[row * width + x] = true
            }
        }
    }

    func write(row: Int, into out: inout [UInt8], at outRow: Int) {
        for x in 0..<width {
            let s = (row * width + x) * 3
            let o = (outRow * width + x) * 4
            out[o] = UInt8(max(0, min(255, color[s].rounded())))
            out[o + 1] = UInt8(max(0, min(255, color[s + 1].rounded())))
            out[o + 2] = UInt8(max(0, min(255, color[s + 2].rounded())))
            out[o + 3] = 255
        }
    }
}

/// A small fixed-seed random source, so a picture always grows the same strip.
struct SplitMix {
    var state: UInt64

    init(seed: UInt64) { state = seed }

    mutating func next() -> UInt64 {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
