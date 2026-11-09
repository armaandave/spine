import SwiftUI

enum ProgressSaveFollowUp: Equatable {
    case openLog
    case finish
    case close
}

struct ProgressUpdateSaveRequest: Equatable {
    let mode: ProgressUpdateMode
    let value: Decimal
    /// The day the sheet's date row named, "YYYY-MM-DD"; the sheet sets it, a save from elsewhere sends none.
    var progressedOn: String? = nil
}

/// Today's part of a title's progress as a progress sheet counts it: everything done on the title today, not only
/// what this save adds. Pure, so the lit part of the bar, the words beside it and the drag can be tested without a
/// screen.
nonisolated enum ProgressSheetToday {
    static let movedBack = "moved back"

    /// What the title already had today before this save, in the sheet's `unit`: `part` is what Home's tile counts for
    /// today (the server's rows plus what moved on this phone since Home loaded), in the tile's own unit. Pages and
    /// percent convert through the title's page count (`total`); a game's minutes never become percent. It never
    /// exceeds `last`, the place the title stands at: today's part is the end of what was read or played.
    static func before(part: TodayPart?, unit: MeterUnit, total: Int?, last: Decimal?) -> Decimal {
        guard let part, part.amount.isFinite, part.amount > 0 else { return 0 }
        let amount: Double
        if unit == .minutes {
            guard part.unit == .minutes else { return 0 }
            amount = part.amount
        } else {
            guard let converted = TileTodayMath.convert(part, to: todayUnit(unit), total: total.map(Double.init)) else {
                return 0
            }
            amount = converted
        }
        return min(ProgressPercent.rounded(Decimal(amount)), max(last ?? 0, 0))
    }

    /// All of today's progress once the typed number is saved, and whether that number is below where the title stands.
    /// A number above `last` adds its distance to `before`; one at or below it adds nothing.
    static func lit(before: Decimal, last: Decimal?, typed: Decimal?) -> (amount: Decimal, movedBack: Bool) {
        let from = max(last ?? 0, 0)
        guard let typed else { return (before, false) }
        if typed > from { return (before + typed - from, false) }
        return (before, typed < from)
    }

    /// "+20 pages", "+3h" or "moved back" for the lit amount; nil when nothing was done today.
    static func amountText(unit: MeterUnit, lit: Decimal, movedBack: Bool) -> String? {
        if movedBack { return Self.movedBack }
        guard lit > 0 else { return nil }
        return "+\(ProgressSheetBar.amount(lit, unit: unit))"
    }

    /// The caption's words: "+20 pages today", or "moved back" as it is.
    static func line(forAmountText text: String?) -> String? {
        text.map { $0 == movedBack ? $0 : "\($0) today" }
    }

    static func todayUnit(_ unit: MeterUnit) -> TodayUnit {
        switch unit {
        case .episodes: .episodes
        case .chapters: .chapters
        case .issues: .issues
        case .pages: .pages
        case .percent: .percent
        case .minutes: .minutes
        }
    }
}

