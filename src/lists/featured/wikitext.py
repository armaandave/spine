"""Read award winners out of a Wikipedia ceremony page's wikitext.

Pure functions: no network and no Django, so recorded revisions replay exactly.

``read_page(wikitext, labels)`` finds the categories in ``labels`` (normalized page
label -> nominee-file category) and every entry the page marks as a winner:

- an entry that starts in bold (``'''…'''``), the convention "winners are listed
  first and highlighted in boldface";
- an entry carrying a double dagger (``‡``, ``{{double-dagger}}``) or ``{{won}}``;
- a table row or cell shaded in a colour the page's ``{{legend|<colour>|… winner}}``
  declares;
- an entry in a table column headed "Winner";
- an infobox ``awardN_type``/``awardN_winner`` pair, or an infobox field named in
  ``infobox`` (field -> category).

A category is found where its label heads a section, a definition term, a bold
line, a table column, a table row, or a table cell (``{{Award category|…}}``).

``evaluate(reading, nominees)`` turns marks into one outcome per category: the
marked nominees, no winner yet, review (a marked entry that isn't a nominee, or
several winners without a tie), or missing (the page doesn't show the category).
"""

import html
import re
from dataclasses import dataclass, field

from lists.featured.text import normalize_title, title_forms

MISSING = "missing"
NONE = "none"
WINNERS = "winners"
REVIEW = "review"

_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_REF = re.compile(r"<ref\b[^>]*?/>|<ref\b[^>]*>.*?</ref\s*>", re.S | re.I)
_NOWIKI = re.compile(r"</?nowiki\s*/?>", re.I)
_LINK = re.compile(r"\[\[([^\[\]|]*)(?:\|([^\[\]]*))?\]\]")
_EXTERNAL = re.compile(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]")
_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_TAG = re.compile(r"<[^<>]+>")
_BREAK = re.compile(r"<br\s*/?>", re.I)
_HEADING = re.compile(r"^(={1,6})\s*(.*?)\s*\1\s*$")
_BACKGROUND = re.compile(r"background(?:-color)?\s*:\s*([^;\"'|]+)", re.I)
_LEGEND = re.compile(r"\{\{\s*legend(?:\s+inline)?\s*\|\s*([^|{}]+?)\s*\|([^{}]*)\}\}", re.I)
_BOLD_START = re.compile(r"^\s*'''")
_DAGGER = re.compile(r"‡|\{\{\s*(?:double[- ]?dagger|‡|dagger\s*\|\s*double)\s*\}\}", re.I)
_WON = re.compile(r"\{\{\s*(?:won|award[- ]won)\s*(?:\||\}\})|\(\s*winner\s*\)", re.I)
_TIE = re.compile(
    r"\(\s*tied?\s*\)|\{\{\s*tie\s*\}\}|\btied\b|\bjoint(?:ly)?\s+(?:winners?|awarded|recipients?)\b",
    re.I,
)
_PIECES = re.compile(r"\s+[–—-]\s+|\s*[,;/()\[\]]\s*|\s+as\s+|\s+from\s+|\s+for\s+", re.I)
_WINNER_HEADER = {"winner", "winners", "winner s", "won"}
# Sequel numbers compare equal either way ("Slay the Spire II" is "Slay the Spire 2").
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
_NON_ARTICLE = ("file:", "image:", "category:", "media:")

# Templates shown as their last positional argument, their first, or joined.
_SHOW_LAST = {
    "award category", "nowrap", "nobold", "small", "big", "resize", "color", "colour", "sronly",
    "sort", "lang", "transliteration", "flag", "center", "bold", "nobr", "noitalic", "vanchor",
    "anchor", "visible anchor", "ubl", "nowrap begin",
}
_SHOW_FIRST = {"abbr", "ill", "interlanguage link", "illm", "sic", "proper name"}
_SYMBOLS = {
    "double-dagger": "‡", "double dagger": "‡", "doubledagger": "‡", "‡": "‡", "dagger": "†",
    "nbsp": " ", "'": "'", "!": "|", "=": "=", "snd": " – ", "spaced ndash": " – ", "ndash": "–",
    "mdash": "—", "tie": "(tie)", "won": "Won", "-": " ", "·": " · ", "dot": " · ",
}


