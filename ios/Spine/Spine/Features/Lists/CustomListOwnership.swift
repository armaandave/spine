import Foundation

extension CustomListDetail {
    /// Only a list's owner can edit or delete it from the list page. Before the profile loads there's no current
    /// user, so nobody owns anything.
    func isOwned(by currentUserId: Int?) -> Bool {
        guard let currentUserId else { return false }
        return owner.id == currentUserId
    }
}
