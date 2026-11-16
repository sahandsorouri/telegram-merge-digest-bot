# Telegram Merge Bot

Forward a pile of Telegram messages to this bot. Get back one digest, grouped by whoever originally wrote each message.

## The flow

1. Forward as many messages as you like. Different chats, different days, all fine.
2. After each burst the bot posts a short status line at the bottom with a **Done** button.
3. Keep forwarding. Nothing closes on a timer, the batch stays open until you tap Done.
4. Tap **Done**. You get the merged digest.

```
🧺 Collecting
7 messages · 3 people
Forward more, or tap Done.
[ ✅ Done ]  [ 🗑 Cancel ]
```

```
📋 Merged · 7 messages · 3 people
Thu 06 Aug, 14:22
━━━━━━━━━━━━━━━━━━━
📢 Design Weekly
09:30  Office closed on Sunday.
09:31  Payroll moves to the 25th.

👤 Ali Rezaei
14:02  Deck is sent, check the third slide.
14:03  We also need the Arabic version before Sunday.

👤 Sara Movahed
14:05  Received. The org chart page is still old.

👤 Ali Rezaei
14:11  Ignore the last one, Arabic can wait.
━━━━━━━━━━━━━━━━━━━
[ 👤 By person ]  [ 📝 Stitched ]  [ 📎 .md file ]
```

The status message is re-posted at the bottom after each burst, so the Done button is always the last thing in the chat and never gets buried.

## Output formats

| Format | What it is |
|---|---|
| **Time order** (default) | The conversation in the order it actually happened. A run of messages from one person collapses into a single block, and a person who speaks twice appears twice. |
| **By person** | Everything from one person together. Easier to see what one individual said, at the cost of losing the sequence. |
| **Stitched** | One continuous block per person, no timestamps. Good for pasting into a brief or another tool. |

Switch between them with the buttons under the result.

**📎 .md of this view** exports whichever of the three you are currently looking at, as a Markdown note with frontmatter already filled in, ready to drop into a notes app such as Obsidian. So there are three possible files, not one:

| View | File | Body |
|---|---|---|
| Time order | `2026-08-06 Telegram Merge.md` | `##` heading per day, `###` per speaker block |
| By person | `2026-08-06 Telegram Merge By Person.md` | `##` per person, every timestamp dated |
| Stitched | `2026-08-06 Telegram Merge Stitched.md` | `##` per person, one paragraph, no times |

The filenames differ on purpose, so exporting two views does not overwrite one note. The frontmatter carries `view:` so you can tell them apart later.

If a digest goes past Telegram's 4096 character limit it is sent as a file automatically, in the view you were on.

## Right to left text

Persian, Arabic, and Hebrew messages sit on the same line as a left-to-right timestamp. Left alone, the bidirectional algorithm takes the line's direction from the first strong character, which is a digit, and trailing punctuation ends up on the wrong visual side.

Every message and every author name that contains RTL characters is wrapped in `U+2068 FSI` and `U+2069 PDI`, so the run picks its own direction and cannot leak into the text around it. Pure English output is left untouched, no invisible characters in the file.

## Commands

| Command | Effect |
|---|---|
| `/done` | Same as the Done button |
| `/new` | Throw the current batch away and start clean |
| `/status` | Re-post the status line |
| `/privacy` | What the bot stores and for how long |
| `/id` | Show your Telegram user id |
| `/help` | Usage |

## Grouping rules

Authors come from Telegram's `forward_origin`, so the bot handles all four cases:

| Origin | Group label |
|---|---|
| Normal forward from a person | Their name |
| Sender hid their forward link | Their name, no user id |
| Forward from a channel | Channel title, plus the post signature if there is one |
| Posted on behalf of a group | Group title |
| No origin at all | `Unknown sender` |

That last row covers two real cases: text you typed into the bot yourself, and messages you forwarded with **hide sender name** ticked. Telegram strips the origin completely in both, so no bot can recover the author.

If the same person shows up once with a visible profile and once hidden, and the display name matches, the two groups are folded into one.

Messages keep their **original** send time, not the time you forwarded them. That is what makes time order work even when you forward things out of sequence.

## Dates

One rule decides every date in the output, so the header, the day lines and the timestamps can never contradict each other.

A batch inside a single day states that date once in the header and shows bare times:

```
📋 Merged · 4 messages · 2 people
Thu 06 Aug 2026
```

A batch that spans days states the range, then puts the day in front of the messages that belong to it:

```
📋 Merged · 7 messages · 3 people
Tue 04 Aug to Thu 06 Aug 2026  ·  2 days
━━━━━━━━━━━━━━━━━━━
📅 Tue 04 Aug
📢 Design Weekly
13:30  Office closed on Sunday.

📅 Thu 06 Aug
👤 Ali Rezaei
18:02  Deck is sent, check the third slide.
```

Details that follow from that rule:

- A bare time is never printed when the batch covers more than one day. The **By person** view has no day line to sit under, so every timestamp there carries its own date instead.
- A block never spans midnight. 23:59 and 00:01 are different days to a reader, so they become separate blocks even for the same person.
- The year appears whenever a batch crosses one, in both the range and the day lines.
- The `.md` file gets a `##` heading per day, plus `from`, `to` and `days` in the frontmatter.
- The filename comes from the message dates, not the merge date. Forwarding something from March today produces `2026-03-01 Telegram Merge.md`, not today's date.

## Limits

- Text and captions only. Photos, files, and voice notes are counted and reported, then skipped. The count always appears so nothing disappears quietly.
- Private chat only. In a group the bot just points you back to a direct message.
- 500 messages per batch, then the batch stops accepting new ones and says so.

## Privacy

The bot can hold other people's forwarded conversations, so nothing is kept longer than it has to be.

- While you are collecting, only the **text** of what you forwarded is stored. Attachments are counted and dropped on arrival, never saved.
- A delivered batch is deleted within `RETENTION_HOURS`, 24 by default. That is also when the format buttons under an old digest stop working.
- An abandoned batch expires after `STALE_BATCH_HOURS`, 7 days by default.
- `/new` deletes your current batch immediately.
- `/privacy` says all of this inside the bot.

Every person gets their own batch, keyed by chat id. Results are capped per person, not globally, so a heavy user can never push someone else's digest out.

## Access

The bot is default deny. Only Telegram user ids listed in `ALLOWED_USER_IDS` can use it. Anyone else gets no answer at all, and the attempt is logged. If the list is empty, the bot answers nobody and logs a warning at start.

To find your numeric user id, message a bot such as `userinfobot` on Telegram, or send the bot any message and read your id from its log, which records the id of every user it ignores.

## Setup

1. Get a token from [BotFather](https://t.me/BotFather).
2. Copy the example settings and fill them in:

```bash
cp .env.example .env
```

3. Run it locally:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python bot.py
```

Or with Docker:

```bash
docker compose up -d --build
```

`data/` is mounted as a volume, so a batch in progress survives a rebuild. Only one instance may poll a given bot token at a time; stop the local one before starting another, or Telegram hands updates to whichever asks first.

## Environment variables

| Name | Required | What it is |
|---|---|---|
| `BOT_TOKEN` | yes | Telegram bot token from BotFather |
| `ALLOWED_USER_IDS` | yes | Comma separated numeric Telegram user ids allowed to use the bot |
| `TZ` | no | IANA time zone for the digest, default `UTC` |
| `RETENTION_HOURS` | no | Hours a delivered batch is kept, default `24` |
| `STALE_BATCH_HOURS` | no | Hours an abandoned batch stays open, default `168` |
| `STATUS_DEBOUNCE` | no | Seconds of quiet before the status line is re-posted, default `1.2` |
| `MAX_ITEMS` | no | Messages per batch, default `500` |
| `KEEP_FINISHED` | no | Finished batches kept per person, default `5` |
| `PURGE_EVERY_SECONDS` | no | How often expired batches are deleted, default `3600` |
| `DATA_DIR` | no | Where the state file lives, default `data` |

## Tests

No token or network needed.

```bash
.venv/bin/python tests/test_merge.py
```

A passing run also prints one full sample batch in all three formats. The tests also run under `pytest`.

## Layout

```
bot.py          handlers, status message, delivery
store.py        batch state, JSON snapshot in data/
grouping.py     forward_origin to author, grouping, alias merge
render.py       digest / stitched / markdown
models.py       Item, Group, Batch
config.py       env and the access allowlist
```

## License

MIT, see `LICENSE`.

Built by Sahand Sorouri, https://sahand.sorouri.com
