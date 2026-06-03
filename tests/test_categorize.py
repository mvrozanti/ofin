from ofin.parsers.categorize import classify_extrato, classify_fatura


def test_classify_sweep_resgate():
    cat, sweep, interest, internal = classify_extrato("RESGATE APLIC AUT MAIS")
    assert cat == "sweep_resgate"
    assert sweep is True
    assert internal is True


def test_classify_sweep_aplicacao():
    cat, sweep, interest, internal = classify_extrato("APL APLIC AUT MAIS")
    assert cat == "sweep_aplicacao"
    assert sweep is True
    assert internal is True


def test_classify_rendimento_cdb_marks_interest_not_internal():
    cat, _, interest, internal = classify_extrato("REND PAGO APLIC AUT MAIS")
    assert cat == "rendimento_cdb"
    assert interest is True
    assert internal is False


def test_classify_salario():
    cat, _, _, internal = classify_extrato("REMUNERACAO/SALARIO")
    assert cat == "salario"
    assert internal is False


def test_classify_estorno_marked_internal():
    cat, _, _, internal = classify_extrato("EST PIX RECEBIDO")
    assert cat == "estorno"
    assert internal is True


def test_classify_pagamento_cartao_internal():
    cat, _, _, internal = classify_extrato("PAG FATURA ITAU PLATINU")
    assert cat == "pagamento_cartao"
    assert internal is True


def test_classify_ted_not_internal_when_other_party():
    cat, _, _, internal = classify_extrato("TED 033.4635.OUTRA PESSOA")
    assert cat == "transferencia"
    assert internal is False


def test_classify_ted_self_marked_internal():
    cat, _, _, internal = classify_extrato("TED 033 MARCELO ROZANTI")
    assert cat == "transferencia_propria"
    assert internal is True


def test_classify_ifood_extrato():
    cat, _, _, _ = classify_extrato("ON IFD COMIDA")
    assert cat == "restaurante"


def test_classify_pix_generic():
    cat, _, _, internal = classify_extrato("PIX RECEBIDO 12345678")
    assert cat == "pix"
    assert internal is False


def test_classify_boleto_pag_titulo():
    cat, _, _, _ = classify_extrato("PAG TIT BANCO 1234")
    assert cat == "boleto"


def test_classify_fatura_international_flag():
    cat = classify_fatura("RANDOM SHOP", is_international=True)
    assert cat in {"compra_online", "internacional_outros", "outros"}


def test_classify_fatura_empty_returns_outros():
    assert classify_fatura("") == "outros"
    assert classify_fatura(None) == "outros"


def test_classify_fatura_does_not_raise_on_none():
    classify_fatura(None, category_hint=None, is_international=False)


def test_classify_fatura_hint_passthrough():
    cat = classify_fatura("UNKNOWN MERCHANT", category_hint="restaurante")
    assert cat in {"restaurante", "outros"}
