import SwiftUI
import UIKit

/// A number field that takes focus as soon as it is in a window, so the caret blinks and the number pad rises
/// with the sheet. Focusing from SwiftUI's `.onAppear` runs before the field is in a window and loses the caret.
struct AutofocusNumberField: UIViewRepresentable {
    @Binding var text: String
    let placeholder: String
    let accessibilityLabel: String
    /// Changing this value focuses the field again.
    let focusRequest: Int

    private static let minimumWidth: CGFloat = 28
    /// Room beside the text for the caret. Before a first digit it blinks at the placeholder's leading edge, and a field
    /// exactly as wide as a placeholder like "174" clips it away.
    private static let caretRoom: CGFloat = 6

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, focusRequest: focusRequest)
    }

    func makeUIView(context: Context) -> AutofocusTextField {
        let field = AutofocusTextField()
        field.keyboardType = .numberPad
        field.textAlignment = .center
        field.tintColor = .white
        field.setContentHuggingPriority(.required, for: .horizontal)
        field.setContentCompressionResistancePriority(.required, for: .horizontal)
        field.addTarget(context.coordinator, action: #selector(Coordinator.editingChanged(_:)), for: .editingChanged)
        update(field, sizeCategory: UIContentSizeCategory(context.environment.dynamicTypeSize))
        return field
    }

    func updateUIView(_ field: AutofocusTextField, context: Context) {
        context.coordinator.text = $text
        update(field, sizeCategory: UIContentSizeCategory(context.environment.dynamicTypeSize))
        if context.coordinator.focusRequest != focusRequest {
            context.coordinator.focusRequest = focusRequest
            field.becomeFirstResponder()
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: AutofocusTextField, context: Context) -> CGSize? {
        let size = uiView.intrinsicContentSize
        return CGSize(width: max(size.width + Self.caretRoom, Self.minimumWidth), height: size.height)
    }

    private func update(_ field: AutofocusTextField, sizeCategory: UIContentSizeCategory) {
        let font = Self.font(for: sizeCategory)
        let fontChanged = field.font != font
        if fontChanged {
            field.font = font
        }
        if field.text != text {
            field.text = text
            field.invalidateIntrinsicContentSize()
        }
        field.accessibilityLabel = accessibilityLabel
        if fontChanged || field.placeholder != placeholder {
            field.attributedPlaceholder = NSAttributedString(
                string: placeholder,
                attributes: [.foregroundColor: UIColor.secondaryLabel, .font: font]
            )
            field.invalidateIntrinsicContentSize()
        }
    }

    /// Title3 semibold with monospaced digits at the page's text size, the size SwiftUI gives the unit beside it.
    /// Set from the environment on each update, so the field never scales itself a second time.
    static func font(for sizeCategory: UIContentSizeCategory) -> UIFont {
        let title3 = UIFont.preferredFont(
            forTextStyle: .title3,
            compatibleWith: UITraitCollection(preferredContentSizeCategory: sizeCategory)
        )
        return UIFont.monospacedDigitSystemFont(ofSize: title3.pointSize, weight: .semibold)
    }

    final class Coordinator: NSObject {
        var text: Binding<String>
        var focusRequest: Int

        init(text: Binding<String>, focusRequest: Int) {
            self.text = text
            self.focusRequest = focusRequest
        }

        @objc func editingChanged(_ field: UITextField) {
            text.wrappedValue = field.text ?? ""
            field.invalidateIntrinsicContentSize()
        }
    }
}

final class AutofocusTextField: UITextField {
    private var hasFocusedOnce = false

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard window != nil, !hasFocusedOnce else { return }
        hasFocusedOnce = true
        // One main-queue hop, so the window has finished attaching.
        DispatchQueue.main.async { [weak self] in
            self?.becomeFirstResponder()
        }
    }
}