/// The before/today bar a progress sheet draws in its number's section: earlier progress dim, all of today's part lit
/// in the accent, and a thumb to drag along it. A sheet turns it on through the environment, so `ProgressSheetForm`'s
/// own parameters stay as they are; a sheet that sets nothing draws nothing. The words about today sit in the section's
/// caption (`ProgressSheetTodayLine`), where hours, which have no bar, put them too.
nonisolated struct ProgressSheetBar: Equatable {
    static let height: CGFloat = 14
    static let radius: CGFloat = 7
    /// The thumb's width. The bar stops half a thumb short of each end of its row, so the thumb's center reaches both
    /// ends.
    static let thumb: CGFloat = 26
    static let touchHeight: CGFloat = 44
    /// How many places along the bar a drag ticks at, at most.
    static let maxDetents = 20

    let model: ProgressMeterModel
    let accent: Color
    /// "+20 pages", "+2.5%" or "moved back", counting all of today; nil when nothing was done today.
    let text: String?
    let unit: MeterUnit
    /// The end of the bar: the total, or 100 for percent.
    let end: Decimal
    /// What one step of a drag is: a whole page, chapter or issue, or half a percent.
    let step: Decimal
    /// The number the thumb is at: the typed one, or the last saved one while nothing is typed.
    let value: Decimal
    /// Exact shares of the bar, 0...1: done before today, today's part (which ends at the thumb) and the thumb's place.
    let beforeFraction: Double
    let todayFraction: Double
    let thumbFraction: Double

    var palette: MeterPalette {
        MeterPalette(before: Color(white: 1, opacity: 0.55), rest: Color(white: 1, opacity: 0.14), accent: accent)
    }

    /// The caption's words: "+20 pages today", or "moved back".
    var todayLine: String? { ProgressSheetToday.line(forAmountText: text) }

    /// The bar for `typed` against `last` out of `total` (percent runs to 100), with `todayBefore` already done today.
    /// Nil when the total is unknown. No number typed lights only what was done before this save, a number above the
    /// total stops at it, and one below `last` takes the thumb back with it, says it moved back, and leaves nothing
    /// lit past it.
    static func make(
        unit: MeterUnit,
        last: Decimal?,
        typed: Decimal?,
        total: Int?,
        accent: Color,
        todayBefore: Decimal = 0
    ) -> ProgressSheetBar? {
        let end = unit == .percent ? 100 : total
        guard let end, end > 0 else { return nil }
        let limit = Decimal(end)
        let from = min(max(last ?? 0, 0), limit)
        let to = min(max(typed ?? from, 0), limit)
        let before = min(max(todayBefore, 0), from)
        // Today began where the title stood less what was already done today; going back past that lights nothing.
        let litFrom = min(to, from - before)
        guard let model = ProgressMeterModel.make(unit: unit, from: round(litFrom), to: round(to), total: end) else {
            return nil
        }
        let lit = ProgressSheetToday.lit(before: before, last: from, typed: to)
        let whole = Double(end)
        return ProgressSheetBar(
            model: model,
            accent: accent,
            text: ProgressSheetToday.amountText(unit: unit, lit: lit.amount, movedBack: lit.movedBack),
            unit: unit,
            end: limit,
            step: unit == .percent ? Decimal(string: "0.5") ?? 1 : 1,
            value: to,
            beforeFraction: NSDecimalNumber(decimal: litFrom).doubleValue / whole,
            todayFraction: NSDecimalNumber(decimal: to - litFrom).doubleValue / whole,
            thumbFraction: NSDecimalNumber(decimal: to).doubleValue / whole
        )
    }

    /// The number a drag at `fraction` of the bar's length (0...1) asks for, in whole steps and never off either end.
    func value(atFraction fraction: Double) -> Decimal {
        let stepSize = NSDecimalNumber(decimal: step).doubleValue
        let share = fraction.isNaN ? 0 : fraction
        let units = min(max(share, 0), 1) * NSDecimalNumber(decimal: end).doubleValue
        let stepped = (units / stepSize).rounded() * stepSize
        return min(max(ProgressPercent.rounded(Decimal(stepped)), 0), end)
    }

    /// How many ticks a drag along the whole bar makes: one per step, at most `maxDetents`, so a long book ticks every
    /// few percent instead of buzzing.
    var detentCount: Int {
        min(max(NSDecimalNumber(decimal: end / step).intValue, 1), Self.maxDetents)
    }

    /// Which tick `number` is on, 0...`detentCount`.
    func detent(of number: Decimal) -> Int {
        let share = NSDecimalNumber(decimal: min(max(number, 0), end) / end).doubleValue
        return Int((share * Double(detentCount)).rounded())
    }

    /// The number one VoiceOver swipe up (+1) or down (-1) asks for: a twentieth of the bar, at least one step.
    func adjusted(by direction: Int) -> Decimal {
        let jump = max(value(atFraction: 1 / Double(Self.maxDetents)), step)
        return min(max(value + jump * Decimal(direction), 0), end)
    }

    /// What VoiceOver reads for the bar: "232 of 400 pages", "47.5 percent".
    var spokenValue: String {
        let number = ProgressPercent.text(value)
        let total = ProgressPercent.text(end)
        switch unit {
        case .percent: return "\(number) percent"
        case .pages: return "\(number) of \(total) pages"
        case .chapters: return "\(number) of \(total) chapters"
        case .issues: return "\(number) of \(total) issues"
        case .episodes: return "\(number) of \(total) episodes"
        case .minutes: return number
        }
    }

    private static func round(_ value: Decimal) -> Int {
        Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
    }

    static func amount(_ delta: Decimal, unit: MeterUnit) -> String {
        let number = ProgressPercent.text(delta)
        switch unit {
        case .percent: return "\(number)%"
        case .pages: return "\(number) page\(delta == 1 ? "" : "s")"
        case .chapters: return "\(number) chapter\(delta == 1 ? "" : "s")"
        case .issues: return "\(number) issue\(delta == 1 ? "" : "s")"
        case .episodes: return "\(number) episode\(delta == 1 ? "" : "s")"
        case .minutes: return GameProgressValues.timeLabel(NSDecimalNumber(decimal: delta).intValue)
        }
    }
}

/// The accent-colored words about today ("+20 pages today", "+3h today") at the trailing end of a progress sheet's
/// caption. Hidden while the sheet shows a warning.
nonisolated struct ProgressSheetTodayLine: Equatable {
    let text: String
    let accent: Color
}

extension EnvironmentValues {
    @Entry var progressSheetBar: ProgressSheetBar? = nil
    @Entry var progressSheetTodayLine: ProgressSheetTodayLine? = nil
    /// Moves the sheet's number: a drag along the bar asks for a value, and the sheet writes it where it keeps what the
    /// person typed.
    @Entry var progressSheetSetNumber: (Decimal) -> Void = { _ in }
    /// What Home's tile counts as today's part of a title, in the tile's unit: its server rows plus what moved on this
    /// phone since Home loaded. Home supplies it; a sheet opened from a title's page has none and counts only its own save.
    @Entry var progressSheetTodayPart: ((MediaRef) -> TodayPart?)? = nil
}

