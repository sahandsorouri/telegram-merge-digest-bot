"""Output formats: timeline, by person, stitched, markdown file.

Timeline is the default. Real time order matters more than tidy grouping, so
the conversation reads top to bottom the way it happened, and a run of messages
from one person collapses into a single block.

Dates are handled once, here, by a single rule: a batch that lives inside one
day says so in the header and shows bare times, and a batch that spans days
puts the day in front of the messages that belong to it. A time with no date
attached is never printed when the batch covers more than one day.
"""

from __future__ import annotations

import html
import json
from datetime import datetime

from config import TIMEZONE
from grouping import local_day
from models import Group

SEPARATOR = "━━━━━━━━━━━━━━━━━━━"

ICONS = {
    "user": "👤",
    "hidden": "👤",
    "chat": "👥",
    "channel": "📢",
    "unknown": "❓",
}

# Width of "14:02  ", used to indent wrapped lines under their timestamp.
INDENT = " " * 7

# Bidirectional text.
#
# A line like "14:02  سلام." mixes a left-to-right timestamp with a
# right-to-left message. Without help, the bidi algorithm gives the whole line
# the direction of the first strong character, which is the digit, so the
# trailing "." of the Persian sentence gets pushed to the wrong visual side.
#
# FSI and PDI wrap a run in a "first strong isolate": the run picks its own
# direction from its own first strong character, and it cannot leak into the
# text around it. This is exactly the case they exist for.
FSI = "⁨"
PDI = "⁩"

