import { FormEvent, useEffect, useState } from "react";
import { api, ApiRequestError, money } from "./api";
import type {
  CardDetails,
  CardPurchase,
  CardSummary,
  CardType,
} from "./types";

export const CARD_CREDENTIAL_TTL_MS = 30_000;

export const maskedCardNumber = (last4: string) => `•••• •••• •••• ${last4}`;

export const formatCardNumber = (number: string) =>
  number.replace(/\D/g, "").replace(/(.{4})/g, "$1 ").trim();

const cardTypeLabel = (type: CardType) => (type === "DEBIT" ? "Débito" : "Crédito");

function friendlyError(error: unknown): string {
  if (error instanceof ApiRequestError && error.status === 401) {
    return "Senha incorreta ou sessão expirada. Entre novamente se necessário.";
  }
  return error instanceof Error ? error.message : "Não foi possível concluir a operação.";
}

export function CardsView() {
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [purchases, setPurchases] = useState<CardPurchase[]>([]);
  const [details, setDetails] = useState<CardDetails | null>(null);
  const [revealingCardId, setRevealingCardId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [hasError, setHasError] = useState(false);

  async function refresh() {
    try {
      const [nextCards, nextPurchases] = await Promise.all([
        api.cards(),
        api.cardPurchases(),
      ]);
      setCards(nextCards);
      setPurchases(nextPurchases);
      setHasError(false);
    } catch (error) {
      setHasError(true);
      setNotice(friendlyError(error));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (!details) return;
    const hideCredentials = () => {
      setDetails(null);
      setNotice("Dados sensíveis ocultados automaticamente.");
      setHasError(false);
    };
    const timeout = window.setTimeout(hideCredentials, CARD_CREDENTIAL_TTL_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") hideCredentials();
    };
    window.addEventListener("pagehide", hideCredentials);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener("pagehide", hideCredentials);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [details]);

  async function issueCard(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setDetails(null);
    setSubmitting(true);
    setNotice("");
    try {
      const issued = await api.issueCard(
        String(data.get("type")) as CardType,
        String(data.get("password")),
      );
      if (document.visibilityState !== "hidden") setDetails(issued);
      setNotice("Cartão virtual emitido. Os dados serão ocultados em 30 segundos.");
      setHasError(false);
      await refresh();
    } catch (error) {
      setHasError(true);
      setNotice(friendlyError(error));
    } finally {
      form.reset();
      setSubmitting(false);
    }
  }

  async function revealCard(event: FormEvent<HTMLFormElement>, cardId: string) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setDetails(null);
    setSubmitting(true);
    setNotice("");
    try {
      const revealed = await api.revealCard(cardId, String(data.get("password")));
      if (document.visibilityState !== "hidden") setDetails(revealed);
      setRevealingCardId(null);
      setNotice("Dados revelados por 30 segundos.");
      setHasError(false);
    } catch (error) {
      setHasError(true);
      setNotice(friendlyError(error));
    } finally {
      form.reset();
      setSubmitting(false);
    }
  }

  const issuedTypes = new Set(cards.map((card) => card.type));
  const canIssue = !issuedTypes.has("DEBIT") || !issuedTypes.has("CREDIT");

  return (
    <section className="cards-page">
      <div className="lab-warning" role="note">
        <strong>Cartões exclusivos do laboratório.</strong> Nunca use estes dados fora da
        Moura Store e nunca informe um cartão real nesta simulação.
      </div>

      <div className="title cards-title">
        <div>
          <p className="eyebrow">CARTEIRA VIRTUAL</p>
          <h2>Seus cartões</h2>
        </div>
        <button
          className="refresh"
          onClick={() => {
            setDetails(null);
            setLoading(true);
            void refresh();
          }}
        >
          Atualizar compras
        </button>
      </div>

      {notice && (
        <div className={hasError ? "notice notice-error" : "notice"} role="status">
          {notice}
        </div>
      )}

      {loading ? (
        <p className="loading">Carregando cartões…</p>
      ) : (
        <div className="virtual-card-grid">
          {cards.map((card) => (
            <article className={`virtual-card ${card.type.toLowerCase()}-card`} key={card.id}>
              <div className="virtual-card-top">
                <span>Moura Banking</span>
                <em>{card.formFactor === "VIRTUAL" ? "Virtual" : "Físico"}</em>
              </div>
              <strong className="masked-number">{maskedCardNumber(card.last4)}</strong>
              <div className="virtual-card-meta">
                <span>
                  <small>Modalidade</small>
                  {cardTypeLabel(card.type)}
                </span>
                <span>
                  <small>Validade</small>
                  {String(card.expiryMonth).padStart(2, "0")}/{card.expiryYear}
                </span>
                <span>
                  <small>Status</small>
                  {card.status === "ACTIVE" ? "Ativo" : card.status}
                </span>
              </div>

              {card.type === "CREDIT" ? (
                <div className="credit-usage">
                  <div>
                    <span>Limite disponível</span>
                    <strong>{money(card.availableAmount)}</strong>
                  </div>
                  <progress
                    max={card.creditLimit ?? 0}
                    value={card.usedAmount ?? 0}
                    aria-label="Uso do limite de crédito"
                  />
                  <small>
                    {money(card.usedAmount ?? 0)} usados de {money(card.creditLimit ?? 0)}
                  </small>
                </div>
              ) : (
                <div className="credit-usage">
                  <span>Saldo disponível para débito</span>
                  <strong>{money(card.availableAmount)}</strong>
                </div>
              )}

              <button
                className="card-action"
                onClick={() => {
                  setDetails(null);
                  setNotice("");
                  setRevealingCardId(revealingCardId === card.id ? null : card.id);
                }}
              >
                {revealingCardId === card.id ? "Cancelar" : "Ver dados do cartão"}
              </button>

              {revealingCardId === card.id && (
                <form className="reveal-form" onSubmit={(event) => revealCard(event, card.id)}>
                  <label>
                    Confirme sua senha
                    <input
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      minLength={10}
                      maxLength={72}
                      required
                    />
                  </label>
                  <button disabled={submitting}>Revelar por 30 segundos</button>
                </form>
              )}
            </article>
          ))}
          {!cards.length && (
            <div className="card empty-card">
              <p>Nenhum cartão virtual emitido.</p>
            </div>
          )}
        </div>
      )}

      {details && (
        <section className="sensitive-card" aria-live="assertive">
          <div className="sensitive-heading">
            <div>
              <p className="eyebrow">DADOS TEMPORÁRIOS</p>
              <h2>{cardTypeLabel(details.type)} virtual</h2>
            </div>
            <button onClick={() => setDetails(null)}>Ocultar agora</button>
          </div>
          <p className="sensitive-number">{formatCardNumber(details.number)}</p>
          <div className="sensitive-fields">
            <span>
              <small>Titular</small>
              <strong>{details.holderName}</strong>
            </span>
            <span>
              <small>Validade</small>
              <strong>{String(details.expiryMonth).padStart(2, "0")}/{details.expiryYear}</strong>
            </span>
            <span>
              <small>CVV</small>
              <strong>{details.cvv}</strong>
            </span>
          </div>
          <p className="sensitive-hint">
            Estes dados não são salvos no navegador e serão ocultados ao trocar de tela,
            minimizar a aba ou após 30 segundos.
          </p>
          <a href="/store/" target="_blank" rel="noreferrer" onClick={() => setDetails(null)}>
            Testar cartão na Moura Store
          </a>
        </section>
      )}

      {canIssue && (
        <form className="card form issue-card" onSubmit={issueCard}>
          <div>
            <p className="eyebrow">NOVO CARTÃO</p>
            <h2>Emitir cartão virtual</h2>
          </div>
          <label>
            Modalidade
            <select name="type" required defaultValue="">
              <option value="" disabled>Selecione</option>
              <option value="DEBIT" disabled={issuedTypes.has("DEBIT")}>Débito</option>
              <option value="CREDIT" disabled={issuedTypes.has("CREDIT")}>Crédito</option>
            </select>
          </label>
          <label>
            Confirme sua senha
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              minLength={10}
              maxLength={72}
              required
            />
          </label>
          <button disabled={submitting}>Emitir com segurança</button>
        </form>
      )}

      <div className="title purchase-title">
        <div>
          <p className="eyebrow">ATIVIDADE</p>
          <h2>Compras com cartão</h2>
        </div>
        <a href="/store/" target="_blank" rel="noreferrer" onClick={() => setDetails(null)}>
          Ir para a Moura Store
        </a>
      </div>
      <div className="card purchase-list">
        {purchases.map((purchase) => (
          <div className="purchase-row" key={purchase.paymentId}>
            <span>
              <b>{purchase.merchantName}</b>
              <small>{purchase.orderReference}</small>
            </span>
            <span>
              <b>{money(purchase.amount)}</b>
              <small>
                {cardTypeLabel(purchase.paymentType)}
                {purchase.installments > 1 ? ` · ${purchase.installments}x` : ""}
              </small>
            </span>
            <span>
              <em className={purchase.status.toLowerCase()}>{purchase.status}</em>
              {purchase.declineCode && <small>{purchase.declineCode}</small>}
            </span>
            <span>{new Date(purchase.createdAt).toLocaleString("pt-BR")}</span>
          </div>
        ))}
        {!purchases.length && <p className="empty">Nenhuma compra com cartão encontrada.</p>}
      </div>
    </section>
  );
}
