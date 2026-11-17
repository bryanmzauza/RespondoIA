"""Learn the user's style and give each request its context. No Qt here.

Without a database (it could not be opened) the style is off: every method returns a
neutral value and suggestions keep working. Database errors are logged by type only.
"""

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from respondoia import contacts
from respondoia.database import Database
from respondoia.domain import LABELS, ChatLine, UserContext
from respondoia.knowledge import read_knowledge
from respondoia.llm import LlmError, ModelClient
from respondoia.style import store
from respondoia.style.language import chat_language
from respondoia.style.parser import ExportChat
from respondoia.style.profile import (
    EXTRACT_SYSTEM,
    LOCAL_MIN,
    SMALL_VOLUME,
    StyleProfile,
    StyleTraits,
    build_local,
    extract_text,
    render,
)
from respondoia.style.sampler import from_export, from_screen, sample

logger = logging.getLogger("respondoia")


@dataclass(frozen=True)
class ImportResult:
    added: int
    skipped_pasted: int
    language: str | None


@dataclass(frozen=True)
class LanguageView:
    language: str
    messages: int
    profile: StyleProfile | None
    manual: str
    small: bool  # messages < SMALL_VOLUME


@dataclass(frozen=True)
class StyleOverview:
    languages: tuple[LanguageView, ...]  # most messages first


