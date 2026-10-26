import SwiftUI

struct MediaFilterButton: View {
    enum Surface {
        /// A faint white circle.
        case flat
        /// A Liquid Glass circle.
        case glass
        /// No circle of its own: a toolbar item draws the glass around it.
        case toolbar
    }

    @Binding var filter: MediaFilterState
    let scope: MediaFilterScope
    var options: MediaFilterOptionsResponse = .empty
    var mediaTypes: [String] = APIConstants.fallbackMediaTypes
    var showsTagFilter = true
    var surface: Surface = .flat
    /// For the Streaming Services screen the sheet can open.
    var onUnauthorized: () -> Void = {}
    var onApply: () -> Void

    @State private var isPresented = false

    var body: some View {
        Button {
            isPresented = true
        } label: {
            ZStack(alignment: .topTrailing) {
                Image(systemName: "line.3.horizontal.decrease.circle")
                    .font(.system(size: 18, weight: .semibold))
                    // On glass the icon follows the glass, which turns light over bright artwork.
                    .foregroundStyle(surface == .flat ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                    .frame(width: 34, height: 34)
                    .background(.white.opacity(surface == .flat ? 0.10 : 0), in: Circle())

                if filter.activeCount > 0 {
                    Text("\(filter.activeCount)")
                        .font(.system(size: 9, weight: .heavy))
                        .foregroundStyle(.black)
                        .monospacedDigit()
                        .frame(minWidth: 15, minHeight: 15)
                        .background(.white, in: Circle())
                        .offset(x: 3, y: -3)
                }
            }
        }
        .buttonStyle(.plain)
        .contentShape(.circle)
        .glassEffect(surface == .glass ? .regular.interactive() : .identity, in: .circle)
        .accessibilityLabel(filter.activeCount > 0 ? "Filters, \(filter.activeCount) active" : "Filters")
        .sheet(isPresented: $isPresented) {
            MediaFilterSheet(
                filter: $filter,
                scope: scope,
                options: options,
                mediaTypes: mediaTypes,
                showsTagFilter: showsTagFilter,
                onUnauthorized: onUnauthorized,
                onApply: onApply
            )
        }
    }
}
