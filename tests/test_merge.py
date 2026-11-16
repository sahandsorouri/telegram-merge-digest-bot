"""Logic tests. No Telegram connection, no token needed.

Run:  python3 tests/test_merge.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The date tests are written for a UTC+4 zone (no daylight saving).
os.environ["TZ"] = "Etc/GMT-4"

import render  # noqa: E402
from grouping import author_of, group_items, item_from_message, timeline_blocks  # noqa: E402
from models import Batch, Item  # noqa: E402


# ------------------------------------------------------------- fixtures


def at(hh: int, mm: int, day: int = 6, month: int = 8, year: int = 2026) -> datetime:
    return datetime(year, month, day, hh, mm, tzinfo=timezone.utc)


def user(uid: int, first: str, last: str | None = None, username: str | None = None):
    return NS(id=uid, first_name=first, last_name=last, username=username)


def from_user(u, when: datetime):
    return NS(type="user", sender_user=u, date=when)


def from_hidden(name: str, when: datetime):
    return NS(type="hidden_user", sender_user_name=name, date=when)


def from_channel(title: str, chat_id: int, when: datetime, signature: str | None = None):
    return NS(type="channel", chat=NS(id=chat_id, title=title), date=when, author_signature=signature)


def from_group_chat(title: str, chat_id: int, when: datetime):
    return NS(
        type="chat",
        sender_chat=NS(id=chat_id, title=title),
        date=when,
        author_signature=None,
    )


def msg(text=None, origin=None, caption=None, when=None, **media):
    base = dict(
        text=text,
        caption=caption,
        forward_origin=origin,
        date=when or at(12, 0),
        photo=None,
        document=None,
        video=None,
        audio=None,
        voice=None,
        video_note=None,
        animation=None,
        sticker=None,
        contact=None,
        location=None,
        venue=None,
        poll=None,
        dice=None,
        game=None,
    )
    base.update(media)
    return NS(**base)


def build(messages) -> Batch:
    batch = Batch(batch_id="test", chat_id=1)
    for m in messages:
        item, media = item_from_message(m)
        if media:
            batch.skipped_media += 1
        if item:
            batch.add(item)
    return batch


# ---------------------------------------------------------------- tests


def test_author_kinds():
    ali = user(101, "Ali", "Rezaei")
    assert author_of(msg("hi", from_user(ali, at(14, 2))))[:3] == ("u:101", "Ali Rezaei", "user")
    assert author_of(msg("hi", from_hidden("Sara M", at(14, 5))))[:3] == (
        "h:sara m",
        "Sara M",
        "hidden",
    )
    assert author_of(msg("hi", from_channel("Design Weekly", -100, at(9, 30))))[:3] == (
        "c:-100",
        "Design Weekly",
        "channel",
    )
    assert author_of(msg("hi", from_group_chat("Ops Room", -200, at(9, 30))))[:3] == (
        "c:-200",
        "Ops Room",
        "chat",
    )
    # Typed straight into the bot, or forwarded with the sender name hidden.
    assert author_of(msg("typed here"))[:3] == ("unknown", "Unknown sender", "unknown")


def test_username_and_signature_fallbacks():
    only_username = user(7, "", None, "example_user")
    assert author_of(msg("x", from_user(only_username, at(1, 0))))[1] == "@example_user"
    signed = from_channel("Example News", -5, at(1, 0), signature="Editor")
    assert author_of(msg("x", signed))[1] == "Example News (Editor)"


def test_original_time_is_kept_not_forward_time():
    origin = from_user(user(1, "Ali"), at(9, 15))
    item, _ = item_from_message(msg("old news", origin, when=at(18, 0)))
    assert item.ts == int(at(9, 15).timestamp()), "must use the original send time"


def test_text_only_policy():
    # Photo with no caption: skipped, counted, no item.
    item, media = item_from_message(msg(photo=["file_id"]))
    assert item is None and media is True
    # Photo with a caption: caption kept, attachment still counted.
    item, media = item_from_message(
        msg(caption="see slide 3", origin=from_user(user(1, "Ali"), at(10, 0)), photo=["f"])
    )
    assert item is not None and item.text == "see slide 3" and media is True
    # Voice note: counted, dropped.
    item, media = item_from_message(msg(voice=NS(duration=42)))
    assert item is None and media is True


def test_grouping_order_and_counts():
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    batch = build(
        [
            msg("Deck is sent, check the third slide.", from_user(ali, at(14, 2))),
            msg("Received. The org chart page is still old.", from_user(sara, at(14, 5))),
            msg("We also need the Arabic version before Sunday.", from_user(ali, at(14, 3))),
            msg("Office closed on Sunday.", from_channel("Design Weekly", -100, at(9, 30))),
            msg("Ignore the last one, Arabic can wait.", from_user(ali, at(14, 11))),
        ]
    )
    groups = group_items(batch.items)
    assert [g.name for g in groups] == ["Design Weekly", "Ali Rezaei", "Sara Movahed"], (
        "groups order by first appearance in real time, channel posted at 09:30"
    )
    assert [len(g.items) for g in groups] == [1, 3, 1]
    # Ali's messages are in original order even though they arrived shuffled.
    ali_group = next(g for g in groups if g.name == "Ali Rezaei")
    assert [i.text[:6] for i in ali_group.items] == ["Deck i", "We als", "Ignore"]


def test_hidden_and_visible_same_person_merge():
    batch = build(
        [
            msg("first", from_user(user(9, "Sara", "Movahed"), at(10, 0))),
            msg("second", from_hidden("Sara Movahed", at(10, 5))),
        ]
    )
    groups = group_items(batch.items)
    assert len(groups) == 1, "same display name should fold into one person"
    assert len(groups[0].items) == 2


def test_timeline_keeps_real_time_order():
    """The default view must never reshuffle the conversation."""
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    batch = build(
        [
            msg("A one", from_user(ali, at(14, 2))),
            msg("A two", from_user(ali, at(14, 3))),
            msg("S one", from_user(sara, at(14, 5))),
            msg("A three", from_user(ali, at(14, 11))),
            msg("S two", from_user(sara, at(14, 12))),
        ]
    )
    blocks = timeline_blocks(batch.items)
    assert [b.name for b in blocks] == [
        "Ali Rezaei",
        "Sara Movahed",
        "Ali Rezaei",
        "Sara Movahed",
    ], "a person who speaks twice gets two blocks, order is preserved"
    assert [len(b.items) for b in blocks] == [2, 1, 1, 1], "consecutive runs merge"

    flat = [i.text for b in blocks for i in b.items]
    assert flat == ["A one", "A two", "S one", "A three", "S two"], "strict time order"


def test_timeline_sorts_messages_that_arrive_out_of_order():
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("late", from_user(ali, at(15, 0))),
            msg("early", from_user(ali, at(9, 0))),
        ]
    )
    blocks = timeline_blocks(batch.items)
    assert [i.text for i in blocks[0].items] == ["early", "late"]


def test_persian_text_is_bidi_isolated():
    """A right-to-left message on a line with a left-to-right timestamp."""
    fa = "سلام، دک رو فرستادم."
    ali = user(101, "Ali", "Rezaei")
    batch = build([msg(fa, from_user(ali, at(14, 2)))])
    blocks = timeline_blocks(batch.items)
    when = int(at(14, 22).timestamp())

    digest = render.render_timeline(blocks, total=1, skipped=0)
    assert render.FSI + fa + render.PDI in digest, "Persian must be wrapped in an isolate"

    markdown = render.render_markdown(blocks, view=render.VIEW_TIMELINE, total=1, skipped=0, when_ts=when)
    assert render.FSI + fa + render.PDI in markdown
    assert markdown.encode("utf-8").decode("utf-8") == markdown


def test_english_output_has_no_invisible_characters():
    """The isolate is only added where it is needed."""
    ali = user(101, "Ali", "Rezaei")
    batch = build([msg("plain english", from_user(ali, at(14, 2)))])
    blocks = timeline_blocks(batch.items)
    when = int(at(14, 22).timestamp())

    for text in (
        render.render_timeline(blocks, total=1, skipped=0),
        render.render_markdown(blocks, view=render.VIEW_TIMELINE, total=1, skipped=0, when_ts=when),
    ):
        assert render.FSI not in text and render.PDI not in text


def test_rtl_detection():
    assert render.has_rtl("سلام") is True
    assert render.has_rtl("مرحبا") is True
    assert render.has_rtl("hello") is False
    assert render.has_rtl("14:02") is False
    assert render.has_rtl("mixed سلام here") is True


def test_persian_name_in_frontmatter_stays_readable():
    name = "مریم احمدی"
    batch = build([msg("hi", from_hidden(name, at(10, 0)))])
    markdown = render.render_markdown(
        timeline_blocks(batch.items),
        view=render.VIEW_TIMELINE,
        total=1,
        skipped=0,
        when_ts=int(at(10, 0).timestamp()),
    )
    assert f'people: ["{name}"]' in markdown, "no \\uXXXX escaping in the YAML"


def test_single_day_batch_shows_the_date_once_and_bare_times():
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("morning", from_user(ali, at(6, 0, day=6))),
            msg("evening", from_user(ali, at(15, 0, day=6))),
        ]
    )
    blocks = timeline_blocks(batch.items)
    span = render.Span(blocks)
    assert span.multi_day is False
    assert len(span.days) == 1

    out = render.render_timeline(blocks, total=2, skipped=0)
    assert "Thu 06 Aug 2026" in out, "the date belongs in the header"
    assert "📅" not in out, "no day separators are needed inside one day"
    assert span.stamp(batch.items[0].ts) == render.fmt_time(batch.items[0].ts)


def test_multi_day_batch_separates_the_days():
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    batch = build(
        [
            msg("day one", from_user(ali, at(10, 0, day=4))),
            msg("day one again", from_user(ali, at(11, 0, day=4))),
            msg("day two", from_user(sara, at(9, 0, day=6))),
        ]
    )
    blocks = timeline_blocks(batch.items)
    span = render.Span(blocks)
    assert span.multi_day is True
    assert len(span.days) == 2

    out = render.render_timeline(blocks, total=3, skipped=0)
    assert "Tue 04 Aug to Thu 06 Aug 2026" in out, "the header states the real range"
    assert "2 days" in out
    assert out.count("📅") == 2, "one date line per day"
    assert "📅 <b>Tue 04 Aug</b>" in out
    assert "📅 <b>Thu 06 Aug</b>" in out


def test_a_block_never_spans_midnight():
    """23:59 and 00:01 are different days to a reader, so they cannot merge."""
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("before midnight", from_user(ali, at(19, 59, day=4))),
            msg("after midnight", from_user(ali, at(20, 1, day=4))),
        ]
    )
    # 19:59 and 20:01 UTC are 23:59 and 00:01 in UTC+4.
    blocks = timeline_blocks(batch.items)
    assert len(blocks) == 2, "same person, but the day changed between them"
    assert [i.text for b in blocks for i in b.items] == ["before midnight", "after midnight"]


def test_by_person_view_dates_every_timestamp_when_days_are_mixed():
    """This view has no day separator to lean on, so times must carry a date."""
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("first", from_user(ali, at(10, 0, day=4))),
            msg("second", from_user(ali, at(10, 0, day=6))),
        ]
    )
    out = render.render_digest(group_items(batch.items), total=2, skipped=0)
    assert "04 Aug 14:00" in out and "06 Aug 14:00" in out, "each line carries its own date"

    same_day = build([msg("only", from_user(ali, at(10, 0, day=6)))])
    out = render.render_digest(group_items(same_day.items), total=1, skipped=0)
    assert "06 Aug" not in out.split("\n", 2)[2], "no date clutter inside a single day"


def test_span_across_years():
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("old", from_user(ali, at(10, 0, day=30, month=12, year=2025))),
            msg("new", from_user(ali, at(10, 0, day=2, month=1, year=2026))),
        ]
    )
    blocks = timeline_blocks(batch.items)
    span = render.Span(blocks)
    assert span.crosses_years is True
    assert span.label() == "Tue 30 Dec 2025 to Fri 02 Jan 2026", "both years must be visible"
    out = render.render_timeline(blocks, total=2, skipped=0)
    assert "📅 <b>Tue 30 Dec 2025</b>" in out, "day lines carry the year when years differ"


def test_markdown_days_and_filename():
    ali = user(101, "Ali", "Rezaei")
    multi = build(
        [
            msg("one", from_user(ali, at(10, 0, day=4))),
            msg("two", from_user(ali, at(10, 0, day=6))),
        ]
    )
    blocks = timeline_blocks(multi.items)
    md = render.render_markdown(blocks, view=render.VIEW_TIMELINE, total=2, skipped=0, when_ts=int(at(20, 0, day=6).timestamp()))
    assert "from: 2026-08-04" in md and "to: 2026-08-06" in md
    assert "days: 2" in md
    assert "## Tue 04 Aug 2026" in md and "## Thu 06 Aug 2026" in md
    assert render.markdown_filename(blocks, 0) == "2026-08-04 to 2026-08-06 Telegram Merge.md"

    single = build([msg("one", from_user(ali, at(10, 0, day=6)))])
    one_block = timeline_blocks(single.items)
    md = render.render_markdown(one_block, view=render.VIEW_TIMELINE, total=1, skipped=0, when_ts=0)
    assert "## Thu 06 Aug 2026" not in md, "a single day needs no day heading"
    assert render.markdown_filename(one_block, 0) == "2026-08-06 Telegram Merge.md"


def test_filename_uses_message_dates_not_merge_date():
    """Forwarding old messages today must not name the file after today."""
    ali = user(101, "Ali", "Rezaei")
    batch = build([msg("old news", from_user(ali, at(10, 0, day=1, month=3)))])
    blocks = timeline_blocks(batch.items)
    merged_today = int(at(23, 0, day=6).timestamp())
    assert render.markdown_filename(blocks, merged_today) == "2026-03-01 Telegram Merge.md"


def _three_view_batch():
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    return build(
        [
            msg("A one", from_user(ali, at(10, 0, day=4))),
            msg("S one", from_user(sara, at(11, 0, day=4))),
            msg("A two", from_user(ali, at(12, 0, day=6))),
        ]
    )


def test_md_export_matches_the_view_you_are_on():
    """The file button exports what is on screen, not a fixed shape."""
    batch = _three_view_batch()
    blocks = timeline_blocks(batch.items)
    groups = group_items(batch.items)
    when = int(at(20, 0, day=6).timestamp())

    timeline_md = render.render_markdown(
        blocks, view=render.VIEW_TIMELINE, total=3, skipped=0, when_ts=when
    )
    person_md = render.render_markdown(
        groups, view=render.VIEW_PERSON, total=3, skipped=0, when_ts=when
    )
    stitched_md = render.render_markdown(
        groups, view=render.VIEW_STITCHED, total=3, skipped=0, when_ts=when
    )

    assert timeline_md != person_md != stitched_md, "three genuinely different files"

    # Timeline: day headings, and Ali appears twice because he speaks twice.
    assert "## Tue 04 Aug 2026" in timeline_md and "## Thu 06 Aug 2026" in timeline_md
    assert timeline_md.count("**Ali Rezaei**") == 2
    assert "in the order they were sent" in timeline_md

    # By person: one heading per person, every timestamp dated, no day headings.
    assert person_md.count("## Ali Rezaei") == 1
    assert "## Tue 04 Aug 2026" not in person_md
    assert "**04 Aug 14:00**" in person_md and "**06 Aug 16:00**" in person_md
    assert "grouped by person" in person_md

    # Stitched: one paragraph per person, no timestamps at all.
    assert "A one A two" in stitched_md, "a person's messages run together"
    assert "**" not in stitched_md.split("---", 2)[2], "no bold timestamps in the body"
    assert "one block per person" in stitched_md


def test_each_view_declares_itself_and_gets_its_own_filename():
    batch = _three_view_batch()
    blocks = timeline_blocks(batch.items)
    groups = group_items(batch.items)

    for view, source, expected in (
        (render.VIEW_TIMELINE, blocks, "2026-08-04 to 2026-08-06 Telegram Merge.md"),
        (render.VIEW_PERSON, groups, "2026-08-04 to 2026-08-06 Telegram Merge By Person.md"),
        (render.VIEW_STITCHED, groups, "2026-08-04 to 2026-08-06 Telegram Merge Stitched.md"),
    ):
        md = render.render_markdown(source, view=view, total=3, skipped=0, when_ts=0)
        assert f"view: {view}" in md, "the note records which shape it is"
        assert render.markdown_filename(source, 0, view) == expected

    names = {render.markdown_filename(groups, 0, v) for v in render.VIEW_NAMES}
    assert len(names) == 3, "exporting all three must not overwrite one file"


def test_no_h3_anywhere_and_no_skipped_levels():
    """A viewer that ignores H3 dropped the author lines completely once.

    Nothing that has to be readable may depend on H3 again.
    """
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    batches = {
        "one day": [
            msg("a", from_user(ali, at(10, 0, day=6))),
            msg("b", from_user(sara, at(11, 0, day=6))),
        ],
        "two days": [
            msg("a", from_user(ali, at(10, 0, day=4))),
            msg("b", from_user(sara, at(11, 0, day=6))),
        ],
    }
    for label, messages in batches.items():
        batch = build(messages)
        for view, source in (
            (render.VIEW_TIMELINE, timeline_blocks(batch.items)),
            (render.VIEW_PERSON, group_items(batch.items)),
            (render.VIEW_STITCHED, group_items(batch.items)),
        ):
            md = render.render_markdown(source, view=view, total=2, skipped=0, when_ts=0)
            assert "### " not in md, f"H3 used in the {view} file ({label})"
            levels = [len(x) - len(x.lstrip("#")) for x in md.splitlines() if x.startswith("#")]
            assert max((b - a for a, b in zip(levels, levels[1:])), default=0) <= 1, (
                f"skipped a heading level in the {view} file ({label})"
            )


def test_every_view_puts_the_author_names_in_the_file():
    """The names must be visibly on the page, not only in the frontmatter."""
    batch = _three_view_batch()
    for view, source, marker in (
        (render.VIEW_TIMELINE, timeline_blocks(batch.items), "**{}**"),
        (render.VIEW_PERSON, group_items(batch.items), "## {}"),
        (render.VIEW_STITCHED, group_items(batch.items), "## {}"),
    ):
        md = render.render_markdown(source, view=view, total=3, skipped=0, when_ts=0)
        body = md.split("---", 2)[2]
        for name in ("Ali Rezaei", "Sara Movahed"):
            assert marker.format(name) in body, f"{name} not visible in the {view} file"
            assert f'"{name}"' in md, f"{name} missing from the {view} frontmatter"


def test_unknown_view_falls_back_to_timeline():
    batch = _three_view_batch()
    blocks = timeline_blocks(batch.items)
    md = render.render_markdown(blocks, view="nonsense", total=3, skipped=0, when_ts=0)
    assert "view: timeline" in md
    assert render.markdown_filename(blocks, 0, "nonsense").endswith("Telegram Merge.md")


def test_status_line_counts_days():
    assert "2 days" in render.render_status(total=5, groups=2, skipped=0, days=2)
    assert "day" not in render.render_status(total=5, groups=2, skipped=0, days=1)


def test_renderers_produce_output():
    ali = user(101, "Ali", "Rezaei")
    batch = build(
        [
            msg("line one\nline two", from_user(ali, at(14, 2))),
            msg("<b>not html</b> & co", from_user(ali, at(14, 3))),
            msg(photo=["x"]),
        ]
    )
    groups = group_items(batch.items)
    when = int(at(14, 22).timestamp())

    digest = render.render_digest(groups, total=batch.count, skipped=batch.skipped_media)
    assert "&lt;b&gt;not html&lt;/b&gt; &amp; co" in digest, "user text must be escaped"
    assert "1 attachment skipped" in digest
    assert "2 messages" in digest

    stitched = render.render_stitched(groups, total=batch.count, skipped=batch.skipped_media)
    assert "Ali Rezaei" in stitched

    markdown = render.render_markdown(
        groups, view=render.VIEW_TIMELINE, total=batch.count, skipped=batch.skipped_media, when_ts=when
    )
    assert markdown.startswith("---\ntype: note")
    assert '"Ali Rezaei"' in markdown
    assert "**Ali Rezaei**" in markdown  # names are bold, never H3
    assert render.markdown_filename(groups, when).endswith("Telegram Merge.md")


def test_status_line_states_never_asks():
    line = render.render_status(total=7, groups=3, skipped=0)
    assert "Collecting" in line
    assert "7 messages · 3 people" in line
    assert "?" not in line, "the status line must not ask a question"


def test_batch_roundtrip():
    batch = build([msg("hello", from_user(user(1, "Ali"), at(10, 0)))])
    batch.skipped_media = 2
    clone = Batch.from_dict(batch.to_dict())
    assert clone.count == 1 and clone.skipped_media == 2
    assert clone.items[0].author_name == "Ali"
    assert clone.closed_ts == 0


def _temp_store():
    import tempfile
    from pathlib import Path as P

    from store import Store

    return Store(P(tempfile.mkdtemp()) / "state.json")


def test_two_people_do_not_share_state():
    """The bot can serve several people at once. Batches must not cross."""
    s = _temp_store()
    a = s.get_or_open(111)
    b = s.get_or_open(222)
    a.add(Item("u:1", "Ali", "user", 1000, "mine", 0))
    b.add(Item("u:2", "Sara", "user", 1000, "hers", 0))
    assert s.get_open(111).count == 1 and s.get_open(222).count == 1
    assert a.batch_id != b.batch_id
    s.close(111)
    assert s.get_open(111) is None, "closing one person's batch must not touch the other"
    assert s.get_open(222).count == 1


def test_keep_finished_is_per_person():
    """A heavy user must not evict someone else's result."""
    from config import KEEP_FINISHED

    s = _temp_store()
    for _ in range(KEEP_FINISHED + 3):
        batch = s.get_or_open(111)
        batch.add(Item("u:1", "Ali", "user", 1000, "x", 0))
        s.close(111)

    quiet = s.get_or_open(222)
    quiet.add(Item("u:2", "Sara", "user", 1000, "y", 0))
    s.close(222)
    quiet_id = quiet.batch_id

    for _ in range(KEEP_FINISHED + 3):
        batch = s.get_or_open(111)
        batch.add(Item("u:1", "Ali", "user", 1000, "x", 0))
        s.close(111)

    assert s.get_finished(quiet_id) is not None, "the quiet user's result survived"
    mine = [b for b in s.finished.values() if b.chat_id == 111]
    assert len(mine) == KEEP_FINISHED, "the heavy user is capped, not everyone else"