/// The bar in its own row, inside the number's section: as wide as the section's text, a thumb on it, and a drag
/// anywhere along the row sets the number. Typing stays the main way in; this is the quick way to a place.
private struct ProgressSheetBarRow: View {
    let bar: ProgressSheetBar
    @Environment(\.progressSheetSetNumber) private var setNumber
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var width: CGFloat = 0
    /// True while a finger is down on the bar, and false again however the touch ends.
    @GestureState private var isDragging = false
    /// The tick the drag is on, for the selection haptic.
    @State private var detent = 0

    private var travel: CGFloat { max(width - ProgressSheetBar.thumb, 0) }

    var body: some View {
        ZStack(alignment: .leading) {
            MeterSegmentsLayout(before: bar.beforeFraction, today: bar.todayFraction) {
                Rectangle().fill(bar.palette.before)
                Rectangle().fill(bar.palette.accent)
            }
            .frame(height: ProgressSheetBar.height)
            .background(bar.palette.rest)
            .clipShape(RoundedRectangle(cornerRadius: ProgressSheetBar.radius, style: .continuous))
            .padding(.horizontal, ProgressSheetBar.thumb / 2)

            Circle()
                .fill(Color.white)
                .frame(width: ProgressSheetBar.thumb, height: ProgressSheetBar.thumb)
                .shadow(color: .black.opacity(0.45), radius: 7, y: 2)
                .offset(x: travel * bar.thumbFraction)
                .opacity(width > 0 ? 1 : 0)
        }
        .frame(maxWidth: .infinity)
        .frame(height: ProgressSheetBar.touchHeight)
        // Typed numbers glide; a drag follows the finger with no lag.
        .animation(
            isDragging || reduceMotion ? nil : .easeInOut(duration: 0.25),
            value: [bar.beforeFraction, bar.todayFraction, bar.thumbFraction]
        )
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .updating($isDragging) { _, state, _ in state = true }
                .onChanged { drag in
                    guard travel > 0 else { return }
                    let share = Double((drag.location.x - ProgressSheetBar.thumb / 2) / travel)
                    let value = bar.value(atFraction: share)
                    if value != bar.value { setNumber(value) }
                    let next = bar.detent(of: value)
                    if next != detent { detent = next }
                }
        )
        .sensoryFeedback(.selection, trigger: detent)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Progress")
        .accessibilityValue(bar.spokenValue)
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: setNumber(bar.adjusted(by: 1))
            case .decrement: setNumber(bar.adjusted(by: -1))
            @unknown default: break
            }
        }
    }
}

/// The title a progress sheet updates. A Home card knows it before the title's page has loaded, so the sheet opens at
/// once; the page's own copy takes over once it has.
struct ProgressSheetTitle: Identifiable, Equatable {
    let ref: MediaRef
    let title: String
    /// A book's page count: from its page, or from a card's page progress. A manga's chapters or a comic's issues so
    /// far, when the provider lists them (MC-002, MC-003).
    let totalPages: Int?
    /// The poster's accent color, "#RRGGBB", which colors the before/today bar.
    let posterAccentHex: String?

    var id: String { ref.id }

    init(detail: MediaDetail) {
        ref = detail.ref
        posterAccentHex = detail.posterAccentColor
        title = detail.displayTitle
        totalPages = detail.ref.isSeries ? detail.userState?.book?.total : detail.progressTotalPages
    }

    init(item: LibraryItem) {
        ref = item.media.ref
        posterAccentHex = item.media.posterAccentColor
        title = item.media.displayTitle
        // Percentage progress's max is 100, not a page count.
        totalPages = item.tracking.progress.flatMap { progress in
            progress.mode == .pages ? progress.max.map { NSDecimalNumber(decimal: $0).intValue } : nil
        }
    }
}

@MainActor
@Observable
final class ProgressUpdateViewModel {
    var title: ProgressSheetTitle
    let progress: ProgressState?
    var mode: ProgressUpdateMode
    var input = ""
    /// The number the server last refused.
    var refusedValue: Decimal?
    private var hasEditedInput = false

    convenience init(detail: MediaDetail, progress: ProgressState?) {
        self.init(title: ProgressSheetTitle(detail: detail), progress: progress)
    }

    init(title: ProgressSheetTitle, progress: ProgressState?) {
        self.title = title
        self.progress = progress
        self.mode = Self.defaultMode(for: title, progress: progress)
        if let lastValue {
            input = ProgressPercent.text(lastValue)
        }
    }

    /// Percentage progress's total is 100, not a page count.
    var totalPages: Int? {
        title.totalPages ?? (progress?.mode == .pages ? Self.intValue(progress?.max) : nil)
    }

    var lastValue: Decimal? {
        guard let progress else { return nil }
        // Percentage progress's max is 100, not a page count, so its page comes from the book's own count.
        if progress.mode == .percentage, mode == .pages, let percent = progress.value(in: .percentage) {
            return converted(percent, from: .percentage, to: .pages)
        }
        if mode == .percentage, ProgressDisplayPreferences.mode(for: title.ref) == .percentage {
            return progress.savedPercentage
        }
        return progress.value(in: mode)
    }

    var currentValue: Decimal? {
        ProgressPercent.value(input)
    }

    /// The field's text: empty until the first digit, so the last saved value shows as its placeholder and the first
    /// digit typed replaces it.
    var keyboardInputText: String {
        hasEditedInput ? input : ""
    }