class StyleService:
    def __init__(
        self,
        db: Database | None,
        client: Callable[[], ModelClient | None],
        knowledge_file: Path,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db = db
        self._client = client
        self._knowledge_file = knowledge_file
        self._now = now

    def learn(self, title: str, lines: Sequence[ChatLine], is_group: bool) -> str | None:
        """Keep the user's own lines. Returns the language of the chat, if known."""
        language = chat_language([line.text for line in lines if line.text])

        def run(conn):
            stamp = self._stamp()
            contact = contacts.see(conn, title, is_group, language, stamp)
            if contact is None:  # the name is ambiguous
                return language
            chat_lang = language or contact.language
            pasted = store.pasted_hashes(conn, self._now())
            own = from_screen(lines, f"contact:{contact.id}", chat_lang, pasted)
            added = store.add_samples(conn, own, contact.id, stamp)
            for lang in {m.language for m in own}:
                self._rebuild(conn, lang)
            logger.info("learn added=%d language=%s", added, chat_lang or "none")
            return chat_lang

        return self._safely("learn", run, language)

    def context(self, title: str, language: str | None, is_group: bool) -> UserContext:
        try:
            knowledge = read_knowledge(self._knowledge_file)
        except OSError as exc:
            logger.info("knowledge failed type=%s", type(exc).__name__)
            knowledge = ""
        neutral = UserContext(language, None, False, False, "", knowledge)

        def run(conn):
            contact = contacts.find(conn, title)
            label = contact.label if contact else None
            from_user = bool(contact) and contact.label_source == "user"
            ask = contact is not None and not is_group and label is None and not from_user
            lang = language or (contact.language if contact else None)
            loaded = store.load_profile(conn, lang) if lang else None
            style = render(loaded[0], label, loaded[1]) if loaded else ""
            return UserContext(lang, label, from_user, ask, style, knowledge)

        return self._safely("context", run, neutral)

    def guess_label(self, title: str, label: str) -> None:
        if label in LABELS:
            self._label(title, label, "model")

    def set_label(self, title: str, label: str | None) -> None:
        self._label(title, label if label in LABELS else None, "user")

    def remember_pasted(self, text: str) -> None:
        self._safely("pasted", lambda conn: store.remember_pasted(conn, text, self._now()), None)

    def forget(self, title: str) -> None:
        """Drop the contact and its messages, and take them out of the profiles too."""

        def run(conn):
            languages = set(contacts.sample_languages(conn, title))
            languages |= set(store.forget_import(conn, title))
            contacts.forget(conn, title)
            # What the model wrote may echo that chat, so it goes too until the next update.
            for language in languages:
                self._rebuild(conn, language, keep_traits=False)

        self._safely("forget", run, None)

    def import_export(self, chat: ExportChat, author: str, label: str | None) -> ImportResult:
        pasted = self._safely("import", lambda conn: store.pasted_hashes(conn, self._now()), set())
        # The file is read outside any transaction, so the hotkey never waits on it.
        everything = from_export(chat, author, label, set())
        kept = from_export(chat, author, label, pasted)
        own = store.newest(kept)
        skipped = len(everything) - len(kept)

        def run(conn):
            added = store.add_samples(conn, own, None, self._stamp())
            for lang in {m.language for m in own}:
                self._rebuild(conn, lang)
            counts = Counter(m.language for m in own).most_common(1)
            logger.info("import added=%d", added)
            return ImportResult(added, skipped, counts[0][0] if counts else None)

        return self._safely("import", run, ImportResult(0, 0, None))

    def refresh(self, language: str) -> StyleProfile | None:
        """Ask the model to describe the style. Costs one call. None without data."""
        client = self._client()
        if client is None:
            raise LlmError("auth")
        messages = self._safely("refresh", lambda conn: store.samples(conn, language), [])
        if not messages:
            return None
        # The call runs outside any transaction: nothing waits on the database meanwhile.
        result = client.extract(EXTRACT_SYSTEM, extract_text(sample(messages)), StyleTraits)

        def save(conn):
            current = store.samples(conn, language)
            profile = build_local(language, current, result.value, self._stamp())
            store.save_profile(conn, profile, extracted=True)
            return profile

        profile = self._safely("refresh", save, None)
        logger.info(
            "refresh ok ms=%d tokens_in=%d tokens_out=%d",
            result.latency_ms,
            result.input_tokens,
            result.output_tokens,
        )
        return profile

    def refresh_if_due(self, language: str) -> bool:
        """At most one automatic extraction a day, after enough new messages."""
        due = self._safely(
            "due", lambda conn: store.extraction_due(conn, language, self._now()), False
        )
        if not due:
            return False
        self._safely("attempt", lambda conn: store.mark_attempt(conn, language, self._now()), None)
        try:
            return self.refresh(language) is not None
        except LlmError as exc:
            logger.info("refresh failed code=%s", exc.code)
            return False

    def overview(self) -> StyleOverview:
        def run(conn):
            views = []
            for language, count in store.languages(conn):
                loaded = store.load_profile(conn, language)
                profile, manual = loaded if loaded else (None, "")
                views.append(LanguageView(language, count, profile, manual, count < SMALL_VOLUME))
            return StyleOverview(tuple(views))

        return self._safely("overview", run, StyleOverview(()))

    def save_manual(self, language: str, text: str) -> None:
        self._safely("manual", lambda conn: store.save_manual(conn, language, text), None)

    def exclude_example(self, language: str, text: str) -> None:
        def run(conn):
            store.exclude_example(conn, language, text)
            self._rebuild(conn, language)

        self._safely("exclude", run, None)

    def reset(self) -> None:
        self._safely("reset", store.reset, None)

    def _label(self, title: str, label: str | None, source: str) -> None:
        def run(conn):
            contact = contacts.find(conn, title)
            if contact is not None:
                contacts.set_label(conn, contact.id, label, source)

        self._safely("label", run, None)

    def _rebuild(self, conn, language: str, keep_traits: bool = True) -> None:
        """Recount the local part of the profile, keeping what the model wrote."""
        messages = store.samples(conn, language)
        loaded = store.load_profile(conn, language)
        # An existing profile is always recounted, so removed messages leave its examples.
        if len(messages) < LOCAL_MIN and loaded is None:
            return
        traits = loaded[0].traits if loaded and keep_traits else None
        store.save_profile(conn, build_local(language, messages, traits, self._stamp()), False)

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    def _safely(self, event: str, run, default):
        if self._db is None:
            return default
        try:
            with self._db.transaction() as conn:
                return run(conn)
        except Exception as exc:  # noqa: BLE001 - style must never stop a suggestion
            # Only the type: a message can quote stored text.
            logger.info("style failed event=%s type=%s", event, type(exc).__name__)
            return default
