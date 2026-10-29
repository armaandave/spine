import Foundation
import XCTest
@testable import Spine

/// Decoding the v2 stats payload (`docs/stats-contract.md`), its legacy fallback, and the year pills.
@MainActor
final class StatsPresentationTests: XCTestCase {
    // MARK: - Every block

    func testDecodesEveryV2Block() throws {
        let page = try decode(Self.fullPageJSON)

        XCTAssertFalse(page.isLegacy)
        XCTAssertEqual(page.schemaVersion, 2)
        XCTAssertEqual(page.range.startDate, "2025-01-01")
        XCTAssertEqual(page.range.timezone, "America/New_York")
        XCTAssertFalse(page.range.isAllTime)
        XCTAssertNil(page.mediaType)
        XCTAssertEqual(page.audience, .owner)
        XCTAssertEqual(page.years, StatsYears(first: 2014, current: 2026))
        XCTAssertEqual(page.mediaTypes.map(\.mediaType), ["movie", "tv"])
        XCTAssertEqual(page.mediaTypes.first?.titles, 120)
        XCTAssertEqual(page.mediaTypes.first?.logs, 151)

        let header = try XCTUnwrap(page.header)
        XCTAssertEqual([header.titles, header.logs, header.activeDays, header.longestDailyStreak], [212, 268, 141, 9])
        XCTAssertEqual(header.details.map(\.key), ["current_daily_streak", "rewatches", "days_with_two_plus_titles"])
        XCTAssertEqual(header.details.first?.unit, "day")
        XCTAssertEqual(header.details.first?.label, "Current daily streak")
        XCTAssertNil(header.details[1].unit)
        XCTAssertTrue(header.details[2].approximate)

        let time = try XCTUnwrap(page.time)
        XCTAssertEqual(time.rows.map(\.mediaType), ["movie", "tv", "book"])
        XCTAssertEqual(time.rows[2].unit, "pages")
        XCTAssertEqual(time.rows[1].known, 1100)
        XCTAssertTrue(time.rows[1].approximate)
        XCTAssertEqual(time.hoursTotal?.minutes, 38640)
        XCTAssertEqual(time.hoursTotal?.leavesOut, ["manga", "book", "comic"])
        XCTAssertEqual(time.hoursTotal?.mediaTypes, ["movie", "tv", "anime", "game", "music"])

        let top = try XCTUnwrap(page.top)
        XCTAssertEqual(top.source, .list)
        XCTAssertTrue(top.isFromList)
        XCTAssertEqual(top.tag, "top2025")
        XCTAssertNil(top.tagHint)
        XCTAssertFalse(top.hintPrivate)
        XCTAssertEqual(top.list, StatsTopList(id: 81, name: "My 2025", total: 24))
        XCTAssertEqual(top.items.map(\.rank), [1, 2])
        XCTAssertEqual(top.items[0].stars?.value, 5)
        XCTAssertNil(top.items[1].stars)

        let highest = try XCTUnwrap(page.highestRated)
        XCTAssertEqual(highest.total, 140)
        XCTAssertEqual(highest.items[0].day, "2025-03-02")
        XCTAssertNil(highest.items[1].day)
        XCTAssertEqual(highest.items[1].media.displayTitle, "Severance S2", "Seasons read \"Show S2\" (TV-004)")
        XCTAssertEqual(highest.items[1].stars?.value, 4.5)

        let activity = try XCTUnwrap(page.activity)
        XCTAssertEqual(activity.months.map(\.month), ["2025-01", "2025-02"])
        XCTAssertEqual(activity.months[0].byType, ["movie": 9, "tv": 5])
        XCTAssertEqual(activity.months[1].byType, [:])
        XCTAssertEqual(activity.weekdays, [31, 18, 22, 19, 25, 40, 44])
        XCTAssertEqual(activity.weekStartsOn, "sunday")
        XCTAssertEqual(activity.busiestDay, StatsBusiestDay(date: "2025-08-02", count: 6))
        XCTAssertEqual(activity.busiestMonth, StatsBusiestMonth(month: "2025-08", count: 31))

        let ratings = try XCTUnwrap(page.ratings)
        XCTAssertEqual(ratings.averageStars?.value, 3.65)
        XCTAssertEqual(ratings.averageStars?.text, "3.65")
        XCTAssertEqual(ratings.rated, 140)
        XCTAssertEqual(ratings.distribution.map(\.stars.text), ["0.5", "3.5", "5.0"])
        XCTAssertEqual(ratings.distribution[1].ratingMin.text, "6.5")
        XCTAssertEqual(ratings.distribution[1].ratingMax.text, "7.4")
        XCTAssertEqual(ratings.distribution[2].ratingMax.text, "10.0", "Bounds go back to the diary exactly as sent")
        XCTAssertEqual(ratings.distribution[1].byType, ["movie": 30, "tv": 7])

        let versus = try XCTUnwrap(page.ratedVsEveryone)
        XCTAssertEqual(versus.sources.map(\.label), ["Letterboxd", "IMDb"])
        XCTAssertEqual(versus.higher.first?.everyoneStars?.value, 3.1)
        XCTAssertEqual(versus.higher.first?.difference?.value, 1.4)
        XCTAssertEqual(versus.lower.first?.difference?.value, -2.2)
        XCTAssertEqual(versus.lower.first?.source, "imdb")

        let years = try XCTUnwrap(page.releaseYears)
        XCTAssertEqual(years.rows.map(\.year), [1999, 2024])
        XCTAssertEqual(years.rows[1].averageStars?.value, 3.45)
        XCTAssertEqual(years.rows[0].byType, ["movie": 3, "tv": 1])
        XCTAssertEqual(years.coverage, StatsCoverage(known: 110, total: 120))
        XCTAssertEqual(years.newVsOlder, StatsNewVsOlder(new: 12, recent: 40, older: 58))

        let genres = try XCTUnwrap(page.genres)
        XCTAssertEqual(genres.rows.first?.key, "scifi_fantasy")
        XCTAssertEqual(genres.rows.first?.id, "scifi_fantasy")
        XCTAssertNil(genres.rows.first?.values)
        XCTAssertEqual(genres.rows.first?.byType["anime"], 9)
        XCTAssertEqual(genres.total, 13)
        XCTAssertEqual(genres.music, [StatsMusicGenre(name: "shoegaze", values: ["shoegaze", "Shoegaze"], titles: 7)])

        let languages = try XCTUnwrap(page.languages)
        XCTAssertEqual(languages.rows.map(\.name), ["English", "Japanese"])
        XCTAssertEqual(languages.mediaTypes, ["movie", "tv"])
        XCTAssertEqual(languages.total, 14)

        XCTAssertEqual(page.listRings.map(\.id), [5, 12])
        XCTAssertEqual(page.listRings[0].fraction, 0.31, accuracy: 0.0001)
        XCTAssertNil(page.listRings[1].mediaType)

        XCTAssertNil(page.collections, "Null on Global (STAT-302)")

        let mostLogged = try XCTUnwrap(page.mostLogged)
        XCTAssertEqual(mostLogged.label, "Most logged")
        XCTAssertEqual(mostLogged.total, 7)
        XCTAssertEqual(mostLogged.items.first?.times, 5)

        XCTAssertEqual(page.milestones.map(\.number), [1, 100])
        XCTAssertEqual(page.milestones[1].entryId, 9312)
        XCTAssertEqual(page.milestones[1].mediaType, "tv")
        XCTAssertEqual(page.milestones[0].day, "2014-03-02")

        XCTAssertFalse(page.isEmpty)
        XCTAssertEqual(page.featuredMedia?.title, "Top One", "The backdrop follows Top's #1")
    }

