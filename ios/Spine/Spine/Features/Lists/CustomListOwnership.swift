import Foundation

/// What the current user may do from a list's page, matching the server: the owner and collaborators can edit
/// (`CustomList.user_can_edit`), only the owner can delete (`user_can_delete`). Before the profile loads there's no
/// current user, so nobody can do either.
extension CustomListDetail {
    func canEdit(by currentUserId: Int?) -> Bool {
        guard let currentUserId else { return false }
        return owner.id == currentUserId || collaborators.contains { $0.id == currentUserId }
    }

    func canDelete(by currentUserId: Int?) -> Bool {
        guard let currentUserId else { return false }
        return owner.id == currentUserId
    }
}