def _template(body):
    parts = [part.strip() for part in body.split("|")]
    name = parts[0].replace("_", " ").strip().lower()
    args = [part for part in parts[1:] if "=" not in part.split("[", 1)[0]]
    if name in _SYMBOLS:
        return _SYMBOLS[name]
    if name == "sortname":
        return " ".join(args[:2])
    if name in ("blist", "hlist", "flatlist", "plainlist", "unbulleted list"):
        return ", ".join(args)
    if name in _SHOW_LAST:
        return args[-1] if args else ""
    if name in _SHOW_FIRST:
        return args[0] if args else ""
    return ""


def _link(match):
    target, display = match.group(1), match.group(2)
    if target.strip().lower().startswith(_NON_ARTICLE):
        return ""
    return display if display is not None else target


def expand(text):
    """Replace links with their text and templates with what they show."""
    previous = None
    while previous != text:
        previous = text
        text = _LINK.sub(_link, text)
        text = _TEMPLATE.sub(lambda match: _template(match.group(1)), text)
    return text


def plain(text):
    """Return wikitext as plain text."""
    text = expand(_EXTERNAL.sub(r"\1", str(text or "")))
    text = _BREAK.sub(" ", text)
    text = _TAG.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    return " ".join(html.unescape(text).split())


def strip_noise(text):
    """Remove comments, references and nowiki tags."""
    return _NOWIKI.sub("", _REF.sub("", _COMMENT.sub("", str(text or ""))))


def loose(form):
    """Return a normalized title with Roman sequel numbers as digits."""
    return " ".join(_ROMAN.get(word, word) for word in form.split())


def forms(title):
    """Return the comparable forms of a title: in full and before any subtitle."""
    return {loose(form) for form in title_forms(title)}


def _target_name(target):
    target = target.split("#", 1)[0]
    return re.sub(r"\s*\([^()]*\)\s*$", "", target).replace("_", " ")


def names(raw):
    """Return the normalized names an entry mentions: links, italics, quotes, pieces."""
    found = set()
    for match in _LINK.finditer(raw):
        target, display = match.group(1), match.group(2)
        if target.strip().lower().startswith(_NON_ARTICLE):
            continue
        found.add(plain(_target_name(target)))
        if display is not None:
            found.add(plain(display))
    inline = html.unescape(_TAG.sub("", _BREAK.sub(" ", expand(raw)))).replace("'''", "")
    found.update(plain(segment) for segment in re.findall(r"''(.+?)''", inline))
    found.update(plain(segment) for segment in re.findall(r"[\"“](.+?)[\"”]", inline))
    text = plain(raw)
    found.add(text)
    found.update(piece for piece in _PIECES.split(text))
    found_forms = set()
    for name in found:
        found_forms |= forms(name.replace("‡", "").strip())
    return frozenset(form for form in found_forms if form)


def _color(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower().rstrip("!").strip())


def winner_colors(text):
    """Return the colours the page's legends say mark a winner."""
    colors = set()
    for color, label in _LEGEND.findall(text):
        if re.search(r"\bwin(?:ner|ners|s)?\b|\bwon\b", plain(label), re.I):
            colors.add(_color(color))
    return colors


def _backgrounds(attrs):
    return {_color(value) for value in _BACKGROUND.findall(attrs or "")}


def _template_spans(text):
    """Return ``(start, end)`` of every top-level ``{{…}}``."""
    spans, depth, start, index = [], 0, 0, 0
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            if depth == 0:
                start = index
            depth += 1
            index += 2
        elif pair == "}}" and depth:
            depth -= 1
            index += 2
            if depth == 0:
                spans.append((start, index))
        else:
            index += 1
    return spans


def split_top(text, separator):
    """Split on ``separator`` outside ``[[…]]`` and ``{{…}}``."""
    parts, depth, start, index = [], 0, 0, 0
    while index < len(text):
        pair = text[index : index + 2]
        if pair in ("[[", "{{"):
            depth += 1
            index += 2
            continue
        if pair in ("]]", "}}") and depth:
            depth -= 1
            index += 2
            continue
        if depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def infobox_fields(text):
    """Return ``{field: value}`` from the first ``{{Infobox …}}``, or ``{}``."""
    for start, end in _template_spans(text):
        body = text[start + 2 : end - 2]
        if not body.lstrip().lower().startswith("infobox"):
            continue
        fields = {}
        for part in split_top(body, "|")[1:]:
            name, equals, value = part.partition("=")
            if equals:
                fields[name.strip().lower().replace(" ", "_")] = value.strip()
        return fields
    return {}