    func testDecodesATypeTabWithCollectionsAndTheTopFallback() throws {
        let page = try decode("""
        {
          "schema_version": 2,
          "range": {"start_date": null, "end_date": null, "timezone": "UTC", "is_all_time": true},
          "media_type": "manga",
          "audience": "follower",
          "years": {"first": null, "current": 2026},
          "media_types": [],
          "header": {"titles": 0, "logs": 0, "active_days": 0, "longest_daily_streak": 0, "details": []},
          "time": {"rows": [], "hours_total": null},
          "top": {"source": "highest_rated", "tag": "topalltime", "tag_hint": null, "hint_private": false, "list": null,
                  "items": [{"rank": 1, "media": \(Self.media(id: 9, title: "Berserk", type: "manga")), "stars": "5.0"}]},
          "highest_rated": {"total": 1, "items": []},
          "genres": {"rows": [{"key": null, "name": "Sci-Fi", "values": ["Sci-Fi", "Sci-fi"], "titles": 22, "rated": 25,
                               "average_stars": "3.9", "by_type": {"manga": 22}}],
                     "total": 19, "coverage": {"known": 60, "total": 64}, "music": []},
          "languages": null,
          "collections": {"complete_count": 1, "almost_count": 0,
                          "complete": [{"id": "119", "source": "mal", "media_type": "manga", "name": "Saga", "item_count": 2,
                                        "poster_urls": [], "completion": {"completed_count": 2, "total_count": 2}}],
                          "almost": []},
          "list_rings": [],
          "milestones": []
        }
        """)

        XCTAssertEqual(page.mediaType, "manga")
        XCTAssertEqual(page.audience, .follower)
        XCTAssertNil(page.years?.first)
        XCTAssertTrue(page.header?.isEmpty == true)
        XCTAssertTrue(page.time?.isEmpty == true)
        XCTAssertNil(page.time?.hoursTotal)
        XCTAssertEqual(page.top?.source, .highestRated)
        XCTAssertFalse(page.top?.isFromList == true, "The fallback: Highest rated stays hidden")
        XCTAssertEqual(page.genres?.rows.first?.values, ["Sci-Fi", "Sci-fi"])
        XCTAssertEqual(page.genres?.rows.first?.id, "Sci-Fi")
        XCTAssertNil(page.languages)
        XCTAssertEqual(page.collections?.completeCount, 1)
        XCTAssertEqual(page.collections?.complete.first?.ref, SeriesRef(source: "mal", id: "119", mediaType: "manga"))
        XCTAssertNil(page.activity, "Missing keys are missing sections")
        XCTAssertNil(page.mostLogged)
    }

