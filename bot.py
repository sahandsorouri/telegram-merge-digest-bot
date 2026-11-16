"""Telegram Merge Bot.

Forward a pile of messages here. The bot collects them, groups them by the
person who originally wrote them, and hands back one merged digest when you
tap Done. Nothing closes on a timer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import render
from config import (
    ALLOWED_USER_IDS,
    BOT_TOKEN,
    MAX_ITEMS,
    PURGE_EVERY_SECONDS,
    RETENTION_HOURS,
    STATUS_DEBOUNCE,
    TELEGRAM_TEXT_LIMIT,
)
from config import is_allowed
from grouping import group_items, item_from_message, local_day, timeline_blocks
from store import Store

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("merge-bot")

store = Store()

# chat_id -> pending status refresh
_pending: dict[int, asyncio.Task] = {}

HELP = (
    "<b>Telegram Merge Bot</b>\n\n"
    "Forward as many messages as you like. They can come from different chats "
    "and different days.\n\n"
    "I keep collecting until you tap <b>Done</b>. Nothing closes on its own.\n\n"
    "When you tap Done you get one digest, in the order the messages were "
    "actually sent, with each person's name on their own block.\n\n"
    "Buttons under the result switch the view: <b>By person</b> puts everything "
    "from one person together, <b>Stitched</b> drops the times.\n\n"
    "<b>Commands</b>\n"
    "/done  close the batch and build the digest\n"
    "/new  throw away the current batch and start clean\n"
    "/status  show what is in the batch right now\n"
    "/privacy  what I store and for how long\n"
    "/id  show your Telegram user id\n\n"
    "<i>Text and captions only. Attachments are counted, then skipped.</i>"
)

PRIVACY = (
    "<b>What I keep</b>\n\n"
    "While you are collecting, I hold the text of what you forwarded so I can "
    "merge it. Nothing else. No photos, no files, no voice notes, they are "
    "counted and dropped on arrival.\n\n"
    f"After you tap Done, the batch is deleted within {int(RETENTION_HOURS)} hours. "
    "That is also when the format buttons under an old digest stop working.\n\n"
    "/new deletes your current batch immediately.\n\n"
    "I never read your chats. I only ever see what you forward to me here."
)

GROUP_REPLY = "Forward your messages to me in a private chat, not in a group."


# ---------------------------------------------------------------- access


def allowed(update: Update) -> bool:
    """Default deny: only user ids listed in ALLOWED_USER_IDS."""
    user = update.effective_user
    if not user or user.is_bot:
        return False
    return is_allowed(user.id)


def is_private(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat) and chat.type == ChatType.PRIVATE


async def deny(update: Update) -> None:
    """Strangers get no answer at all."""
    user = update.effective_user
    log.info("ignored update from a user not on the list (%s)", user.id if user else "?")


# ---------------------------------------------------------- status line


def status_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Done", callback_data=f"done:{batch_id}"),
                InlineKeyboardButton("🗑 Cancel", callback_data=f"cancel:{batch_id}"),
            ]
        ]
    )


VIEWS = (
    (render.VIEW_TIMELINE, "🕒 Time order"),
    (render.VIEW_PERSON, "👤 By person"),
    (render.VIEW_STITCHED, "📝 Stitched"),
)


def result_keyboard(batch_id: str, current: str) -> InlineKeyboardMarkup:
    """The file button exports the view you are looking at, not a fixed one."""
    row = [
        InlineKeyboardButton(label, callback_data=f"fmt:{batch_id}:{kind}")
        for kind, label in VIEWS
        if kind != current
    ]
    rows = [row] if row else []
    rows.append(
        [InlineKeyboardButton("📎 .md of this view", callback_data=f"file:{batch_id}:{current}")]
    )
    return InlineKeyboardMarkup(rows)


async def refresh_status(chat_id: int, bot) -> None:
    """Re-post the status line at the bottom so Done is always reachable.

    Editing in place would leave the button stranded above the messages that
    arrived after it, which is exactly when you want to tap it.
    """
    batch = store.get_open(chat_id)
    if batch is None:
        return

    groups = group_items(batch.items)
    text = render.render_status(
        total=batch.count,
        groups=len(groups),
        skipped=batch.skipped_media,
        days=len({local_day(i.ts) for i in batch.items}),
        capped=batch.count >= MAX_ITEMS,
    )

    old_id = batch.status_message_id
    try:
        sent = await bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=status_keyboard(batch.batch_id),
            disable_notification=True,
        )
    except TelegramError as exc:
        log.warning("status send failed: %s", exc)
        return

    batch.status_message_id = sent.message_id
    store.save()

    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except TelegramError:
            pass


def schedule_status(chat_id: int, bot) -> None:
    task = _pending.pop(chat_id, None)
    if task:
        task.cancel()
    _pending[chat_id] = asyncio.create_task(_delayed_status(chat_id, bot))


async def _delayed_status(chat_id: int, bot) -> None:
    try:
        await asyncio.sleep(STATUS_DEBOUNCE)
        await refresh_status(chat_id, bot)
    except asyncio.CancelledError:
        pass
    except Exception:  # never let a background task die silently
        log.exception("status refresh failed")
    finally:
        _pending.pop(chat_id, None)


def cancel_pending(chat_id: int) -> None:
    task = _pending.pop(chat_id, None)
    if task:
        task.cancel()


async def clear_status(chat_id: int, bot, batch) -> None:
    if batch.status_message_id:
        try:
            await bot.delete_message(chat_id, batch.status_message_id)
        except TelegramError:
            pass
        batch.status_message_id = None


# ------------------------------------------------------------- collecting


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        await deny(update)
        return

    message = update.effective_message
    chat_id = update.effective_chat.id
    batch = store.get_or_open(chat_id)

    if batch.count >= MAX_ITEMS:
        schedule_status(chat_id, context.bot)
        return

    item, had_media = item_from_message(message)
    if had_media:
        batch.skipped_media += 1
    if item is not None:
        batch.add(item)
    elif not had_media:
        return  # nothing usable, and nothing to report

    store.save()
    schedule_status(chat_id, context.bot)


# ---------------------------------------------------------------- output


def shape_for(batch, view: str):
    """The grouping each view needs. Timeline wants blocks, the rest want people."""
    if view == render.VIEW_TIMELINE:
        return timeline_blocks(batch.items)
    return group_items(batch.items)


async def deliver_file(chat_id: int, bot, batch, view: str, *, query=None) -> None:
    """Export the current view as a Markdown note."""
    if view not in render.VIEW_NAMES:
        view = render.VIEW_TIMELINE
    when = int(time.time())
    groups = shape_for(batch, view)
    span = render.Span(groups)

    content = render.render_markdown(
        groups, view=view, total=batch.count, skipped=batch.skipped_media, when_ts=when
    )
    buffer = BytesIO(content.encode("utf-8"))
    await bot.send_document(
        chat_id,
        document=buffer,
        filename=render.markdown_filename(groups, when, view),
        caption=f"{render.VIEW_NAMES[view]} · "
        f"{render.plural(batch.count, 'message')} · {span.label()}",
    )
    if query is not None:
        await query.answer(f"{render.VIEW_NAMES[view]} sent as a file.")


async def deliver(chat_id: int, bot, batch, kind: str, *, query=None) -> None:
    """Send or swap the merged result in the requested view."""
    if kind not in render.VIEW_NAMES:
        kind = render.VIEW_TIMELINE
    groups = shape_for(batch, kind)

    if kind == render.VIEW_STITCHED:
        text = render.render_stitched(groups, total=batch.count, skipped=batch.skipped_media)
    elif kind == render.VIEW_PERSON:
        text = render.render_digest(groups, total=batch.count, skipped=batch.skipped_media)
    else:
        text = render.render_timeline(groups, total=batch.count, skipped=batch.skipped_media)

    if len(text) > TELEGRAM_TEXT_LIMIT:
        await bot.send_message(
            chat_id,
            "This digest is past Telegram's message limit, so here it is as a file.",
        )
        await deliver_file(chat_id, bot, batch, kind, query=query)
        return

    keyboard = result_keyboard(batch.batch_id, kind)
    if query is not None:
        try:
            await query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
            await query.answer()
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise
            await query.answer("Already showing that.")
        return

    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def finish(chat_id: int, bot) -> bool:
    """Close the open batch and send the digest. False if there was nothing."""
    cancel_pending(chat_id)
    batch = store.get_open(chat_id)
    if batch is None or batch.count == 0:
        if batch is not None:
            await clear_status(chat_id, bot, batch)
            store.discard(chat_id)
            store.save()
        return False

    await clear_status(chat_id, bot, batch)
    store.close(chat_id)
    store.save()
    await deliver(chat_id, bot, batch, render.VIEW_TIMELINE)
    return True


# -------------------------------------------------------------- commands


async def guard(update: Update) -> bool:
    """Access plus chat type. False means the handler should stop."""
    if not allowed(update):
        await deny(update)
        return False
    if not is_private(update):
        await update.effective_message.reply_text(GROUP_REPLY)
        return False
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.effective_message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.effective_message.reply_text(PRIVACY, parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.effective_message.reply_text(f"Your Telegram id is {update.effective_user.id}.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    ok = await finish(update.effective_chat.id, context.bot)
    if not ok:
        await update.effective_message.reply_text("Nothing collected yet. Forward some messages.")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    cancel_pending(chat_id)
    batch = store.get_open(chat_id)
    if batch is not None:
        await clear_status(chat_id, context.bot, batch)
        store.discard(chat_id)
        store.save()
    await update.effective_message.reply_text("Cleared. Forward messages to start a new batch.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    chat_id = update.effective_chat.id
    batch = store.get_open(chat_id)
    if batch is None or batch.count == 0:
        await update.effective_message.reply_text("The batch is empty.")
        return
    cancel_pending(chat_id)
    await refresh_status(chat_id, context.bot)


# -------------------------------------------------------------- callbacks


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not allowed(update):
        await deny(update)
        return

    data = query.data or ""
    chat_id = update.effective_chat.id

    if data.startswith("done:"):
        ok = await finish(chat_id, context.bot)
        if not ok:
            await query.answer("Nothing collected.")
        else:
            await query.answer()
        return

    if data.startswith("cancel:"):
        cancel_pending(chat_id)
        batch = store.get_open(chat_id)
        if batch is not None:
            await clear_status(chat_id, context.bot, batch)
            store.discard(chat_id)
            store.save()
        await query.answer("Batch dropped.")
        return

    if data.startswith(("fmt:", "file:")):
        parts = data.split(":")
        if len(parts) != 3:
            await query.answer()
            return
        action, batch_id, view = parts
        batch = store.get_finished(batch_id)
        if batch is None:
            await query.answer("That batch is gone. Forward again.", show_alert=True)
            return
        # Digests sent before the three-view export carry "fmt:<id>:file".
        # Those keyboards live in the chat forever, so keep honouring them
        # instead of silently swapping the view under the person tapping.
        if action == "fmt" and view == "file":
            action, view = "file", render.VIEW_TIMELINE
        if action == "file":
            await deliver_file(chat_id, context.bot, batch, view, query=query)
        else:
            await deliver(chat_id, context.bot, batch, view, query=query)
        return

    await query.answer()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message and allowed(update):
        try:
            await update.effective_message.reply_text(
                "Something broke on my side. The batch is still here, try Done again."
            )
        except TelegramError:
            pass


# ------------------------------------------------------------------ main


async def purge_loop(app: Application) -> None:
    """Delete expired batches on a timer for as long as the bot is up."""
    while True:
        try:
            await asyncio.sleep(PURGE_EVERY_SECONDS)
            removed = store.purge()
            if removed:
                store.save()
                log.info("purged %s expired batch(es)", removed)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("purge failed")


async def on_startup(app: Application) -> None:
    app.bot_data["purge_task"] = asyncio.create_task(purge_loop(app))


async def on_shutdown(app: Application) -> None:
    task = app.bot_data.get("purge_task")
    if task:
        task.cancel()
    store.save()


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not ALLOWED_USER_IDS:
        log.warning("ALLOWED_USER_IDS is empty: the bot will answer nobody")

    builder = Application.builder().token(BOT_TOKEN)
    try:
        from telegram.ext import AIORateLimiter

        builder = builder.rate_limiter(AIORateLimiter())
    except (ImportError, RuntimeError):
        # Optional extra. Without it the bot still works, it just has no
        # cushion against Telegram's flood limits when many people use it.
        log.warning("rate limiter not installed, running without it")

    app = builder.post_init(on_startup).post_shutdown(on_shutdown).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler(["new", "cancel"], cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.StatusUpdate.ALL, on_message)
    )
    app.add_error_handler(on_error)

    log.info(
        "merge bot up, %d allowed user(s), retention=%sh",
        len(ALLOWED_USER_IDS),
        int(RETENTION_HOURS),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