def _flatten(text):
    """Remove the infobox and put every other multi-line template on one line."""
    pieces, last = [], 0
    for start, end in _template_spans(text):
        body = text[start:end]
        pieces.append(text[last:start])
        if body[2:].lstrip().lower().startswith("infobox"):
            pieces.append("")
        else:
            pieces.append(body.replace("\n", " "))
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def section_text(text, title):
    """Return the section headed ``title`` (normalized match), or None."""
    wanted = normalize_title(title)
    lines = text.split("\n")
    for index, line in enumerate(lines):
        heading = _HEADING.match(line.strip())
        if not heading or normalize_title(plain(heading.group(2))) != wanted:
            continue
        level = len(heading.group(1))
        body = []
        for following in lines[index + 1 :]:
            other = _HEADING.match(following.strip())
            if other and len(other.group(1)) <= level:
                break
            body.append(following)
        return "\n".join(body)
    return None


@dataclass(frozen=True)
class Mark:
    """One entry the page marks as a winner."""

    category: str
    text: str
    names: frozenset
    kind: str  # bold | dagger | won | shaded | winner-column | infobox
    nested: bool = False  # under an entry that is itself marked (credits, details)
    tie: bool = False


@dataclass
class Reading:
    """What one page revision says: the categories it shows and its marks."""

    found: set = field(default_factory=set)
    marks: list = field(default_factory=list)
    ties: set = field(default_factory=set)


@dataclass
class _Item:
    raw: str
    depth: int
    category: str | None
    marked: bool = False


@dataclass
class _Cell:
    header: bool
    attrs: str
    lines: list