    /// A manga's or a comic's unit: "chapter" or "issue" (MC-001).
    private var countedUnit: String? {
        guard title.ref.isSeries else { return nil }
        return title.ref.mediaType == "comic" ? "issue" : "chapter"
    }

    private func counted(_ count: Decimal, unit: String) -> String {
        "\(ProgressPercent.text(count)) \(unit)\(count == 1 ? "" : "s")"
    }

    /// Under the number: where the last save left off, and how far this one moves from it. Nil before a first save.
    var summaryText: String? {
        guard let lastValue else { return nil }
        if let countedUnit {
            guard let currentValue, currentValue != lastValue else {
                return "Last saved at \(counted(lastValue, unit: countedUnit))"
            }
            let amount = counted(abs(currentValue - lastValue), unit: countedUnit)
            return "\(currentValue > lastValue ? "+" : "−")\(amount) since \(ProgressPercent.text(lastValue))"
        }
        let lastText = ProgressPercent.text(lastValue)
        let last = mode == .percentage ? "\(lastText)%" : "page \(lastText)"
        guard let currentValue, currentValue != lastValue else { return "Last saved at \(last)" }
        let delta = abs(currentValue - lastValue)
        let deltaText = ProgressPercent.text(delta)
        let amount = mode == .percentage ? "\(deltaText)%" : delta == 1 ? "1 page" : "\(deltaText) pages"
        return "\(currentValue > lastValue ? "+" : "−")\(amount) since \(last)"
    }

    var validationMessage: String? {
        guard let value = currentValue else { return nil }
        if let countedUnit {
            // A comic's number goes to the server, which asks Comic Vine again first (MC-004).
            guard title.ref.mediaType == "manga", let totalPages, value > Decimal(totalPages) else { return nil }
            return "This manga has \(counted(Decimal(totalPages), unit: countedUnit))."
        }
        if mode == .percentage, value > 100 {
            return "Enter a value from 0 to 100."
        }
        if mode == .pages, let totalPages, value > Decimal(totalPages) {
            return "Enter a value from 0 to \(totalPages)."
        }
        return nil
    }

    /// A series with a total of one has no number (MC-005), and a paused one takes none until it is resumed, as the
    /// server says. A book's sheet keeps its number.
    func showsNumber(journeyStatus: String?) -> Bool {
        !(title.ref.isSeries && (title.totalPages == 1 || journeyStatus == "Paused"))
    }

    /// What follows a saved number. A manga's or a comic's log screen opens when the server says so, once per attempt
    /// (MC-100, MC-101). A book's opens at its last page.
    static func afterSave(isSeries: Bool, opensLog: Bool, reachedEnd: Bool) -> ProgressSaveFollowUp {
        if isSeries { return opensLog ? .openLog : .close }
        return reachedEnd ? .finish : .close
    }

    /// The warning under the number. A manga's or a comic's refusal from the server goes with the number it refused,
    /// so it doesn't sit beside a corrected one (MC-004).
    func warning(serverMessage: String?) -> String? {
        if let validationMessage { return validationMessage }
        guard title.ref.isSeries else { return serverMessage }
        return currentValue == refusedValue ? serverMessage : nil
    }

    var canSave: Bool {
        guard currentValue != nil, validationMessage == nil else { return false }
        return currentValue != lastValue
    }

    var isFullProgress: Bool {
        guard let currentValue else { return false }
        if mode == .percentage {
            return currentValue == 100
        }
        return totalPages.map { currentValue == Decimal($0) } ?? false
    }

    var saveRequest: ProgressUpdateSaveRequest? {
        guard canSave, let currentValue else { return nil }
        return ProgressUpdateSaveRequest(mode: mode, value: currentValue)
    }

    func selectMode(_ newMode: ProgressUpdateMode) {
        guard title.ref.mediaType == "book", newMode != mode else { return }
        // Nothing typed: the new mode's last value, not its rounding of the old one, which would look like a change.
        let isLastValue = input == lastValue.map(ProgressPercent.text)
        let typed = currentValue.flatMap { converted($0, from: mode, to: newMode) }
        mode = newMode
        input = (isLastValue ? lastValue : typed).map(ProgressPercent.text) ?? ""
    }

    func updateInput(_ rawValue: String) {
        let digits = mode == .percentage
            ? ProgressPercent.sanitized(rawValue)
            : String(rawValue.filter(\.isNumber).prefix(6))
        if !hasEditedInput, digits == input {
            // The first digit typed is the last value itself: it stays, so the next digit adds to it.
            hasEditedInput = true
            return
        }
        guard !digits.isEmpty else {
            input = ""
            hasEditedInput = true
            return
        }
        if !hasEditedInput {
            let replacement = !input.isEmpty && digits.hasPrefix(input) ? String(digits.dropFirst(input.count)) : digits
            input = replacement.isEmpty ? digits : replacement
        } else {
            input = digits
        }
        hasEditedInput = true
    }

    /// A drag along the bar sets the number as if it had been typed.
    func setNumber(_ value: Decimal) {
        input = ProgressPercent.text(value)
        hasEditedInput = true
    }

