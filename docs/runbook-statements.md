# Runbook — aquisição mensal de dados

Passo a passo para alimentar o ofin. Preencher os `TODO` em sessão
colaborativa (screenshots bem-vindos — colar no chat e o agente
transcreve os passos aqui).

## Cadência

- **TODO**: definir dia do mês (sugestão: dia 05, extrato+fatura do mês
  anterior já fechados).
- Tempo estimado total: ~15 min quando os passos estiverem preenchidos.

---

## Itaú — extrato (PDF)

### Onde conseguir
- App/site Itaú → conta corrente → extrato mensal em PDF.

### Passos
1. TODO(screenshot): navegação exata no app até "extrato em PDF".
2. TODO: qual período selecionar (mês fechado anterior).
3. Salvar como `extrato-YYYY-MM.pdf`.

### O que anotar
- Nada manual — o parser extrai transações + saldos CDB.

---

## Itaú — fatura do cartão (PDF)

### Onde conseguir
- App/site Itaú → cartões → fatura fechada → exportar PDF.

### Passos
1. TODO(screenshot): navegação até a fatura fechada.
2. Salvar como `fatura-YYYY-MM.pdf`.

### O que anotar
- Nada manual.

---

## Binance — snapshot de saldo

### Onde conseguir
- App Binance → Carteira → Visão geral → valor estimado total.

### Passos
1. TODO(screenshot): onde ler o "valor estimado" em BRL (ou USD +
   conversão).
2. Registrar em `/savings` → "registrar snapshot": source=`binance`,
   valor R$ total. Opcional: uma linha por ativo relevante
   (asset=BTC, qtd, valor).

### O que anotar
- Valor total BRL da carteira no dia. Precisão de centavos é
  irrelevante — é foto de patrimônio, não contabilidade.

---

## BTG — snapshot de saldo

### Onde conseguir
- TODO: app BTG → onde fica o saldo total (conta + investimentos).

### Passos
1. TODO(screenshot).
2. Registrar em `/savings`: source=`btg`, valor R$ total.

### O que anotar
- Saldo total BRL. Se um dia quisermos nível-transação: BTG exporta
  extrato? TODO investigar.

---

## BTC (carteira fria)

### Onde conseguir
- TODO: endereço/xpub da carteira → saldo via explorador (ex.
  mempool.space) → cotação BRL do dia.

### Passos
1. TODO: link direto do explorador com o endereço.
2. Registrar em `/savings`: source=`btc`, asset=`BTC`, qtd em BTC,
   valor R$ convertido.

### O que anotar
- Quantidade BTC (muda raramente) + valor BRL do dia.

---

## Empréstimos com amigos

- Na hora que emprestar/receber: `/savings` → "registrar empréstimo".
- Se o empréstimo saiu como PIX já importado: abrir a transação em
  `/transactions` → drawer → "registrar como empréstimo" (pré-preenche).
- Pagamento parcial: botão "+ pagamento" na linha do empréstimo.

---

## Registro no ofin

1. PDFs → `/import` (um por vez; conferir warnings em `/documents`).
2. Snapshots + empréstimos → `/savings`.
3. Depois de importar: conferir o card "matéria escura" no dashboard —
   se subiu, triar em `/transactions` (chip "matéria escura", drawer,
   criar regra ou override).

## Verificação

- Dashboard: patrimônio total bate com a intuição?
- Matéria escura ≈ 0?
- Sankey do mês: alguma categoria estranha?
