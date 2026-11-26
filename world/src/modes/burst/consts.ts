/* Burst: hardcoded demo profile + per-ray copy. Numbers computed once from public/data/world.json
   (his 990 titles): 1,088 logs, first log 2021-01-01. */
export const PROFILE = {
  handle: "@armaandave",
  name: "Armaan Dave",
  tag: "Films, albums, shows and books, logged since 2021.",
  titles: 990,
  logs: 1088,
  since: 2021,
  ring: "ARMAAN DAVE · SPINE · 990 TITLES · 1,088 LOGS · SINCE 2021 · 657 FILMS, 289 ALBUMS, 22 SHOWS, 21 BOOKS, 1 GAME · EVERY RAY IS A TASTE CLUSTER · THE POSTERS ARE MINE ·",
  byType: [
    ["Films", "657 titles · 723 logs"],
    ["Albums", "289 titles · 308 logs"],
    ["Shows", "22 titles · 36 logs"],
    ["Books", "21 titles · 21 logs"],
  ] as [string, string][],
  mostLogged: [
    ["The Boy and the Heron", "’23", "Miyazaki · 5 logs"],
    ["One Battle After Another", "’25", "P. T. Anderson · 4 logs"],
  ] as [string, string, string][],
  also: ["Game of Thrones · Succession", "Pray for Paris · Rebel"],
  centerId: 697, // The Boy and the Heron: his most-logged title
};

/** One line per taste cluster (world.clusters order). */
export const NOTES: string[] = [
  "Miyazaki, Kon and Takahata: hand-drawn worlds, quiet flights, a heron",
  "Dust planets, long takes in the Zone, worlds that run out of road",
  "Fellowships, kings and storms, and a Hyrule that never gets smaller",
  "Björk to PinkPantheress: pop that bends, R&B that glows after dark",
  "Beats from the basement, DOOM to MIKE: dense, dusty, no hooks wasted",
  "Distorted 808s and ad-libs: Uzi, Keef, Thug, Tyler at full volume",
  "Westside, Kendrick, Pusha: the hall of fame, front to back",
  "Men who want it too much: Nolan, Anderson, Kubrick and the cost",
  "Spider-Verse to Gotham: masks, mourning and the long cut of the cape",
  "The Devils to Talk to Me: dread in daylight, the new and the very old",
  "Talking lions, tin cars and toys: animation that gets under your ribs",
  "Informants, bombs and detectives: Bong, Scorsese, Fincher in the dark",
  "Sicario, Inception, Bag: plans that go wrong with immaculate manners",
  "Kubrick laughing at the bomb, Anderson in love: warm, odd, rewatchable",
  "Wong, Ray, Kiarostami: subtitles on, the camera in no hurry",
  "Thrones to Succession: ten-hour films that keep their cliffhangers",
];