    private func converted(_ value: Decimal, from: ProgressUpdateMode, to: ProgressUpdateMode) -> Decimal? {
        guard from != to else { return value }
        guard let totalPages, totalPages > 0 else { return nil }
        let number = NSDecimalNumber(decimal: value).doubleValue
        switch (from, to) {
        case (.pages, .percentage):
            return Decimal(Int((number / Double(totalPages) * 100).rounded()))
        case (.percentage, .pages):
            return Decimal(Int((number / 100 * Double(totalPages)).rounded()))
        default:
            return value
        }
    }

    private static func defaultMode(for title: ProgressSheetTitle, progress: ProgressState?) -> ProgressUpdateMode {
        // A manga or a comic counts one whole number (MC-001).
        if title.ref.isSeries { return .pages }
        guard title.ref.mediaType == "book" else { return .percentage }
        if let preferredMode = ProgressDisplayPreferences.mode(for: title.ref) {
            return preferredMode
        }
        if let progress {
            return progress.mode
        }
        return title.totalPages == nil ? .percentage : .pages
    }

    private static func intValue(_ value: Decimal?) -> Int? {
        guard let value else { return nil }
        return Int(NSDecimalNumber(decimal: value).doubleValue.rounded())
    }
}

/// Update Progress for a book, a manga or a comic, a native sheet like the TV episode sheet: the title in the bar with
/// Close, ⋯ and Save, the page or count against the last saved one, and Finished Reading. Pause, Did Not Finish, Restart
/// and Delete sit in ⋯. Its choices close it; their writes apply behind it. A series with a total of one has no number
/// (MC-005).
struct ProgressUpdateSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ProgressUpdateViewModel
    @State private var pendingJourneyAction: BookJourneyMenuAction?
    /// A save waiting for its answer, or for the page it writes to.
    @State private var isSubmitting = false
    /// A choice closed the sheet: a second tap while it slides away does nothing.
    @State private var isClosing = false
    /// The day this save is on, today until the date row says otherwise.
    @State private var date = Date()
    /// What the title had today before the save that is on its way. Home's tile moves with the write, so the bar
    /// keeps this until the sheet has gone, and counts the saved number once.
    @State private var todayBeforeSave: Decimal?
    @Environment(\.progressSheetTodayPart) private var todayPart

    let title: ProgressSheetTitle
    let isSaving: Bool
    let errorMessage: String?
    let journeyStatus: String?
    /// The earliest day the date row offers: the start or the last save, whichever is later.
    let dateFloor: Date?
    let onSave: (ProgressUpdateSaveRequest) async -> Bool
    /// Finished Reading, or progress saved at the last page: the completion log, which closes the sheet first.
    let onFinish: () -> Void
    /// A manga or a comic: whether the save just made opens the log screen (MC-100, MC-101); the server says, once per
    /// attempt, and `onReachedTotal` opens it.
    let opensLogAfterSave: () -> Bool
    let onReachedTotal: () -> Void
    /// "pause", "resume", "drop" or "restart".
    let onAction: (String) -> Void
    let onDelete: () -> Void

    init(
        title: ProgressSheetTitle,
        progress: ProgressState?,
        isSaving: Bool,
        errorMessage: String?,
        journeyStatus: String?,
        dateFloor: Date? = nil,
        onSave: @escaping (ProgressUpdateSaveRequest) async -> Bool,
        onFinish: @escaping () -> Void,
        opensLogAfterSave: @escaping () -> Bool = { false },
        onReachedTotal: @escaping () -> Void = {},
        onAction: @escaping (String) -> Void,
        onDelete: @escaping () -> Void
    ) {
        _viewModel = State(initialValue: ProgressUpdateViewModel(title: title, progress: progress))
        self.title = title
        self.isSaving = isSaving
        self.errorMessage = errorMessage
        self.journeyStatus = journeyStatus
        self.dateFloor = dateFloor
        self.onSave = onSave
        self.onFinish = onFinish
        self.opensLogAfterSave = opensLogAfterSave
        self.onReachedTotal = onReachedTotal
        self.onAction = onAction
        self.onDelete = onDelete
    }

    private var numberLabel: String {
        if title.ref.isSeries { return title.ref.mediaType == "comic" ? "Issues" : "Chapters" }
        return viewModel.mode == .pages ? "Page" : "Progress"
    }

    private var meterUnit: MeterUnit {
        if title.ref.isSeries { return title.ref.mediaType == "comic" ? .issues : .chapters }
        return viewModel.mode == .pages ? .pages : .percent
    }

    private var accent: Color {
        ArtAccent.pastel(fromHexOrNil: title.posterAccentHex) ?? HomeDesign.Palette.starGold
    }

    /// What the title already had today, in this sheet's unit, before the number typed here.
    private var todayBefore: Decimal {
        todayBeforeSave ?? ProgressSheetToday.before(
            part: todayPart?(title.ref),
            unit: meterUnit,
            total: viewModel.totalPages,
            last: viewModel.lastValue
        )
    }

    /// The before/today bar: pages or percent for a book, chapters or issues for a manga or comic. Nil when the total is
    /// unknown. Its lit part is all of today's progress on the title, as Home's tile shows it, and follows what is typed.
    private var progressBar: ProgressSheetBar? {
        ProgressSheetBar.make(
            unit: meterUnit,
            last: viewModel.lastValue,
            typed: viewModel.currentValue,
            total: viewModel.totalPages,
            accent: accent,
            todayBefore: todayBefore
        )
    }

    /// Today's words for the caption. Without a total there is no bar, but a number typed or already done today still
    /// has its line.
    private func todayLine(bar: ProgressSheetBar?) -> ProgressSheetTodayLine? {
        if let bar { return bar.todayLine.map { ProgressSheetTodayLine(text: $0, accent: accent) } }
        let lit = ProgressSheetToday.lit(
            before: todayBefore,
            last: viewModel.lastValue,
            typed: viewModel.currentValue
        )
        return ProgressSheetToday.line(
            forAmountText: ProgressSheetToday.amountText(unit: meterUnit, lit: lit.amount, movedBack: lit.movedBack)
        ).map { ProgressSheetTodayLine(text: $0, accent: accent) }
    }

    /// What ⋯ and its dialogs call the title and this read: "book" and "journey", or the type's noun and "attempt".
    private var words: BookJourneyMenuAction.Words {
        title.ref.isSeries
            ? BookJourneyMenuAction.Words(noun: title.ref.mediaType, attempt: "attempt")
            : BookJourneyMenuAction.Words(noun: "book", attempt: "journey")
    }

    var body: some View {
        let warning = viewModel.warning(serverMessage: errorMessage)
        let bar = progressBar
        ProgressSheetForm(
            title: title.title,
            subtitle: journeyStatus == "Paused" ? "Paused" : nil,
            showsNumber: viewModel.showsNumber(journeyStatus: journeyStatus),
            numberLabel: numberLabel,
            number: Binding(get: { viewModel.keyboardInputText }, set: { viewModel.updateInput($0) }),
            placeholder: viewModel.lastValue.map(ProgressPercent.text) ?? "0",
            allowsDecimal: viewModel.mode == .percentage,
            unit: viewModel.mode == .pages ? viewModel.totalPages.map { "of \($0)" } ?? "" : "%",
            footer: warning ?? viewModel.summaryText,
            footerIsWarning: warning != nil,
            finishTitle: "Finished Reading",
            canSave: viewModel.canSave && !isClosing,
            isSaving: isSaving || isSubmitting,
            date: $date,
            dateFloor: dateFloor,
            onSave: save,
            onFinish: { perform(.finish) }
        ) {
            if title.ref.mediaType == "book" {
                Picker("Track by", selection: Binding(get: { viewModel.mode }, set: { viewModel.selectMode($0) })) {
                    ForEach(ProgressUpdateMode.allCases) { Text($0.title).tag($0) }
                }
            }
        } moreActions: {
            menuButton(journeyStatus == "Paused" ? .resume : .pause)
            menuButton(.didNotFinish)
            Divider()
            menuButton(.restart)
            menuButton(.delete)
        }
        .environment(\.progressSheetBar, bar)
        .environment(\.progressSheetTodayLine, todayLine(bar: bar))
        .environment(\.progressSheetSetNumber, { viewModel.setNumber($0) })
        // The page's copy, once it has loaded behind a sheet opened from Home, knows the book's page count.
        .onChange(of: title) { _, title in viewModel.title = title }
        .confirmationDialog(
            pendingJourneyAction?.confirmationTitle(words) ?? "",
            isPresented: Binding(
                get: { pendingJourneyAction != nil },
                set: { if !$0 { pendingJourneyAction = nil } }
            ),
            titleVisibility: .visible
        ) {
            if let action = pendingJourneyAction {
                Button(action.title(words), role: action.isDestructive ? .destructive : nil) {
                    perform(action)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(pendingJourneyAction?.confirmationMessage(words) ?? "")
        }
    }

    private func menuButton(_ action: BookJourneyMenuAction) -> some View {
        Button(action.title(words), systemImage: action.systemImage, role: action == .delete ? .destructive : nil) {
            if action.requiresConfirmation {
                pendingJourneyAction = action
            } else {
                perform(action)
            }
        }
    }

    private func save() {
        guard let undated = viewModel.saveRequest, !isSubmitting, !isClosing else { return }
        let request = undated.dated(date)
        let finishes = viewModel.isFullProgress
        isSubmitting = true
        todayBeforeSave = todayBefore
        Task {
            let saved = await onSave(request)
            isSubmitting = false
            guard saved else {
                todayBeforeSave = nil
                viewModel.refusedValue = request.value
                return
            }
            isClosing = true
            switch ProgressUpdateViewModel.afterSave(
                isSeries: title.ref.isSeries,
                opensLog: opensLogAfterSave(),
                reachedEnd: finishes
            ) {
            case .openLog: onReachedTotal()
            case .finish: onFinish()
            case .close: dismiss()
            }
        }
    }

    private func perform(_ action: BookJourneyMenuAction) {
        pendingJourneyAction = nil
        guard !isClosing, !isSubmitting else { return }
        isClosing = true
        switch action {
        case .pause: onAction("pause")
        case .resume: onAction("resume")
        case .didNotFinish: onAction("drop")
        case .restart: onAction("restart")
        case .delete: onDelete()
        case .finish:
            // The completion log closes the sheet before it opens.
            onFinish()
            return
        }
        dismiss()
    }
}

/// A progress sheet's layout, for a book or a game, in the look of the TV episode sheet and the log sheet: the title
/// in the bar with Close, ⋯ and Save; one number against the last saved one, and how it's counted; and Finished. The
/// number pad rises with the sheet.
struct ProgressSheetForm<CountOptions: View, MoreActions: View>: View {
    @Environment(\.dismiss) private var dismiss
    @State private var focusRequest = 0
    /// The date row's calendar is open. The number pad is down then, which must not close the sheet.
    @State private var isPickingDate = false
    @Environment(\.progressSheetBar) private var progressBar
    @Environment(\.progressSheetTodayLine) private var todayLine

    let title: String
    let subtitle: String?
    /// False leaves out the number and Save: a series with a total of one has only its status actions (MC-005).
    let showsNumber: Bool
    let numberLabel: String
    @Binding var number: String
    let placeholder: String
    let allowsDecimal: Bool
    let unit: String
    /// Under the number: the last save, or what's wrong with this one.
    let footer: String?
    let footerIsWarning: Bool
    /// Finished Reading or Finished Playing; nil leaves it out.
    let finishTitle: String?
    let canSave: Bool
    let isSaving: Bool
    /// The day the save is on; nil leaves the date row out.
    let date: Binding<Date>?
    /// The earliest day the calendar offers; nil leaves it open.
    let dateFloor: Date?
    /// False leaves out ⋯, for a sheet whose only choice is its number.
    let showsMore: Bool
    let onSave: () -> Void
    let onFinish: () -> Void
    let countOptions: CountOptions
    let moreActions: MoreActions

    init(
        title: String,
        subtitle: String?,
        showsNumber: Bool = true,
        numberLabel: String,
        number: Binding<String>,
        placeholder: String,
        allowsDecimal: Bool,
        unit: String,
        footer: String?,
        footerIsWarning: Bool,
        finishTitle: String?,
        canSave: Bool,
        isSaving: Bool,
        date: Binding<Date>? = nil,
        dateFloor: Date? = nil,
        showsMore: Bool = true,
        onSave: @escaping () -> Void,
        onFinish: @escaping () -> Void,
        @ViewBuilder countOptions: () -> CountOptions,
        @ViewBuilder moreActions: () -> MoreActions
    ) {
        self.title = title
        self.subtitle = subtitle
        self.showsNumber = showsNumber
        self.numberLabel = numberLabel
        _number = number
        self.placeholder = placeholder
        self.allowsDecimal = allowsDecimal
        self.unit = unit
        self.footer = footer
        self.footerIsWarning = footerIsWarning
        self.finishTitle = finishTitle
        self.canSave = canSave
        self.isSaving = isSaving
        self.date = date
        self.dateFloor = dateFloor
        self.showsMore = showsMore
        self.onSave = onSave
        self.onFinish = onFinish
        self.countOptions = countOptions()
        self.moreActions = moreActions()
    }

    var body: some View {
        NavigationStack {
            Form {
                if showsNumber {
                    Section {
                        HStack(spacing: 6) {
                            Text(numberLabel)
                            Spacer(minLength: 12)
                            AutofocusNumberField(
                                text: $number,
                                placeholder: placeholder,
                                accessibilityLabel: numberLabel,
                                focusRequest: focusRequest,
                                allowsDecimal: allowsDecimal
                            )
                            if !unit.isEmpty {
                                Text(unit)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            isPickingDate = false
                            focusRequest += 1
                        }
                        .listRowSeparator(progressBar == nil ? .automatic : .hidden, edges: .bottom)

                        // The bar belongs to the number above it: as wide as the section's text, with its thumb's
                        // overhang taking the row's own padding.
                        if let progressBar {
                            ProgressSheetBarRow(bar: progressBar)
                                .listRowInsets(EdgeInsets(top: 0, leading: 3, bottom: 0, trailing: 3))
                                .listRowSeparator(.hidden, edges: .top)
                        }

                        countOptions

                        if let date {
                            dateRow(date)
                        }
                    } footer: {
                        caption
                    }
                    .logRowBackground()
                }

                if let finishTitle {
                    Section {
                        Button(finishTitle, systemImage: "checkmark.circle", action: onFinish)
                    }
                    .logRowBackground()
                }
            }
            .scrollContentBackground(.hidden)
            // A slow drag down starts as a scroll of the rows, which would lower the number pad and leave the sheet
            // with nothing to type into; the drag moves the sheet instead.
            .scrollDismissesKeyboard(.never)
            // The sheet rides on the number pad, so the keyboard takes no room inside it.
            .ignoresSafeArea(.keyboard)
            .fitsSheetToContent(aboveKeyboard: showsNumber)
            .disabled(isSaving)
            // Saving takes the number pad away. After a refusal the number still needs fixing, so it comes back.
            .onChange(of: isSaving) { _, saving in
                if !saving, footerIsWarning { focusRequest += 1 }
            }
            .navigationTitle(title)
            .navigationSubtitle(subtitle ?? "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(role: .close) {
                        dismiss()
                    } label: {
                        Label("Close", systemImage: "xmark")
                    }
                    .disabled(isSaving)
                }
                if showsMore {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            moreActions
                        } label: {
                            Label("More", systemImage: "ellipsis")
                        }
                        .disabled(isSaving)
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSaving {
                        ProgressView()
                    } else if showsNumber {
                        Button(role: .confirm, action: onSave) {
                            Label("Save", systemImage: "checkmark")
                        }
                        .disabled(!canSave)
                        .accessibilityLabel("Save Progress")
                    }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
        .interactiveDismissDisabled(isSaving)
        // Dragging the sheet down lowers the number pad even when the sheet springs back. Without it the sheet has
        // nothing to do, so it closes all the way. Not while a save is out (its failure shows here), nor when the app
        // leaves the screen.
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in
            guard !isSaving, !isPickingDate, UIApplication.shared.applicationState == .active else { return }
            dismiss()
        }
    }

    /// Under the section: the last save or what's wrong with this one, and, at the trailing end in the accent, today's
    /// part ("+20 pages today"). A warning leaves today's words out, since the number they count is not one that saves.
    @ViewBuilder
    private var caption: some View {
        let today = footerIsWarning ? nil : todayLine
        if footer != nil || today != nil {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                if let footer {
                    Text(footer)
                        .foregroundStyle(footerIsWarning ? Color.red : Color.secondary)
                }
                Spacer(minLength: 0)
                if let today {
                    Text(today.text)
                        .font(.footnote.weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(today.accent)
                        .lineLimit(1)
                        .fixedSize()
                        .accessibilityLabel(today.text)
                }
            }
        }
    }

    /// Nothing before the last save: an older day is fixed from its row with Edit. A floor in the future (a clock
    /// behind the server's) falls back to today, so the range is never empty.
    private var dateRange: ClosedRange<Date> {
        let now = Date()
        guard let dateFloor, dateFloor <= now else { return Date.distantPast...now }
        return dateFloor...now
    }

    /// "Date  Today": a tap opens the calendar under it and lowers the number pad; the number's row brings the pad
    /// back. The calendar stops at today, since a save can't be a day ahead.
    @ViewBuilder
    private func dateRow(_ date: Binding<Date>) -> some View {
        Button {
            if isPickingDate {
                isPickingDate = false
                focusRequest += 1
            } else {
                // Before the pad goes down, so its hiding doesn't close the sheet.
                isPickingDate = true
                UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            }
        } label: {
            HStack {
                Text("Date")
                    .foregroundStyle(.primary)
                Spacer(minLength: 12)
                Text(ProgressDateWords.text(for: date.wrappedValue))
                    .foregroundStyle(isPickingDate ? Color.accentColor : Color.secondary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Date")
        .accessibilityValue(ProgressDateWords.text(for: date.wrappedValue))

        if isPickingDate {
            DatePicker("Date", selection: date, in: dateRange, displayedComponents: .date)
                .datePickerStyle(.graphical)
                .labelsHidden()
        }
    }
}

private enum BookJourneyMenuAction: String, CaseIterable, Identifiable {
    case pause
    case resume
    case didNotFinish
    case finish
    case restart
    case delete

    /// A book has journeys; a manga or a comic has attempts (MC-000).
    struct Words {
        let noun: String
        let attempt: String
    }

    var id: String { rawValue }
    func title(_ words: Words) -> String {
        switch self {
        case .pause: "Pause"
        case .resume: "Resume Reading"
        case .didNotFinish: "Mark as DNF"
        case .finish: "Finish & Log"
        case .restart: "Restart from Beginning"
        case .delete: "Delete \(words.attempt.capitalized)"
        }
    }
    var systemImage: String {
        switch self {
        case .pause: "pause.fill"
        case .resume: "play.fill"
        case .didNotFinish: "xmark"
        case .finish: "checkmark"
        case .restart: "arrow.counterclockwise"
        case .delete: "trash"
        }
    }
    var requiresConfirmation: Bool { self == .didNotFinish || self == .restart || self == .delete }
    var isDestructive: Bool { self == .delete || self == .restart }
    func confirmationTitle(_ words: Words) -> String {
        switch self {
        case .didNotFinish: "Mark this \(words.noun) as Did Not Finish?"
        case .restart: "Restart from Beginning?"
        case .delete: "Delete this reading \(words.attempt)?"
        default: title(words)
        }
    }
    func confirmationMessage(_ words: Words) -> String {
        switch self {
        case .didNotFinish: "Your current progress will remain in reading history."
        case .restart: "The current \(words.attempt) will end and a new \(words.attempt) will begin today."
        case .delete: "This \(words.attempt) and its progress will be removed. Completion diary logs are managed separately."
        default: ""
        }
    }
}

extension MediaDetail {
    var progressTotalPages: Int? {
        for key in ["number_of_pages", "pages", "total_pages"] {
            if let value = details?[key]?.progressIntValue {
                return value
            }
        }
        return nil
    }
}

private extension JSONValue {
    var progressIntValue: Int? {
        switch self {
        case let .number(value):
            Int(value)
        case let .string(value):
            Int(value)
        default:
            nil
        }
    }
}
