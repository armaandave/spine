import Foundation

enum CustomListType: String, Codable, Hashable {
    case media
    case people
}

struct PersonListEntry: Codable, Identifiable, Hashable {
    let entryId: Int
    let personId: String
    let source: String
    let name: String
    let profileUrl: String?
    let knownForDepartment: String?
    let position: Int?
    let dateAdded: String
    /// Featured lists only: the entry's note ("Inducted 2019").
    let note: String?

    var id: Int {
        entryId
    }

    var ref: PersonRef {
        PersonRef(source: source, id: personId)
    }

    private enum CodingKeys: String, CodingKey {
        case entryId
        case personId = "id"
        case source
        case name
        case profileUrl
        case knownForDepartment
        case position
        case dateAdded
        case note
    }

    init(
        entryId: Int,
        personId: String,
        source: String,
        name: String,
        profileUrl: String?,
        knownForDepartment: String?,
        position: Int?,
        dateAdded: String,
        note: String? = nil
    ) {
        self.entryId = entryId
        self.personId = personId
        self.source = source
        self.name = name
        self.profileUrl = profileUrl
        self.knownForDepartment = knownForDepartment
        self.position = position
        self.dateAdded = dateAdded
        self.note = note
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        entryId = try container.decode(Int.self, forKey: .entryId)
        personId = try container.decode(String.self, forKey: .personId)
        source = try container.decode(String.self, forKey: .source)
        name = try container.decode(String.self, forKey: .name)
        profileUrl = try container.decodeIfPresent(String.self, forKey: .profileUrl)
        knownForDepartment = try container.decodeIfPresent(String.self, forKey: .knownForDepartment)
        position = try container.decodeIfPresent(Int.self, forKey: .position)
        dateAdded = try container.decode(String.self, forKey: .dateAdded)
        note = (try? container.decodeIfPresent(String.self, forKey: .note)) ?? nil
    }
}

struct CustomListSummary: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let tags: [String]
    let visibility: String
    let isRanked: Bool
    let listType: CustomListType
    let hasItem: Bool?
    let hasPerson: Bool?
    let personEntryId: Int?
    let owner: UserSummary
    let imageUrl: String?
    let previewItems: [MediaSummary]?
    let previewPeople: [PersonListEntry]
    let itemsCount: Int
    let peopleCount: Int
    let entriesCount: Int
    let updatedAt: String?
    let likeCount: Int

    init(
        id: Int,
        name: String,
        slug: String,
        description: String,
        tags: [String] = [],
        visibility: String,
        isRanked: Bool = false,
        listType: CustomListType = .media,
        hasItem: Bool? = nil,
        hasPerson: Bool? = nil,
        personEntryId: Int? = nil,
        owner: UserSummary,
        imageUrl: String? = nil,
        previewItems: [MediaSummary]? = nil,
        previewPeople: [PersonListEntry] = [],
        itemsCount: Int,
        peopleCount: Int = 0,
        entriesCount: Int? = nil,
        updatedAt: String? = nil,
        likeCount: Int
    ) {
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.tags = tags
        self.visibility = visibility
        self.isRanked = isRanked
        self.listType = listType
        self.hasItem = hasItem
        self.hasPerson = hasPerson
        self.personEntryId = personEntryId
        self.owner = owner
        self.imageUrl = imageUrl
        self.previewItems = previewItems
        self.previewPeople = previewPeople
        self.itemsCount = itemsCount
        self.peopleCount = peopleCount
        self.entriesCount = entriesCount ?? (listType == .people ? peopleCount : itemsCount)
        self.updatedAt = updatedAt
        self.likeCount = likeCount
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case slug
        case description
        case tags
        case visibility
        case isRanked
        case listType
        case hasItem
        case hasPerson
        case personEntryId
        case owner
        case imageUrl
        case previewItems
        case previewPeople
        case itemsCount
        case peopleCount
        case entriesCount
        case updatedAt
        case likeCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        slug = try container.decode(String.self, forKey: .slug)
        description = try container.decode(String.self, forKey: .description)
        tags = try container.decodeIfPresent([String].self, forKey: .tags) ?? []
        visibility = try container.decode(String.self, forKey: .visibility)
        isRanked = try container.decodeIfPresent(Bool.self, forKey: .isRanked) ?? false
        listType = try container.decodeIfPresent(CustomListType.self, forKey: .listType) ?? .media
        hasItem = try container.decodeIfPresent(Bool.self, forKey: .hasItem)
        hasPerson = try container.decodeIfPresent(Bool.self, forKey: .hasPerson)
        personEntryId = try container.decodeIfPresent(Int.self, forKey: .personEntryId)
        owner = try container.decode(UserSummary.self, forKey: .owner)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        previewItems = try container.decodeIfPresent([MediaSummary].self, forKey: .previewItems)
        previewPeople = try container.decodeIfPresent([PersonListEntry].self, forKey: .previewPeople) ?? []
        itemsCount = try container.decode(Int.self, forKey: .itemsCount)
        peopleCount = try container.decodeIfPresent(Int.self, forKey: .peopleCount) ?? 0
        entriesCount = try container.decodeIfPresent(Int.self, forKey: .entriesCount)
            ?? itemsCount
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        likeCount = try container.decode(Int.self, forKey: .likeCount)
    }
}