    func testDecodesTheRoundTwoFields() throws {
        let page = try decode("""
        {
          "schema_version": 2,
          "range": {"start_date": "2025-01-01", "end_date": "2025-12-31", "timezone": "UTC", "is_all_time": false},
          "media_type": "movie",
          "audience": "owner",
          "header": {"titles": 40, "logs": 52, "active_days": 31, "longest_daily_streak": 4,
                     "longest_daily_streak_range": {"start": "2025-03-01", "end": "2025-03-04"},
                     "details": [
                       {"key": "longest_weekly_streak", "label": "Longest weekly streak", "value": 6, "unit": "week", "approximate": false,
                        "drill": {"watched_from": "2025-02-03", "watched_to": "2025-03-16"}},
                       {"key": "rewatches", "label": "Rewatches", "value": 3, "unit": null, "approximate": false, "drill": {"repeat": "true"}},
                       {"key": "days_with_two_plus_titles", "label": "Days with 2+ titles", "value": 5, "unit": null, "approximate": false,
                        "drill": {"multi_title_days": "true"}},
                       {"key": "reviews", "label": "Reviews", "value": 2, "unit": null, "approximate": false, "drill": {"has_review": true}},
                       {"key": "episodes", "label": "Episodes", "value": 2, "unit": null, "approximate": false, "drill": null}
                     ]},
          "list_rings": [{"id": 5, "name": "Top 500", "media_type": "movie", "kind": "community", "seen": 46, "total": 98, "seen_in_range": 12}],
          "collections": {"complete_count": 4, "almost_count": 2, "complete": [], "almost": [],
                          "in_range": {"completed_count": 1,
                                       "completed": [{"id": "10", "source": "tmdb", "media_type": "movie", "name": "Dune", "item_count": 3,
                                                      "poster_urls": [], "completion": {"completed_count": 3, "total_count": 3}, "seen_in_range": 1}],
                                       "progressed_count": 0, "progressed": []}},
          "milestones": []
        }
        """)

        let header = try XCTUnwrap(page.header)
        XCTAssertEqual(header.longestDailyStreakRange, StatsDayRange(start: "2025-03-01", end: "2025-03-04"))
        XCTAssertEqual(
            header.details.first?.drill,
            ["watched_from": "2025-02-03", "watched_to": "2025-03-16"],
            "Drill keys are diary params, not converted to camel case"
        )
        XCTAssertEqual(header.details[1].drill, ["repeat": "true"])
        XCTAssertEqual(header.details[2].drill, ["multi_title_days": "true"])
        XCTAssertNil(header.details[3].drill, "A drill that isn't all strings is no drill")
        XCTAssertNil(header.details[4].drill)

        XCTAssertEqual(page.listRings.first?.seenInRange, 12)
        let inRange = try XCTUnwrap(page.collections?.inRange)
        XCTAssertEqual(inRange.completedCount, 1)
        XCTAssertEqual(inRange.completed.first?.seenInRange, 1)
        XCTAssertTrue(inRange.progressed.isEmpty)
    }