# Arabic, Persian, Hebrew, and their presentation forms.
RTL_RANGES = (
    (0x0590, 0x05FF),
    (0x0600, 0x06FF),
    (0x0700, 0x074F),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB1D, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def has_rtl(text: str) -> bool:
    return any(any(low <= ord(ch) <= high for low, high in RTL_RANGES) for ch in text)


def bidi(text: str) -> str:
    """Isolate a run only when it actually needs it.

    Pure English output stays byte clean, no invisible characters in the file.
    """
    return f"{FSI}{text}{PDI}" if has_rtl(text) else text


# ------------------------------------------------------------------ time


def _local(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TIMEZONE)


def fmt_time(ts: int) -> str:
    return _local(ts).strftime("%H:%M")


def fmt_day(ts: int) -> str:
    return _local(ts).strftime("%Y-%m-%d")


def day_label(ts: int, *, with_year: bool = False) -> str:
    return _local(ts).strftime("%a %d %b %Y" if with_year else "%a %d %b")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _count_people(n: int) -> str:
    return "1 person" if n == 1 else f"{n} people"


def _items(groups: list[Group]) -> list:
    return [item for group in groups for item in group.items]


class Span:
    """When the messages in a batch were actually sent.

    Everything about dates in the output is decided from this, so the header,
    the day separators, and the timestamps can never disagree with each other.
    """

    def __init__(self, groups: list[Group]) -> None:
        stamps = [item.ts for item in _items(groups)]
        self.first = min(stamps) if stamps else 0
        self.last = max(stamps) if stamps else 0
        self.days = sorted({local_day(ts) for ts in stamps})

    @property
    def multi_day(self) -> bool:
        return len(self.days) > 1

    @property
    def crosses_years(self) -> bool:
        return _local(self.first).year != _local(self.last).year

    def label(self) -> str:
        """One line describing when the conversation happened."""
        if not self.days:
            return ""
        if not self.multi_day:
            return day_label(self.first, with_year=True)
        start = day_label(self.first, with_year=self.crosses_years)
        return f"{start} to {day_label(self.last, with_year=True)}"

    def stamp(self, ts: int) -> str:
        """A bare time inside a single day, a dated time when days are mixed.

        In the timeline view a day separator already carries the date, so this
        is only used where there is nothing else to anchor the time to.
        """
        return _local(ts).strftime("%d %b %H:%M") if self.multi_day else fmt_time(ts)


# --------------------------------------------------------------- pieces


def _indent_block(text: str, indent: str) -> str:
    lines = text.splitlines() or [""]
    return ("\n" + indent).join(lines)


def _header(total: int, people: int, span: Span) -> list[str]:
    out = [f"📋 <b>Merged</b> · {plural(total, 'message')} · {_count_people(people)}"]
    label = span.label()
    if label:
        if span.multi_day:
            label = f"{label}  ·  {plural(len(span.days), 'day')}"
        out.append(label)
    out.append(SEPARATOR)
    return out


def _footer(skipped: int) -> list[str]:
    out = [SEPARATOR]
    if skipped:
        out.append(f"<i>{plural(skipped, 'attachment')} skipped, this bot merges text only.</i>")
    return out


# -------------------------------------------------------------- formats


def render_timeline(blocks: list[Group], *, total: int, skipped: int) -> str:
    """Default. Real time order, with a date line whenever the day changes."""
    span = Span(blocks)
    people = len({b.key for b in blocks})
    out = _header(total, people, span)

    current_day = None
    for block in blocks:
        day = local_day(block.items[0].ts)
        if span.multi_day and day != current_day:
            out.append(f"📅 <b>{day_label(block.items[0].ts, with_year=span.crosses_years)}</b>")
            current_day = day
        icon = ICONS.get(block.kind, "👤")
        out.append(f"{icon} <b>{bidi(html.escape(block.name))}</b>")
        for item in block.items:
            body = _indent_block(bidi(html.escape(item.text)), INDENT)
            out.append(f"{fmt_time(item.ts)}  {body}")
        out.append("")

    out.extend(_footer(skipped))
    return "\n".join(out).strip()


def render_digest(groups: list[Group], *, total: int, skipped: int) -> str:
    """Everything from one person together. Time order across people is lost.

    There is no day separator to lean on here, so each timestamp carries its
    own date whenever the batch spans more than one day.
    """
    span = Span(groups)
    out = _header(total, len(groups), span)
    for group in groups:
        icon = ICONS.get(group.kind, "👤")
        out.append(
            f"{icon} <b>{bidi(html.escape(group.name))}</b> · {plural(len(group.items), 'message')}"
        )
        for item in group.items:
            body = _indent_block(bidi(html.escape(item.text)), INDENT)
            out.append(f"{span.stamp(item.ts)}  {body}")
        out.append("")
    out.extend(_footer(skipped))
    return "\n".join(out).strip()


def render_stitched(groups: list[Group], *, total: int, skipped: int) -> str:
    """One continuous block per person. No timestamps."""
    span = Span(groups)
    out = [f"📋 <b>Merged</b> · {plural(total, 'message')} · {_count_people(len(groups))}"]
    if span.label():
        out.append(span.label())
    out.append("")
    for group in groups:
        out.append(f"<b>{bidi(html.escape(group.name))}</b>")
        joined = " ".join(item.text.strip() for item in group.items if item.text.strip())
        out.append(bidi(html.escape(joined)))
        out.append("")
    if skipped:
        out.append(f"<i>{plural(skipped, 'attachment')} skipped, this bot merges text only.</i>")
    return "\n".join(out).strip()


# The three shapes a note can take, matching the three views in the chat.
VIEW_TIMELINE = "timeline"
VIEW_PERSON = "person"
VIEW_STITCHED = "stitched"

VIEW_NAMES = {
    VIEW_TIMELINE: "Time order",
    VIEW_PERSON: "By person",
    VIEW_STITCHED: "Stitched",
}

VIEW_INTROS = {
    VIEW_TIMELINE: "in the order they were sent",
    VIEW_PERSON: "grouped by person",
    VIEW_STITCHED: "one block per person",
}

# Appended to the filename so exporting two views does not overwrite one file.
VIEW_SUFFIX = {
    VIEW_TIMELINE: "",
    VIEW_PERSON: " By Person",
    VIEW_STITCHED: " Stitched",
}


def _unique_names(groups: list[Group]) -> list[str]:
    names: list[str] = []
    for group in groups:
        if group.name not in names:
            names.append(group.name)
    return names


def _frontmatter(groups, span, view, total, skipped, when_ts, names) -> list[str]:
    people = ", ".join(json.dumps(n, ensure_ascii=False) for n in names)
    return [
        "---",
        "type: note",
        "source: telegram-merge",
        f"view: {view}",
        f"captured: {fmt_day(when_ts)}",
        f"from: {fmt_day(span.first)}",
        f"to: {fmt_day(span.last)}",
        f"people: [{people}]",
        f"messages: {total}",
        f"days: {len(span.days)}",
        f"skipped_attachments: {skipped}",
        "tags: [telegram, merge, inbox]",
        "---",
        "",
    ]


def render_markdown(
    groups: list[Group], *, view: str, total: int, skipped: int, when_ts: int
) -> str:
    """Vault ready note in the shape of whichever view you are looking at.

    `groups` is timeline blocks for the timeline view and per-person groups for
    the other two, exactly what the matching chat renderer is given.
    """
    if view not in VIEW_NAMES:
        view = VIEW_TIMELINE
    span = Span(groups)
    names = _unique_names(groups)

    out = _frontmatter(groups, span, view, total, skipped, when_ts, names)
    out += [
        f"# Telegram Merge, {span.label()}",
        "",
        f"{plural(total, 'message')} from {_count_people(len(names))}"
        + (f" across {plural(len(span.days), 'day')}" if span.multi_day else "")
        + f", {VIEW_INTROS[view]}.",
        "",
    ]

    if view == VIEW_TIMELINE:
        # The author name is a bold line, not an H3.
        #
        # Nesting a name under a day heading needs H3, and not every Markdown
        # viewer renders H3. One of them dropped the name lines entirely and
        # the note came out looking like nobody wrote anything. Bold renders
        # in every viewer there is, so the name is never at the mercy of a
        # renderer again. Days keep their H2, which is what the outline is for.
        current_day = None
        for block in groups:
            day = local_day(block.items[0].ts)
            if span.multi_day and day != current_day:
                out.append(f"## {day_label(block.items[0].ts, with_year=True)}")
                out.append("")
                current_day = day
            out.append(f"**{bidi(block.name)}**")
            out.append("")
            for item in block.items:
                out.append(f"- **{fmt_time(item.ts)}** {_indent_block(bidi(item.text), '  ')}")
            out.append("")

    elif view == VIEW_PERSON:
        for group in groups:
            out.append(f"## {bidi(group.name)}")
            out.append("")
            for item in group.items:
                out.append(f"- **{span.stamp(item.ts)}** {_indent_block(bidi(item.text), '  ')}")
            out.append("")

    else:
        for group in groups:
            out.append(f"## {bidi(group.name)}")
            out.append("")
            joined = " ".join(item.text.strip() for item in group.items if item.text.strip())
            out.append(bidi(joined))
            out.append("")

    if skipped:
        out.append("---")
        out.append("")
        verb = "was" if skipped == 1 else "were"
        out.append(f"{plural(skipped, 'attachment')} {verb} skipped. This bot merges text only.")
        out.append("")
    return "\n".join(out)


def markdown_filename(groups: list[Group], when_ts: int, view: str = VIEW_TIMELINE) -> str:
    """Named after the messages, not after when they happened to be merged."""
    span = Span(groups)
    suffix = VIEW_SUFFIX.get(view, "")
    if not span.days:
        stamp = fmt_day(when_ts)
    elif span.multi_day:
        stamp = f"{fmt_day(span.first)} to {fmt_day(span.last)}"
    else:
        stamp = fmt_day(span.first)
    return f"{stamp} Telegram Merge{suffix}.md"


def render_status(*, total: int, groups: int, skipped: int, days: int = 1, capped: bool = False) -> str:
    """The collecting line. States what it has. Never asks a question."""
    if total == 0:
        out = ["🧺 <b>Collecting</b>", "Nothing yet. Forward messages here."]
    else:
        line = f"{plural(total, 'message')} · {_count_people(groups)}"
        if days > 1:
            line += f" · {plural(days, 'day')}"
        out = ["🧺 <b>Collecting</b>", line, "Forward more, or tap Done."]
    if skipped:
        out.append(f"<i>{plural(skipped, 'attachment')} skipped, text only.</i>")
    if capped:
        out.append("<i>Batch is full. Tap Done, new messages are not being added.</i>")
    return "\n".join(out)
