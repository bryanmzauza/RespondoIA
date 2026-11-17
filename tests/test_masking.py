from respondoia.domain import ChatLine
from respondoia.privacy import mask_lines, mask_text


def test_valid_cpf_with_and_without_punctuation():
    assert mask_text("meu cpf é 529.982.247-25").text == "meu cpf é [CPF]"
    assert mask_text("52998224725").text == "[CPF]"


def test_valid_cnpj():
    assert mask_text("CNPJ 11.222.333/0001-81").text == "CNPJ [CNPJ]"
    assert mask_text("11222333000181").found == ("CNPJ",)


def test_card_needs_luhn():
    assert mask_text("4111 1111 1111 1111").text == "[CARD]"
    assert mask_text("4111-1111-1111-1111").text == "[CARD]"
    assert mask_text("4111 1111 1111 1112").text == "4111 1111 1111 1112"


def test_iban_email_pix_and_phone():
    assert mask_text("GB82 WEST 1234 5698 7654 32").found == ("IBAN",)
    assert mask_text("IBAN GB82 WEST 1234 5698 7654 32 OK").text == "IBAN [IBAN] OK"
    assert mask_text("ana@exemplo.com").text == "[EMAIL]"
    assert mask_text("chave 123e4567-e89b-42d3-a456-426614174000").text == "chave [PIX]"
    assert mask_text("me liga no (11) 91234-5678").text == "me liga no [PHONE]"
    assert mask_text("call +44 20 7946 0958").text == "call [PHONE]"


def test_password_keeps_the_word():
    assert mask_text("a senha é abc123!").text == "a senha é [PASSWORD]"
    assert mask_text("password: hunter2").text == "password: [PASSWORD]"
    assert mask_text("o código do pedido chegou").text == "o código do pedido chegou"


def test_common_numbers_are_kept():
    for text in (
        "R$ 180 por pessoa",
        "18h30",
        "dia 12/11/2026",
        "pedido 12345",
        "111.111.111-11",
        "529.982.247-24",
        "3 quartos e 2 carros",
        "chego 21:47",
    ):
        assert mask_text(text).text == text, text
        assert mask_text(text).found == ()


def test_found_follows_the_text_order():
    masked = mask_text("cartão 4111 1111 1111 1111, cpf 529.982.247-25, email a@b.co")
    assert masked.found == ("CARD", "CPF", "EMAIL")
    assert masked.text == "cartão [CARD], cpf [CPF], email [EMAIL]"


def test_sender_and_quote_are_masked_too():
    line = ChatLine("contact", "+55 11 91234-5678", "10:00", "text", "ok", "cpf 529.982.247-25")
    (masked,), found = mask_lines([line])
    assert masked.sender == "[PHONE]" and masked.quote == "cpf [CPF]" and masked.text == "ok"
    assert found == ("PHONE", "CPF")
    assert (masked.author, masked.time, masked.kind) == ("contact", "10:00", "text")


def test_card_next_to_other_numbers_is_still_masked():
    assert mask_text("4111 1111 1111 1111 12/28 cvv 123").text == "[CARD] 12/28 cvv 123"
    assert mask_text("4111 1111 1111 1111 123").text == "[CARD] 123"
    assert mask_text("4111111111111111 123").text == "[CARD] 123"
    assert mask_text("cartao 1 4111 1111 1111 1111").text == "cartao 1 [CARD]"


def test_password_after_e_and_colon():
    assert mask_text("a senha é: abc123").text == "a senha é: [PASSWORD]"


def test_phone_rules_keep_their_neighbours():
    assert mask_text("+55 11 91234-5678. 2 caixas").text == "[PHONE]. 2 caixas"
    assert mask_text("total 1234567890").text == "total 1234567890"
    assert mask_text("11 91234-5678").text == "[PHONE]"
    assert mask_text("11912345678").text == "[PHONE]"
