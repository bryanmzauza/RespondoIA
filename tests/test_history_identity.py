from respondoia.memory.identity import Candidate, identify
from respondoia.memory.merge import Stored


def run(prefix: str, n: int = 6, start: int = 0) -> tuple[Stored, ...]:
    return tuple(
        Stored("", f"10:{start + i:02d}", "contact", "", "text", f"{prefix} {start + i}", "")
        for i in range(n)
    )


A = run("pedido do bolo")
B = run("reunião de pais")


def candidate(contact_id, recent=(), avatar="", memory=False, confirmed=False):
    return Candidate(contact_id, "2026-10-01", avatar, recent, memory, confirmed)


def test_no_candidate_is_new():
    assert identify([], run("x"), "").reason == "new"


def test_overlap_picks_the_right_namesake():
    screen = B[3:] + run("reunião de pais", 2, start=6)
    identity = identify([candidate(1, A), candidate(2, B)], screen, "")
    assert (identity.contact_id, identity.reason) == (2, "overlap")


def test_same_name_without_overlap_or_photo_is_ambiguous():
    identity = identify([candidate(1, A, memory=True)], run("outra conversa"), "")
    assert identity.reason == "ambiguous" and identity.contact_id is None
    assert identity.candidates == (1,)


def test_default_picture_is_never_evidence():
    # No picture on either side: the hash is empty and proves nothing.
    identity = identify([candidate(1, A, avatar="")], run("outra conversa"), "")
    assert identity.reason == "ambiguous"


def test_real_photo_after_a_gap():
    identity = identify([candidate(1, A, avatar="ab12")], run("outra conversa"), "ab12")
    assert (identity.contact_id, identity.reason) == (1, "photo")


def test_photo_must_be_unique_among_namesakes():
    pair = [candidate(1, A, avatar="ab12"), candidate(2, B, avatar="ab12")]
    assert identify(pair, run("outra conversa"), "ab12").reason == "ambiguous"


def test_other_photo_is_a_different_person():
    identity = identify([candidate(1, A, avatar="ab12")], run("outra conversa"), "cd34")
    assert identity.reason == "different" and identity.contact_id is None


def test_nothing_stored_means_nothing_to_leak():
    identity = identify([candidate(1)], run("outra conversa"), "")
    assert (identity.contact_id, identity.reason) == (1, "only")


def test_two_empty_namesakes_are_ambiguous():
    assert identify([candidate(1), candidate(2)], run("x"), "").reason == "ambiguous"


def test_user_confirmation_wins():
    pair = [candidate(1, A, memory=True), candidate(2, B, confirmed=True)]
    identity = identify(pair, run("outra conversa"), "")
    assert (identity.contact_id, identity.reason) == (2, "user")


def test_overlap_with_two_candidates_is_ambiguous():
    identity = identify([candidate(1, A), candidate(2, A)], A[2:], "")
    assert identity.reason == "ambiguous" and identity.candidates == (1, 2)


def test_shared_broadcast_messages_do_not_identify():
    broadcast = A[2:4]
    screen = run("outro assunto", 1, start=40) + broadcast + run("outro assunto", 1, start=41)
    identity = identify([candidate(1, A, memory=True)], screen, "")
    assert identity.reason == "ambiguous" and identity.contact_id is None


def test_user_broadcast_at_the_tail_does_not_identify():
    # The user sent the same two messages to two people named Ana; the first has not
    # replied. The second Ana's screen shows those two messages and her own reply.
    promo = tuple(
        Stored("", "18:00", "user", "", "text", f"promoção de natal {i}", "") for i in range(2)
    )
    stored = run("pedido do bolo", 4) + promo
    screen = promo + (Stored("", "18:05", "contact", "", "text", "quero saber mais", ""),)
    identity = identify([candidate(1, stored, memory=True)], screen, "")
    assert identity.reason == "ambiguous" and identity.contact_id is None


def test_one_stored_message_does_not_identify():
    single = (Stored("", "10:00", "contact", "", "text", "Oi", ""),)
    screen = single + (Stored("", "10:01", "contact", "", "text", "tudo bem?", ""),)
    identity = identify([candidate(1, single, memory=True)], screen, "")
    assert identity.reason == "ambiguous"


def test_memory_without_messages_still_needs_evidence():
    identity = identify([candidate(1, memory=True)], run("x"), "")
    assert identity.reason == "ambiguous"