def test_purge_deletes_old_batches():
    import time as _t

    from config import RETENTION_HOURS, STALE_BATCH_HOURS

    s = _temp_store()
    now = int(_t.time())

    fresh = s.get_or_open(111)
    fresh.add(Item("u:1", "Ali", "user", 1000, "x", 0))
    s.close(111)
    fresh_id = fresh.batch_id

    old = s.get_or_open(222)
    old.add(Item("u:2", "Sara", "user", 1000, "y", 0))
    s.close(222)
    old.closed_ts = now - int(RETENTION_HOURS * 3600) - 60
    old_id = old.batch_id

    abandoned = s.get_or_open(333)
    abandoned.created_ts = now - int(STALE_BATCH_HOURS * 3600) - 60

    removed = s.purge(now)
    assert removed == 2
    assert s.get_finished(fresh_id) is not None, "a fresh result must survive"
    assert s.get_finished(old_id) is None, "past retention must be deleted"
    assert s.get_open(333) is None, "an abandoned batch must expire"


def test_allowlist_is_default_deny():
    import config

    ids = config.parse_ids(" 111, 222 ,abc,, ")
    assert ids == {111, 222}
    assert config.is_allowed(111, ids)
    assert not config.is_allowed(333, ids)
    assert not config.is_allowed(None, ids)
    assert not config.is_allowed("111", ids)
    empty = config.parse_ids("")
    assert empty == frozenset()
    assert not config.is_allowed(111, empty), "an empty list answers nobody"


