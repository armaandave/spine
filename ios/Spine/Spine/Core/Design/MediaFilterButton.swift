import SwiftUI

struct MediaFilterButton: View {
    @Binding var filter: MediaFilterState
    let scope: MediaFilterScope
    var options: MediaFilterOptionsResponse = .empty
    var mediaTypes: [String] = APIConstants.fallbackMediaTypes
    var showsTagFilter = true
    /// A Liquid Glass circle instead of the flat one.
    var usesGlass = false
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
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(.white.opacity(usesGlass ? 0 : 0.10), in: Circle())
                    .glassEffect(usesGlass ? .regular.interactive() : .identity, in: .circle)

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