class _Reader:
    def __init__(self, labels, colors):
        self.labels = labels
        self.colors = colors
        self.reading = Reading()
        self.heading_category = None
        self.label_category = None
        self.tables = []
        self.loose = []  # bullets outside tables

    # Labels -----------------------------------------------------------------

    def label(self, raw):
        """Return the category a label line names, or None."""
        text = plain(raw).replace("‡", "").strip().rstrip(":").strip()
        category = self.labels.get(normalize_title(text))
        if category is None:
            stripped = _TIE.sub("", text)
            category = self.labels.get(normalize_title(stripped))
        if category is not None:
            self.reading.found.add(category)
            if _TIE.search(raw):
                self.reading.ties.add(category)
        return category

    # Marks ------------------------------------------------------------------

    def mark_item(self, item, *, winner_column=False, nested=False):
        raw = item.raw
        kinds = []
        if _BOLD_START.match(raw):
            kinds.append("bold")
        if _DAGGER.search(raw):
            kinds.append("dagger")
        if _WON.search(raw):
            kinds.append("won")
        if winner_column and plain(raw):
            kinds.append("winner-column")
        item.marked = bool(kinds)
        if kinds and item.category:
            self.reading.marks.append(
                Mark(item.category, plain(raw)[:200], names(raw), kinds[0], nested, bool(_TIE.search(raw))),
            )

    def items_with_nesting(self, items, *, winner_column=False):
        parents = []
        for item in items:
            while parents and parents[-1].depth >= item.depth:
                parents.pop()
            nested = bool(parents) and parents[-1].marked and item.depth > 0
            self.mark_item(item, winner_column=winner_column, nested=nested)
            parents.append(item)

    # Lines ------------------------------------------------------------------

    def bullet(self, line):
        depth = len(line) - len(line.lstrip("*#:"))
        return depth, line[depth:]

    def run(self, text):
        for line in text.split("\n"):
            stripped = line.strip()
            if self.tables:
                self.table_line(stripped)
                continue
            if stripped.startswith("{|"):
                self.flush_loose()
                self.tables.append({"columns": [], "row": None, "attrs": ""})
                continue
            heading = _HEADING.match(stripped)
            if heading:
                self.flush_loose()
                self.heading_category = self.label(heading.group(2))
                self.label_category = None
                continue
            if stripped.startswith(";"):
                self.flush_loose()
                self.label_category = self.label(stripped[1:])
                continue
            if stripped.startswith(("*", "#")):
                depth, body = self.bullet(stripped)
                self.loose.append(_Item(body, depth, self.label_category or self.heading_category))
                continue
            if stripped.startswith("'''") and stripped.rstrip(":").endswith("'''"):
                category = self.label(stripped)
                if category is not None:
                    self.flush_loose()
                    self.label_category = category
        self.flush_loose()
        while self.tables:
            self.end_table()
        return self.reading

    def flush_loose(self):
        if self.loose:
            self.items_with_nesting(self.loose)
            self.loose = []

    # Tables -----------------------------------------------------------------

    def table_line(self, line):
        table = self.tables[-1]
        if line.startswith("{|"):
            self.tables.append({"columns": [], "row": None, "attrs": ""})
        elif line.startswith("|}"):
            self.end_table()
        elif line.startswith("|+"):
            self.label(line[2:])
        elif line.startswith("|-"):
            self.end_row(table)
            table["attrs"] = line[2:]
        elif line.startswith("!"):
            cells = [cell for part in split_top(line[1:], "!!") for cell in split_top(part, "||")]
            for cell in cells:
                self.add_cell(table, cell, header=True)
        elif line.startswith("|"):
            for cell in split_top(line[1:], "||"):
                self.add_cell(table, cell, header=False)
        elif table["row"]:
            table["row"][-1].lines.append(line)

    def add_cell(self, table, text, *, header):
        attrs, content = "", text
        parts = split_top(text, "|")
        if len(parts) > 1 and "'''" not in parts[0] and ("=" in parts[0] or not parts[0].strip()):
            attrs, content = parts[0], "|".join(parts[1:])
        if table["row"] is None:
            table["row"] = []
        table["row"].append(_Cell(header, attrs, [content.strip()]))

    def end_table(self):
        table = self.tables.pop()
        self.end_row(table)

    def end_row(self, table):
        cells, table["row"] = table["row"], None
        attrs, table["attrs"] = table["attrs"], ""
        if not cells:
            return
        if all(cell.header for cell in cells):
            table["columns"] = [
                (self.label(cell.lines[0]), normalize_title(plain(cell.lines[0])) in _WINNER_HEADER)
                for cell in cells
            ]
            return
        row_category, start = None, 0
        first = cells[0]
        if not any(line.startswith(("*", "#")) for line in first.lines[1:]):
            row_category = self.label(first.lines[0])
            if row_category is not None:
                start = 1
        shaded = bool(self.colors & _backgrounds(attrs))
        row_items = []
        for index in range(start, len(cells)):
            cell = cells[index]
            column_category, winner_column = (None, False)
            if index < len(table["columns"]):
                column_category, winner_column = table["columns"][index]
            category = row_category or column_category or self.label_category or self.heading_category
            items = []
            for number, line in enumerate(cell.lines):
                if not line:
                    continue
                label = self.label(line) if not line.startswith(("*", "#")) else None
                if label is not None:
                    self.items_with_nesting(items, winner_column=winner_column)
                    items, category = [], label
                    continue
                if line.startswith(("*", "#")):
                    depth, body = self.bullet(line)
                    items.append(_Item(body, depth, category))
                elif number == 0:
                    items.append(_Item(line, 0, category))
            self.items_with_nesting(items, winner_column=winner_column)
            row_items += items
            if self.colors & _backgrounds(cell.attrs):
                self.shaded(items)
        if shaded:
            self.shaded(row_items)

    def shaded(self, items):
        by_category = {}
        for item in items:
            if item.category:
                by_category.setdefault(item.category, []).append(item)
        for category, group in by_category.items():
            row_names = frozenset().union(*(names(item.raw) for item in group))
            text = " | ".join(plain(item.raw) for item in group)[:200]
            tie = any(_TIE.search(item.raw) for item in group)
            self.reading.marks.append(Mark(category, text, row_names, "shaded", False, tie))


def read_page(wikitext, labels, *, section=None, infobox=None):
    """Return a ``Reading`` of one revision.

    ``labels`` maps normalized page labels to nominee-file categories; only those
    categories are read. ``section`` limits reading to one section (its heading's
    text). ``infobox`` maps infobox field names to categories.
    """
    text = strip_noise(wikitext)
    colors = winner_colors(text)
    fields = infobox_fields(text) if section is None or infobox else {}
    body = text
    if section is not None:
        body = section_text(text, section)
        if body is None:
            return Reading()
    reader = _Reader(labels, colors)
    reading = reader.run(_flatten(body))
    _infobox_marks(reader, fields, infobox or {})
    return reading