struct CustomListDetail: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
    let slug: String
    let description: String
    let tags: [String]
    let visibility: String
    let isRanked: Bool
    let listType: CustomListType
    let owner: UserSummary
    let imageUrl: String?
    let itemsCount: Int
    let peopleCount: Int
    let entriesCount: Int
    let updatedAt: String?
    let likeCount: Int
    let items: [MediaSummary]
    let people: [PersonListEntry]
    let completion: CompletionProgress?
    /// Users the owner lets edit the list.
    let collaborators: [UserSummary]
    /// Featured metadata, sent for public featured lists: the card fields without previews.
    let featured: FeaturedListCard?

    init(
        id: Int,
        name: String,
        slug: String,
        description: String,
        tags: [String] = [],
        visibility: String,
        isRanked: Bool = false,
        listType: CustomListType = .media,
        owner: UserSummary,
        imageUrl: String? = nil,
        itemsCount: Int,
        peopleCount: Int = 0,
        entriesCount: Int? = nil,
        updatedAt: String? = nil,
        likeCount: Int,
        items: [MediaSummary],
        people: [PersonListEntry] = [],
        completion: CompletionProgress? = nil,
        collaborators: [UserSummary] = [],
        featured: FeaturedListCard? = nil
    ) {
        self.id = id
        self.name = name
        self.slug = slug
        self.description = description
        self.tags = tags
        self.visibility = visibility
        self.isRanked = isRanked
        self.listType = listType
        self.owner = owner
        self.imageUrl = imageUrl
        self.itemsCount = itemsCount
        self.peopleCount = peopleCount
        self.entriesCount = entriesCount ?? (listType == .people ? peopleCount : itemsCount)
        self.updatedAt = updatedAt
        self.likeCount = likeCount
        self.items = items
        self.people = people
        self.completion = completion
        self.collaborators = collaborators
        self.featured = featured
    }

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case slug
        case description
        case tags
        case visibility
        case isRanked
        case listType
        case owner
        case imageUrl
        case itemsCount
        case peopleCount
        case entriesCount
        case updatedAt
        case likeCount
        case items
        case people
        case completion
        case collaborators
        case featured
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(Int.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        slug = try container.decode(String.self, forKey: .slug)
        description = try container.decode(String.self, forKey: .description)
        tags = try container.decodeIfPresent([String].self, forKey: .tags) ?? []
        visibility = try container.decode(String.self, forKey: .visibility)
        isRanked = try container.decodeIfPresent(Bool.self, forKey: .isRanked) ?? false
        listType = try container.decodeIfPresent(CustomListType.self, forKey: .listType) ?? .media
        owner = try container.decode(UserSummary.self, forKey: .owner)
        imageUrl = try container.decodeIfPresent(String.self, forKey: .imageUrl)
        itemsCount = try container.decode(Int.self, forKey: .itemsCount)
        peopleCount = try container.decodeIfPresent(Int.self, forKey: .peopleCount) ?? 0
        entriesCount = try container.decodeIfPresent(Int.self, forKey: .entriesCount)
            ?? itemsCount
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
        likeCount = try container.decode(Int.self, forKey: .likeCount)
        items = try container.decodeIfPresent([MediaSummary].self, forKey: .items) ?? []
        people = try container.decodeIfPresent([PersonListEntry].self, forKey: .people) ?? []
        completion = try container.decodeIfPresent(CompletionProgress.self, forKey: .completion)
        collaborators = (try? container.decodeIfPresent([UserSummary].self, forKey: .collaborators)) ?? []
        // A featured block that can't be decoded is left out rather than failing the list.
        featured = (try? container.decodeIfPresent(FeaturedListCard.self, forKey: .featured)) ?? nil
    }
}

struct CustomListWriteRequest: Encodable {
    var name: String?
    var description: String?
    var visibility: String?
    var isRanked: Bool?
    var listType: CustomListType? = nil
}

struct ListItemWriteRequest: Encodable {
    let ref: MediaRef
}

struct ListItemWriteResponse: Decodable {
    let item: MediaSummary
}

struct ListItemsReorderRequest: Encodable {
    let itemIds: [Int]
}

struct ListPersonWriteRequest: Encodable {
    let ref: PersonRef
}

struct ListPersonWriteResponse: Decodable {
    let created: Bool
    let person: PersonListEntry
}

struct ListPeopleReorderRequest: Encodable {
    let entryIds: [Int]
}