    func testRoundTwoFieldsAreOptional() throws {
        let page = try decode("""
        {
          "schema_version": 2,
          "media_type": "movie",
          "header": {"titles": 1, "logs": 1, "active_days": 1, "longest_daily_streak": 1,
                     "details": [{"key": "likes", "label": "Likes", "value": 1, "unit": null, "approximate": false}]},
          "list_rings": [{"id": 5, "name": "Top 500", "media_type": "movie", "kind": "community", "seen": 4, "total": 500}],
          "collections": {"complete_count": 1, "almost_count": 0,
                          "complete": [{"id": "10", "source": "tmdb", "media_type": "movie", "name": "Dune", "item_count": 2,
                                        "poster_urls": [], "completion": {"completed_count": 2, "total_count": 2}}],
                          "almost": []},
          "milestones": []
        }
        """)

        XCTAssertNil(page.header?.longestDailyStreakRange)
        XCTAssertNil(page.header?.details.first?.drill)
        XCTAssertNil(page.listRings.first?.seenInRange)
        XCTAssertNil(page.collections?.inRange)
        XCTAssertNil(page.collections?.complete.first?.seenInRange)
        XCTAssertEqual(page.collections.map(StatsCollectionsData.groups)?.map(\.group), [.complete], "The lifetime groups still show")
    }

    func testDrillKeysTurnBackIntoSnakeCase() {
        XCTAssertEqual(StatsHeaderDetail.snakeCase("watchedFrom"), "watched_from")
        XCTAssertEqual(StatsHeaderDetail.snakeCase("multiTitleDays"), "multi_title_days")
        XCTAssertEqual(StatsHeaderDetail.snakeCase("repeat"), "repeat")
        XCTAssertEqual(StatsHeaderDetail.snakeCase("has_review"), "has_review")
    }

    // MARK: - Partly broken payloads

    func testOneBrokenBlockHidesOnlyThatSection() throws {
        let page = try decode("""
        {
          "schema_version": 2,
          "media_type": null,
          "audience": "someone-new",
          "years": "2014",
          "media_types": [{"media_type": "movie", "titles": 1, "logs": 1}, {"titles": 3}],
          "header": {"titles": 3, "logs": 4, "active_days": 2, "longest_daily_streak": 1,
                     "details": [{"key": "reviews", "label": "Reviews", "value": 2, "unit": null, "approximate": false},
                                 {"label": "No key", "value": 1}]},
          "time": "oops",
          "top": {"source": "list", "tag": "top2025", "tag_hint": null, "hint_private": false, "list": {"id": 4, "name": "Mine", "total": 3},
                  "items": [{"rank": 1, "stars": "5.0"}, {"rank": 2, "media": \(Self.media(id: 2, title: "Second")), "stars": "four"}]},
          "ratings": {"average_stars": "3.0", "rated": 2,
                      "distribution": [{"stars": "3.0", "count": 2, "rating_min": "5.5", "rating_max": "6.4", "by_type": {"movie": 2}},
                                       {"stars": 5, "count": "lots"},
                                       {"stars": "bad", "count": 1, "rating_min": "9.5", "rating_max": "10.0"}]},
          "activity": [1, 2, 3],
          "release_years": {"rows": [{"year": 2001, "titles": 1}, {"titles": 1}], "coverage": null, "new_vs_older": null},
          "list_rings": {"not": "a list"},
          "collections": null,
          "milestones": [{"number": 1, "day": "2020-01-01", "entry_id": 3, "media_type": "movie",
                          "media": \(Self.media(id: 1, title: "First"))}, {"number": 100}]
        }
        """)

        XCTAssertNil(page.audience, "An unknown audience isn't guessed")
        XCTAssertNil(page.years)
        XCTAssertEqual(page.mediaTypes.map(\.mediaType), ["movie"])
        XCTAssertEqual(page.header?.titles, 3)
        XCTAssertEqual(page.header?.details.map(\.key), ["reviews"])
        XCTAssertNil(page.time)
        XCTAssertEqual(page.top?.items.map(\.rank), [2], "An item with no media is dropped")
        XCTAssertNil(page.top?.items.first?.stars, "Unreadable stars are no stars")
        XCTAssertEqual(page.ratings?.rated, 2)
        XCTAssertEqual(page.ratings?.distribution.map(\.stars.text), ["3.0"])
        XCTAssertNil(page.activity)
        XCTAssertEqual(page.releaseYears?.rows.map(\.year), [2001])
        XCTAssertEqual(page.releaseYears?.rows.first?.byType, [:])
        XCTAssertTrue(page.listRings.isEmpty)
        XCTAssertNil(page.collections)
        XCTAssertEqual(page.milestones.map(\.number), [1])
        XCTAssertFalse(page.isLegacy)
    }