def _infobox_marks(reader, fields, mapping):
    pairs = []
    for name, value in fields.items():
        numbered = re.fullmatch(r"award(\d+)_type", name)
        if numbered:
            pairs.append((reader.label(value), fields.get(f"award{numbered.group(1)}_winner", "")))
    for name, category in mapping.items():
        if name in fields:
            reader.reading.found.add(category)
            pairs.append((category, fields[name]))
    for category, value in pairs:
        if category and plain(value):
            reader.reading.marks.append(
                Mark(category, plain(value)[:200], names(value), "infobox", False, bool(_TIE.search(value))),
            )


@dataclass(frozen=True)
class Nominee:
    """One row of a nominee file."""

    category: str
    rank: int | None
    title: str
    year: int | None = None
    creator: str = ""
    ref: str = ""
    note: str = ""
    aliases: tuple = ()  # other titles the page may use

    @property
    def key(self):
        """Return the winner identity: the ref, else the normalized title."""
        return self.ref or f"t:{normalize_title(self.title)}"

    @property
    def forms(self):
        """Return the title forms compared with page names (full and main title)."""
        found = set()
        for title in (self.title, *self.aliases):
            found |= forms(title)
        return found


@dataclass(frozen=True)
class Outcome:
    """What a page says about one category."""

    state: str  # missing | none | winners | review
    winners: tuple = ()  # nominee keys, sorted
    reasons: tuple = ()  # review details
    tie: bool = False


def _pick(mark, hits):
    keys = {nominee.key for nominee in hits}
    if len(keys) <= 1:
        return keys
    text = normalize_title(mark.text)
    noted = {nominee.key for nominee in hits if nominee.note and normalize_title(nominee.note) in text}
    return noted if len(noted) == 1 else keys


def evaluate(reading, nominees):
    """Return ``{category: Outcome}`` for every category among ``nominees``."""
    by_category = {}
    for nominee in nominees:
        by_category.setdefault(nominee.category, []).append(nominee)
    outcomes = {}
    for category, rows in by_category.items():
        if category not in reading.found:
            outcomes[category] = Outcome(MISSING)
            continue
        winners, reasons = set(), []
        marks = [mark for mark in reading.marks if mark.category == category]
        for mark in marks:
            keys = _pick(mark, [row for row in rows if row.forms & mark.names])
            if len(keys) == 1:
                winners |= keys
            elif keys:
                reasons.append(f"marked entry matches several nominees: {mark.text}")
            elif not mark.nested:
                reasons.append(f"marked entry is not a pre-matched nominee: {mark.text}")
        tie = category in reading.ties or any(mark.tie for mark in marks)
        if reasons:
            outcomes[category] = Outcome(REVIEW, tuple(sorted(winners)), tuple(reasons), tie)
        elif not winners:
            outcomes[category] = Outcome(NONE)
        elif len(winners) > 1 and not tie:
            reason = f"{len(winners)} nominees are marked as winner and the page marks no tie"
            outcomes[category] = Outcome(REVIEW, tuple(sorted(winners)), (reason,), tie)
        else:
            outcomes[category] = Outcome(WINNERS, tuple(sorted(winners)), (), tie)
    return outcomes


def merge(outcomes):
    """Combine one category's outcomes from several pages."""
    outcomes = list(outcomes)
    reviews = [outcome for outcome in outcomes if outcome.state == REVIEW]
    if reviews:
        return Outcome(REVIEW, (), tuple(reason for outcome in reviews for reason in outcome.reasons))
    winners = sorted({key for outcome in outcomes for key in outcome.winners})
    tie = any(outcome.tie for outcome in outcomes)
    if len(winners) > 1 and not tie:
        return Outcome(REVIEW, tuple(winners), ("the pages name different winners",))
    if winners:
        return Outcome(WINNERS, tuple(winners), (), tie)
    if any(outcome.state == NONE for outcome in outcomes):
        return Outcome(NONE)
    return Outcome(MISSING)
