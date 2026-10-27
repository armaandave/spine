import SwiftUI

/// Every list progress ring from the page's payload (STAT-213), most complete first. Rings are
/// lifetime and the payload sends them all, so this list needs no request. A row opens the list.
struct StatsListProgressListView: View {
    let rings: [StatsListRing]
    let services: StatsRouteServices

    @State private var route: StatsRoute?

    var body: some View {
        let sorted = StatsListProgressData.sorted(rings)

        ScrollView(showsIndicators: false) {
            LazyVStack(alignment: .leading, spacing: 0) {
                Text(StatsChartCopy.count(sorted.count, "list", "lists"))
                    .font(.title2.weight(.bold))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .padding(.leading, 4)
                    .padding(.bottom, 20)

                ForEach(Array(sorted.enumerated()), id: \.element.id) { index, ring in
                    StatsListRingRow(
                        ring: ring,
                        isFirst: index == 0,
                        isLast: index == sorted.count - 1
                    ) {
                        route = .list(id: ring.id)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 10)
            .padding(.bottom, 40)
        }
        .background {
            LogBackdrop(url: nil)
        }
        .navigationTitle("List progress")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .environment(\.colorScheme, .dark)
        .toolbar(.hidden, for: .tabBar)
        .navigationDestination(item: $route) { route in
            StatsRouteDestination(route: route, services: services)
        }
    }
}