    func testDecimalStringsReadAsNumbers() throws {
        XCTAssertEqual(StatsDecimal("4.0")?.value, 4)
        XCTAssertEqual(StatsDecimal("3.65")?.value, 3.65)
        XCTAssertEqual(StatsDecimal("-1.1")?.value, -1.1)
        XCTAssertEqual(StatsDecimal(" 2.5 ")?.text, "2.5")
        XCTAssertNil(StatsDecimal("four"))
        XCTAssertNil(StatsDecimal(""))
        XCTAssertNil(StatsDecimal("nan"))
        XCTAssertEqual(StatsDecimal.value("4.5"), 4.5)
        XCTAssertNil(StatsDecimal.value(nil))
        XCTAssertEqual(StatsDecimal(4).text, "4.0")
        XCTAssertEqual(StatsDecimal(3.65).text, "3.65")
        XCTAssertEqual(StatsDecimal(3.5).text, "3.5")

        // A bare number still decodes, in case a server sends one.
        let decoded = try JSONDecoder.api.decode([StatsDecimal].self, from: Data(#"["3.5", 4.5]"#.utf8))
        XCTAssertEqual(decoded.map(\.value), [3.5, 4.5])
        XCTAssertEqual(decoded.map(\.text), ["3.5", "4.5"])
    }

    // MARK: - Null versus empty (STAT-302)

    func testEmptySectionsFollowTheContract() {
        XCTAssertTrue(StatsHeader(titles: 0, logs: 0, activeDays: 4, longestDailyStreak: 2, details: []).isEmpty)
        XCTAssertFalse(StatsHeader(titles: 0, logs: 1, activeDays: 0, longestDailyStreak: 0, details: []).isEmpty)
        XCTAssertTrue(StatsRatings(averageStars: nil, rated: 0, distribution: []).isEmpty)
        XCTAssertTrue(StatsEventActivity(months: [], weekdays: [], weekStartsOn: "monday", busiestDay: nil, busiestMonth: nil).isEmpty)
        XCTAssertTrue(StatsGenres(rows: [], total: 0, coverage: nil, music: []).isEmpty)
        XCTAssertFalse(StatsGenres(rows: [], total: 0, coverage: nil, music: [StatsMusicGenre(name: "jazz", titles: 1)]).isEmpty)
        XCTAssertTrue(StatsCollections(completeCount: 0, almostCount: 0, complete: [], almost: []).isEmpty)
        XCTAssertTrue(StatsRatedVsEveryone(sources: [], higher: [], lower: []).isEmpty)

        XCTAssertTrue(StatsPage().isEmpty)
        XCTAssertTrue(StatsPage(
            header: StatsHeader(titles: 0, logs: 0, activeDays: 0, longestDailyStreak: 0, details: []),
            time: StatsTime(rows: [], hoursTotal: nil),
            listRings: []
        ).isEmpty)
        XCTAssertFalse(StatsPage(listRings: [StatsListRing(id: 1, name: "Top 500", mediaType: "movie", kind: "community", seen: 2, total: 500)]).isEmpty)
    }

    func testRingFractionIsClamped() {
        XCTAssertEqual(StatsListRing(id: 1, name: "A", mediaType: nil, kind: nil, seen: 3, total: 0).fraction, 0)
        XCTAssertEqual(StatsListRing(id: 1, name: "A", mediaType: nil, kind: nil, seen: 9, total: 4).fraction, 1)
    }

    // MARK: - Legacy fallback (STAT-902)

    func testLegacyPayloadFillsHeaderActivityAndGenres() throws {
        let page = try decode(Self.legacyJSON(schemaVersion: "\"schema_version\": 1,"))

        XCTAssertTrue(page.isLegacy)
        XCTAssertEqual(page.schemaVersion, 1)
        XCTAssertTrue(page.mediaTypes.isEmpty, "The legacy media_types has another shape")
        XCTAssertNil(page.years)
        XCTAssertNil(page.audience)
        XCTAssertNil(page.ratings, "Ratings are hidden on the legacy payload")
        XCTAssertNil(page.top)
        XCTAssertNil(page.highestRated)
        XCTAssertNil(page.mostLogged)

        let header = try XCTUnwrap(page.header)
        XCTAssertEqual([header.titles, header.logs, header.activeDays, header.longestDailyStreak], [12, 15, 9, 4])
        XCTAssertEqual(header.details.map(\.key), ["current_daily_streak", "rewatches", "reviews", "likes"])
        XCTAssertEqual(header.details.map(\.value), [2, 3, 5, 6])
        XCTAssertEqual(header.details[1].label, "Repeats")

        let activity = try XCTUnwrap(page.activity)
        XCTAssertEqual(activity.months.map(\.count), [4, 7])
        XCTAssertEqual(activity.weekStartsOn, "monday")
        // 2025-01-06 is a Monday, 2025-01-12 a Sunday.
        XCTAssertEqual(activity.weekdays, [2, 0, 0, 0, 0, 0, 3])
        XCTAssertEqual(activity.busiestDay, StatsBusiestDay(date: "2025-01-12", count: 3))
        XCTAssertEqual(activity.busiestMonth, StatsBusiestMonth(month: "2025-02", count: 7))

        let genres = try XCTUnwrap(page.genres)
        XCTAssertEqual(genres.rows.map(\.name), ["Drama", "Comedy"])
        XCTAssertEqual(genres.rows.first?.titles, 8)
        XCTAssertEqual(genres.rows.first?.values, ["Drama"])
        XCTAssertNil(genres.rows.first?.averageStars)
        XCTAssertEqual(genres.coverage, StatsCoverage(known: 10, total: 12))
        XCTAssertFalse(page.isEmpty)
    }

    func testMissingSchemaVersionIsLegacyAndATabUsesItsTypeEntry() throws {
        let page = try decode(Self.legacyJSON(schemaVersion: "")).scoped(to: "movie")

        XCTAssertTrue(page.isLegacy)
        XCTAssertEqual(page.mediaType, "movie")
        XCTAssertEqual(page.header?.titles, 7)
        XCTAssertEqual(page.header?.logs, 9)
        XCTAssertEqual(page.header?.details.first?.label, "Rewatches")
        XCTAssertEqual(page.header?.details.first?.value, 1)
        XCTAssertNil(page.activity)
        XCTAssertEqual(page.genres?.rows.map(\.name), ["Thriller"])
        XCTAssertEqual(page.genres?.rows.first?.byType, ["movie": 4])
        XCTAssertNil(page.ratings)

        let missingType = try decode(Self.legacyJSON(schemaVersion: "")).scoped(to: "game")
        XCTAssertTrue(missingType.header?.isEmpty == true)
        XCTAssertNil(missingType.genres)
    }

    func testScopingAV2PageChangesNothing() throws {
        let page = try decode(Self.fullPageJSON)
        XCTAssertEqual(page.scoped(to: "movie"), page)
    }

    func testRepeatsLabelsPerTab() {
        XCTAssertEqual(StatsPage.repeatsLabel(for: nil), "Repeats")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "movie"), "Rewatches")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "tv"), "Rewatches")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "anime"), "Rewatches")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "book"), "Rereads")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "manga"), "Rereads")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "comic"), "Rereads")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "game"), "Replays")
        XCTAssertEqual(StatsPage.repeatsLabel(for: "music"), "Relistens")
    }

    // MARK: - Year pills

    func testPillsRunFromTheFirstYearToThisYearNewestFirst() {
        let now = Self.date(year: 2026, month: 3, day: 1)

        XCTAssertEqual(
            StatsPeriod.pillYears(StatsYears(first: 2021, current: 2026), from: now, calendar: Self.utc),
            [.year(2026), .year(2025), .year(2024), .year(2023), .year(2022), .year(2021)]
        )
        XCTAssertEqual(StatsPeriod.pillYears(StatsYears(first: 2014, current: 2026), from: now, calendar: Self.utc).count, 13)
    }

    func testPillsReachThisYearEvenWhenTheCachedBlockIsOld() {
        let now = Self.date(year: 2027, month: 1, day: 2)

        XCTAssertEqual(
            StatsPeriod.pillYears(StatsYears(first: 2025, current: 2026), from: now, calendar: Self.utc),
            [.year(2027), .year(2026), .year(2025)]
        )
    }

    func testPillsWithNothingLoggedShowThisYearOnly() {
        let now = Self.date(year: 2026, month: 3, day: 1)

        XCTAssertEqual(StatsPeriod.pillYears(StatsYears(first: nil, current: 2026), from: now, calendar: Self.utc), [.year(2026)])
        XCTAssertEqual(
            StatsPeriod.pillYears(StatsYears(first: 2030, current: 2026), from: now, calendar: Self.utc),
            [.year(2026)],
            "A first year after this year can't make an empty range"
        )
        XCTAssertEqual(
            StatsPeriod.pillYears(StatsYears(first: 1, current: 2026), from: now, calendar: Self.utc).count,
            151,
            "A broken first year is capped"
        )
    }

    func testPillsWithoutAnyYearsUseTheRecentYears() {
        let now = Self.date(year: 2026, month: 3, day: 1)

        XCTAssertEqual(
            StatsPeriod.pillYears(nil, from: now, calendar: Self.utc),
            StatsPeriod.recentYears(from: now, calendar: Self.utc)
        )
        XCTAssertEqual(StatsPeriod.pillYears(nil, from: now, calendar: Self.utc).first, .year(2026))
    }

    func testYearsBlockRoundTripsForTheCache() throws {
        let years = StatsYears(first: 2014, current: 2026)
        let data = try JSONEncoder().encode(years)
        XCTAssertEqual(try JSONDecoder().decode(StatsYears.self, from: data), years)

        let defaults = try XCTUnwrap(UserDefaults(suiteName: "StatsPresentationTests.\(UUID().uuidString)"))
        let cache = StatsPickerCache(defaults: defaults)
        cache.setYears(years, for: "mika")
        cache.setMediaTypes(["movie"], for: nil)
        XCTAssertEqual(cache.years(for: "mika"), years)
        XCTAssertNil(cache.years(for: nil), "Your own stats and someone's never share a cache entry")
        XCTAssertEqual(cache.mediaTypes(for: nil), ["movie"])
        XCTAssertNil(cache.mediaTypes(for: "mika"))
    }

    // MARK: - Helpers

    private func decode(_ json: String) throws -> StatsPage {
        try JSONDecoder.api.decode(StatsPage.self, from: Data(json.utf8))
    }

    private static var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar
    }

    private static func date(year: Int, month: Int, day: Int) -> Date {
        utc.date(from: DateComponents(year: year, month: month, day: day))!
    }

    private static func media(id: Int, title: String, type: String = "movie", season: Int? = nil) -> String {
        """
        {"ref": {"item_id": \(id), "source": "tmdb", "media_type": "\(type)", "media_id": "\(id)", \
        "season_number": \(season.map(String.init) ?? "null"), "episode_number": null}, \
        "title": "\(title)", "poster_url": "https://example.com/\(id).jpg"}
        """
    }

    private static let fullPageJSON = """
    {
      "schema_version": 2,
      "range": {"start_date": "2025-01-01", "end_date": "2025-12-31", "timezone": "America/New_York", "is_all_time": false},
      "media_type": null,
      "audience": "owner",
      "years": {"first": 2014, "current": 2026},
      "media_types": [{"media_type": "movie", "titles": 120, "logs": 151}, {"media_type": "tv", "titles": 18, "logs": 40}],
      "header": {
        "titles": 212, "logs": 268, "active_days": 141, "longest_daily_streak": 9,
        "details": [
          {"key": "current_daily_streak", "label": "Current daily streak", "value": 3, "unit": "day", "approximate": false},
          {"key": "rewatches", "label": "Repeats", "value": 17, "unit": null, "approximate": false},
          {"key": "days_with_two_plus_titles", "label": "Days with 2+ titles", "value": 38, "unit": null, "approximate": true}
        ]
      },
      "time": {
        "rows": [
          {"media_type": "movie", "unit": "minutes", "value": 18240, "known": 148, "total": 151, "approximate": false},
          {"media_type": "tv", "unit": "minutes", "value": 20400, "known": 1100, "total": 1204, "approximate": true},
          {"media_type": "book", "unit": "pages", "value": 6120, "known": 18, "total": 19, "approximate": false}
        ],
        "hours_total": {"minutes": 38640, "media_types": ["movie", "tv", "anime", "game", "music"], "leaves_out": ["manga", "book", "comic"], "approximate": true}
      },
      "top": {
        "source": "list", "tag": "top2025", "tag_hint": null, "hint_private": false,
        "list": {"id": 81, "name": "My 2025", "total": 24},
        "items": [
          {"rank": 1, "media": \(media(id: 1, title: "Top One")), "stars": "5.0"},
          {"rank": 2, "media": \(media(id: 2, title: "Top Two")), "stars": null}
        ]
      },
      "highest_rated": {
        "total": 140,
        "items": [
          {"media": \(media(id: 3, title: "Dune: Part Two")), "stars": "5.0", "day": "2025-03-02"},
          {"media": \(media(id: 4, title: "Severance", type: "season", season: 2)), "stars": "4.5", "day": null}
        ]
      },
      "activity": {
        "months": [
          {"month": "2025-01", "count": 14, "by_type": {"movie": 9, "tv": 5}},
          {"month": "2025-02", "count": 0, "by_type": {}}
        ],
        "weekdays": [31, 18, 22, 19, 25, 40, 44],
        "week_starts_on": "sunday",
        "busiest_day": {"date": "2025-08-02", "count": 6},
        "busiest_month": {"month": "2025-08", "count": 31}
      },
      "ratings": {
        "average_stars": "3.65", "rated": 140,
        "distribution": [
          {"stars": "0.5", "count": 0, "rating_min": "0.0", "rating_max": "1.4", "by_type": {}},
          {"stars": "3.5", "count": 37, "rating_min": "6.5", "rating_max": "7.4", "by_type": {"movie": 30, "tv": 7}},
          {"stars": "5.0", "count": 12, "rating_min": "9.5", "rating_max": "10.0", "by_type": {"movie": 12}}
        ]
      },
      "rated_vs_everyone": {
        "sources": [{"media_type": "movie", "source": "letterboxd", "label": "Letterboxd"}, {"media_type": "tv", "source": "imdb", "label": "IMDb"}],
        "higher": [{"media": \(media(id: 5, title: "Higher")), "stars": "4.5", "everyone_stars": "3.1", "difference": "1.4", "source": "letterboxd"}],
        "lower": [{"media": \(media(id: 6, title: "Lower", type: "tv")), "stars": "2.0", "everyone_stars": "4.2", "difference": "-2.2", "source": "imdb"}]
      },
      "release_years": {
        "rows": [
          {"year": 1999, "titles": 4, "logs": 5, "rated": 3, "average_stars": "4.0", "by_type": {"movie": 3, "tv": 1}},
          {"year": 2024, "titles": 31, "logs": 33, "rated": 30, "average_stars": "3.45", "by_type": {"movie": 25, "anime": 6}}
        ],
        "coverage": {"known": 110, "total": 120},
        "new_vs_older": {"new": 12, "recent": 40, "older": 58}
      },
      "genres": {
        "rows": [{"key": "scifi_fantasy", "name": "Sci-Fi & Fantasy", "values": null, "titles": 41, "rated": 33, "average_stars": "3.8",
                  "by_type": {"movie": 20, "tv": 6, "anime": 9, "game": 6}}],
        "total": 13,
        "coverage": {"known": 380, "total": 412},
        "music": [{"name": "shoegaze", "values": ["shoegaze", "Shoegaze"], "titles": 7}]
      },
      "languages": {
        "rows": [
          {"name": "English", "titles": 140, "rated": 120, "average_stars": "3.6", "by_type": {"movie": 120, "tv": 20}},
          {"name": "Japanese", "titles": 9, "rated": 8, "average_stars": "4.1", "by_type": {"movie": 6, "tv": 3}}
        ],
        "total": 14,
        "coverage": {"known": 150, "total": 158},
        "media_types": ["movie", "tv"]
      },
      "list_rings": [
        {"id": 5, "name": "Letterboxd's Top 500 Films", "media_type": "movie", "kind": "community", "seen": 155, "total": 500},
        {"id": 12, "name": "Mixed Canon", "media_type": null, "kind": "canon", "seen": 9, "total": 57}
      ],
      "collections": null,
      "most_logged": {"label": "Most logged", "total": 7, "items": [{"media": \(media(id: 7, title: "Again")), "times": 5}]},
      "milestones": [
        {"number": 1, "day": "2014-03-02", "entry_id": 11, "media_type": "movie", "media": \(media(id: 8, title: "First"))},
        {"number": 100, "day": "2024-03-12", "entry_id": 9312, "media_type": "tv", "media": \(media(id: 9, title: "Show", type: "tv"))}
      ]
    }
    """

    private static func legacyJSON(schemaVersion: String) -> String {
        """
        {
          \(schemaVersion)
          "range": {"start_date": null, "end_date": null, "timezone": "UTC", "is_all_time": true},
          "overview": {"tracked_count": 40, "completed_count": 30, "diary_entry_count": 15, "unique_logged_count": 12,
                       "review_count": 5, "repeat_count": 3, "rated_count": 10, "average_rating": "3.5", "liked_count": 6,
                       "active_days": 9, "current_streak_days": 2, "longest_streak_days": 4},
          "media_types": [{"media_type": "movie", "diary_entry_count": 9, "unique_logged_count": 7, "review_count": 2,
                           "repeat_count": 1, "liked_count": 3, "top_genres": [{"name": "Thriller", "count": 4}],
                           "metadata_coverage": {"total_items": 7, "genre_items": 6}}],
          "activity": {
            "days": [{"date": "2025-01-06", "count": 2}, {"date": "2025-01-12", "count": 3}],
            "months": [{"month": "2025-01", "count": 4}, {"month": "2025-02", "count": 7}],
            "active_days": 9, "current_streak_days": 2, "longest_streak_days": 4
          },
          "rating_distribution": [{"rating": "4.0", "count": 3}],
          "top_genres": [{"name": "Drama", "count": 8}, {"name": "Comedy", "count": 3}],
          "metadata_coverage": {"total_items": 12, "release_year_items": 11, "genre_items": 10, "language_items": 9}
        }
        """
    }
}