# ----------------------------------------------------------------- run

def sample_run() -> None:
    """Print one realistic batch in all three formats."""
    ali, sara = user(101, "Ali", "Rezaei"), user(102, "Sara", "Movahed")
    batch = build(
        [
            msg("Deck is sent, check the third slide.", from_user(ali, at(14, 2))),
            msg("We also need the Arabic version before Sunday.", from_user(ali, at(14, 3))),
            msg("Received. The org chart page is still old.", from_user(sara, at(14, 5))),
            msg("I am off next week, Wednesday is my last day to review.", from_user(sara, at(14, 7))),
            msg("Ignore the last one, Arabic can wait.", from_user(ali, at(14, 11))),
            msg("Office closed on Sunday.", from_channel("Design Weekly", -100, at(9, 30, day=4))),
            msg("Payroll moves to the 25th.", from_channel("Design Weekly", -100, at(9, 31, day=4))),
            msg(photo=["file_id"]),
        ]
    )
    groups = group_items(batch.items)
    when = int(at(14, 22).timestamp())

    blocks = timeline_blocks(batch.items)
    days = len({__import__("grouping").local_day(i.ts) for i in batch.items})
    print("\n=== STATUS LINE (while collecting) ===")
    print(render.render_status(total=batch.count, groups=len(groups), skipped=batch.skipped_media, days=days))
    print("\n=== TIME ORDER (default, after Done) ===")
    print(render.render_timeline(blocks, total=batch.count, skipped=batch.skipped_media))
    print("\n=== BY PERSON ===")
    print(render.render_digest(groups, total=batch.count, skipped=batch.skipped_media))
    print("\n=== STITCHED ===")
    print(render.render_stitched(groups, total=batch.count, skipped=batch.skipped_media))
    print("\n=== .MD FILE ===")
    print(render.render_markdown(blocks, view=render.VIEW_TIMELINE, total=batch.count, skipped=batch.skipped_media, when_ts=when))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if not failed:
        sample_run()
    sys.exit(1 if failed else 0)
