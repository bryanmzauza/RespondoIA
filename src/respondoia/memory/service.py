"""Chat history for the session and the background thread. No Qt here.

With the history off, for groups, for contacts turned off, or while the contact cannot be
told apart from another with the same name, nothing is stored and nothing is attached to a
request. Without a database every method returns a neutral value. Database errors are
logged by type only.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from respondoia import contacts
from respondoia.config import Settings
from respondoia.context import MEMORY_LIMIT
from respondoia.database import Database
from respondoia.domain import ChatLine
from respondoia.llm import LlmError, ModelClient
from respondoia.memory import store
from respondoia.memory.identity import Candidate, identify
from respondoia.memory.merge import find_overlap, fingerprint, from_lines, merge
from respondoia.memory.recall import (
    MEMORY_SYSTEM,
    ContactMemory,
    Snippet,
    memory_blocks,
    memory_text,
    pick_snippets,
    plain_text,
    search_terms,
)
from respondoia.privacy import mask_text

logger = logging.getLogger("respondoia")

MEMORY_FIRST = 20  # messages before the first memory
MEMORY_NEW = 30  # new messages before an update
MEMORY_EVERY = timedelta(hours=24)


@dataclass(frozen=True)
class HistoryContext:
    state: str  # "off", "group", "out_of_scope", "ask", "ready" or "unavailable"
    contact_id: int | None = None
    owner: str = ""  # the title as stored
    owner_since: str = ""  # first day seen, when other contacts share the name
    messages: int = 0
    last_day: str = ""
    gap_since: str = ""
    memory: str = ""  # masked; "" unless ready
    snippets: tuple[Snippet, ...] = ()  # () unless ready
    candidates: tuple[tuple[int, str], ...] = ()  # (contact_id, first day) when asking
    added: int = 0


OFF = HistoryContext("off")
UNAVAILABLE = HistoryContext("unavailable")


def _pending(lines: Sequence[ChatLine]) -> list[str]:
    last_user = max((i for i, line in enumerate(lines) if line.author == "user"), default=-1)
    return [line.text for line in lines[last_user + 1 :] if line.author != "user"]


class HistoryService:
    def __init__(
        self,
        db: Database | None,
        client: Callable[[], ModelClient | None],
        settings: Callable[[], Settings],
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db = db
        self._client = client
        self._settings = settings
        self._now = now

    # The hotkey path.

    def observe(
        self, title: str, lines: Sequence[ChatLine], is_group: bool, avatar: str
    ) -> HistoryContext:
        context = self._safely(
            "observe", lambda conn: self._observe(conn, title, lines, is_group, avatar), UNAVAILABLE
        )
        logger.info(
            "history observe state=%s added=%d gap=%d snippets=%d memory=%d",
            context.state,
            context.added,
            bool(context.gap_since),
            len(context.snippets),
            bool(context.memory),
        )
        return context

    def confirm(
        self, title: str, lines: Sequence[ChatLine], contact_id: int | None
    ) -> HistoryContext:
        """The user's answer to "same person?". None means another person."""

        def run(conn):
            if contact_id is None:
                is_group = any(line.sender for line in lines)
                return contacts.add_namesake(conn, title, is_group, self._stamp()).id
            return contact_id

        def answer(conn):
            chosen = run(conn)
            # The answer holds for this screen only: it is stored under the chosen contact,
            # so the next press continues it, and no other chat inherits the answer.
            is_group = any(line.sender for line in lines)
            return self._observe(conn, title, lines, is_group, "", chosen=chosen)

        return self._safely("confirm", answer, UNAVAILABLE)

    def store_loaded(
        self, title: str, lines: Sequence[ChatLine], reached_start: bool, avatar: str
    ) -> HistoryContext:
        """What the loader read. Same rules as a hotkey press."""

        def run(conn):
            context = self._observe(conn, title, lines, any(line.sender for line in lines), avatar)
            if context.state == "ready":
                store.mark_loaded(conn, context.contact_id, reached_start, self._stamp())
            return context

        return self._safely("loaded", run, UNAVAILABLE)

    def _observe(self, conn, title, lines, is_group, avatar, chosen=None) -> HistoryContext:
        settings = self._settings()
        found = contacts.candidates(conn, title)
        if not settings.history_enabled:
            if len(found) == 1:
                contacts.mark_used(conn, found[0].id, self._stamp())
            return OFF
        if (is_group or any(c.is_group for c in found)) and not settings.history_include_groups:
            return HistoryContext("group")
        screen = from_lines(lines)
        candidates = []
        for c in found:
            memory = store.load_memory(conn, c.id)
            candidates.append(
                Candidate(
                    c.id,
                    c.first_seen[:10],
                    c.avatar,
                    store.recent(conn, c.id),
                    bool(memory and memory.text),
                    c.id == chosen,
                )
            )
        identity = identify(candidates, screen, avatar)
        if identity.reason == "new":
            created = contacts.see(conn, title, is_group, None, self._stamp())
            if created is None:
                return UNAVAILABLE
            found = [created]
            contact = created
        elif identity.contact_id is None:
            asked = tuple((c.contact_id, c.first_seen) for c in candidates)
            return HistoryContext("ask", owner=title, candidates=asked)
        else:
            contact = next(c for c in found if c.id == identity.contact_id)
        if contact.history == 0:
            return HistoryContext("out_of_scope", contact.id, contact.name)
        stamp = self._stamp()
        contacts.mark_used(conn, contact.id, stamp)
        if avatar:
            contacts.set_avatar(conn, contact.id, avatar)
        current = store.segments(conn, contact.id)
        result = merge([s.messages for s in current], screen)
        added = store.save_merge(conn, contact.id, result, stamp, current)
        status = store.status(conn, contact.id)
        snippets = self._snippets(conn, contact.id, lines, screen)
        memory = store.load_memory(conn, contact.id)
        return HistoryContext(
            "ready",
            contact.id,
            contact.name,
            contact.first_seen[:10] if len(found) > 1 else "",
            status.messages,
            status.last_day,
            status.gap_since,
            mask_text(memory.text).text if memory else "",
            snippets,
            (),
            added,
        )

    def _snippets(self, conn, contact_id, lines, screen) -> tuple[Snippet, ...]:
        limit = self._settings().snippets_per_suggestion
        visible = {fingerprint(m) for m in screen}
        shown = {plain_text(m.text) for m in screen}
        terms = search_terms([mask_text(text).text for text in _pending(lines)])
        groups, found = [], set()
        # More candidates than needed: what is on screen and repeated small talk are skipped.
        for hit in store.search(conn, contact_id, terms, visible, limit * 5):
            if len(groups) >= limit:
                break
            text = plain_text(store.get(conn, hit).text)
            if text in shown or text in found:
                continue
            found.add(text)
            group = [m for m in store.around(conn, hit) if fingerprint(m) not in visible]
            groups.append(
                tuple(
                    replace(m, text=mask_text(m.text).text, quote=mask_text(m.quote).text)
                    for m in group
                )
            )
        return pick_snippets(groups)

    def reaches_older(self, contact_id: int | None, lines: Sequence[ChatLine]) -> bool:
        """True once what the loader read overlaps a stored segment before the newest one:
        the gap is filled."""
        if contact_id is None:
            return False
        block = from_lines(lines)

        def run(conn):
            older = store.segments(conn, contact_id)[:-1]
            return any(find_overlap(segment.messages, block) for segment in older)

        return self._safely("reaches_older", run, False)

    # Settings, deletion and retention.

    def set_contact_history(self, title: str, contact_id: int | None, on: bool) -> None:
        """Turning a contact off also deletes what is stored for it."""

        def run(conn):
            ids = self._ids(conn, title, contact_id)
            for cid in ids:
                contacts.set_history(conn, cid, 1 if on else 0)
            if not on:
                store.forget_contact(conn, ids)

        self._safely("contact_history", run, None)
        if not on:
            self._checkpoint()

    def forget_contact(self, title: str, contact_id: int | None) -> int:
        """The contact told apart, or every contact with this title when it was not."""
        deleted = self._safely(
            "forget", lambda conn: store.forget_contact(conn, self._ids(conn, title, contact_id)), 0
        )
        self._checkpoint()
        return deleted

    def forget_all(self) -> None:
        self._safely("forget_all", store.forget_all, None)
        self._checkpoint()

    def apply_retention(self) -> int:
        now = self._now()
        months = now.year * 12 + now.month - 1 - self._settings().retention_months
        cutoff = f"{months // 12:04d}-{months % 12 + 1:02d}-{min(now.day, 28):02d}"
        deleted = self._safely("retention", lambda conn: store.apply_retention(conn, cutoff), 0)
        if deleted:
            self._checkpoint()
        return deleted

    # The contact memory.

    def memory(self, contact_id: int) -> str:
        row = self._safely("memory", lambda conn: store.load_memory(conn, contact_id), None)
        return row.text if row else ""

    def save_memory(self, contact_id: int, text: str) -> None:
        def run(conn):
            row = store.load_memory(conn, contact_id)
            through = row.through_id if row else 0
            store.save_memory(conn, contact_id, text.strip(), through, self._stamp(), edited=True)

        self._safely("save_memory", run, None)

    def update_memory_if_due(self, contact_id: int) -> bool:
        """Summarize the new messages. Costs one call per block of messages."""
        client = self._client()
        if client is None:
            return False
        now = self._now()

        def due(conn):
            row = store.load_memory(conn, contact_id)
            if (
                row
                and row.attempted_at
                and now - datetime.fromisoformat(row.attempted_at) < (MEMORY_EVERY)
            ):
                return None
            rows = store.rows_after(conn, contact_id, row.through_id if row else 0, 10**9)
            needed = MEMORY_NEW if row and row.text else MEMORY_FIRST
            if len(rows) < needed:
                return None
            store.mark_memory_attempt(conn, contact_id, self._stamp())
            return (row.text if row else "", bool(row and row.edited), rows)

        work = self._safely("memory_due", due, None)
        if work is None:
            return False
        text, edited, rows = work
        try:
            # The calls run outside any transaction: nothing waits on the database meanwhile.
            tokens = 0
            blocks = memory_blocks([message for _, message in rows])
            for block in blocks:
                result = client.extract(
                    MEMORY_SYSTEM, memory_text(text, edited, block), ContactMemory
                )
                text = mask_text(result.value.text.strip()).text[:MEMORY_LIMIT]
                tokens += result.input_tokens
        except LlmError as exc:
            logger.info("memory failed code=%s", exc.code)
            return False
        except Exception as exc:  # noqa: BLE001 - background work never crashes the app
            logger.info("memory failed type=%s", type(exc).__name__)
            return False
        through = max(row_id for row_id, _ in rows)

        def save(conn):
            # A contact deleted meanwhile has no row left to attach the memory to.
            if _contact_exists(conn, contact_id):
                store.save_memory(conn, contact_id, text, through, self._stamp(), edited)
                return True
            return False

        saved = self._safely("memory_save", save, False)
        logger.info("memory ok blocks=%d tokens_in=%d saved=%d", len(blocks), tokens, saved)
        return saved

    # Helpers.

    def _ids(self, conn, title: str, contact_id: int | None) -> list[int]:
        if contact_id is not None:
            return [contact_id]
        return [c.id for c in contacts.candidates(conn, title)]

    def _checkpoint(self) -> None:
        if self._db is None:
            return
        try:
            self._db.checkpoint()
        except Exception as exc:  # noqa: BLE001
            logger.info("history failed event=checkpoint type=%s", type(exc).__name__)

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _safely(self, event: str, run, default):
        if self._db is None:
            return default
        try:
            with self._db.transaction() as conn:
                return run(conn)
        except Exception as exc:  # noqa: BLE001 - history must never stop a suggestion
            logger.info("history failed event=%s type=%s", event, type(exc).__name__)
            return default


def _contact_exists(conn, contact_id: int) -> bool:
    return conn.execute("SELECT 1 FROM contact WHERE id = ?", (contact_id,)).fetchone() is not None
