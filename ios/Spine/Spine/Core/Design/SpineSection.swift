import SwiftUI

/// A titled block: Home's sections and Search's featured shelves. The header can carry a leading glyph and a
/// trailing accessory, such as a count and "See all".
struct SpineSection<Leading: View, Trailing: View, Content: View>: View {
    private let title: String
    private let titleColor: Color
    private let headerInset: CGFloat
    private let leading: Leading
    private let trailing: Trailing
    private let content: Content

    init(
        title: String,
        titleColor: Color = .white,
        headerInset: CGFloat = 0,
        @ViewBuilder leading: () -> Leading,
        @ViewBuilder trailing: () -> Trailing,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.titleColor = titleColor
        self.headerInset = headerInset
        self.leading = leading()
        self.trailing = trailing()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                leading

                Text(title.uppercased())
                    .font(.system(size: 13, weight: .black))
                    .foregroundStyle(titleColor)
                    .tracking(0.8)
                    .accessibilityAddTraits(.isHeader)

                Spacer(minLength: 8)

                trailing
            }
            .padding(.horizontal, headerInset)

            content
        }
    }
}

extension SpineSection where Leading == EmptyView, Trailing == EmptyView {
    init(title: String, @ViewBuilder content: () -> Content) {
        self.init(title: title, leading: { EmptyView() }, trailing: { EmptyView() }, content: content)
    }
}

/// A sideways row of cards: Home's In Progress posters and Search's featured shelves. `trailing` follows the last
/// card, such as a "See all" end cap.
struct SpineRail<Items: RandomAccessCollection, Cell: View, Trailing: View>: View where Items.Element: Identifiable {
    private let items: Items
    private let spacing: CGFloat
    private let leadingInset: CGFloat
    private let trailingInset: CGFloat
    private let cell: (Items.Element) -> Cell
    private let trailing: Trailing

    init(
        items: Items,
        spacing: CGFloat = 12,
        leadingInset: CGFloat = 0,
        trailingInset: CGFloat = 14,
        @ViewBuilder cell: @escaping (Items.Element) -> Cell,
        @ViewBuilder trailing: () -> Trailing
    ) {
        self.items = items
        self.spacing = spacing
        self.leadingInset = leadingInset
        self.trailingInset = trailingInset
        self.cell = cell
        self.trailing = trailing()
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(alignment: .top, spacing: spacing) {
                ForEach(items) { item in
                    cell(item)
                }
                trailing
            }
            .padding(.leading, leadingInset)
            .padding(.trailing, trailingInset)
        }
    }
}

extension SpineRail where Trailing == EmptyView {
    init(
        items: Items,
        spacing: CGFloat = 12,
        leadingInset: CGFloat = 0,
        trailingInset: CGFloat = 14,
        @ViewBuilder cell: @escaping (Items.Element) -> Cell
    ) {
        self.init(
            items: items,
            spacing: spacing,
            leadingInset: leadingInset,
            trailingInset: trailingInset,
            cell: cell,
            trailing: { EmptyView() }
        )
    }
}
