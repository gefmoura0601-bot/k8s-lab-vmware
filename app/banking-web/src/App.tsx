import { FormEvent, useEffect, useState } from "react";
import { api, ApiRequestError, money } from "./api";
import { CardsView } from "./CardsView";
import { cpfDigits, formatCpf, isValidCpf } from "./cpf";
import type { Account, DirectoryEntry, PixKey, Transaction } from "./types";
export function App() {
  const [account, setAccount] = useState<Account | null>(null),
    [directory, setDirectory] = useState<DirectoryEntry[]>([]),
    [transactions, setTransactions] = useState<Transaction[]>([]),
    [pixKey, setPixKey] = useState<PixKey | null>(null),
    [destinationMode, setDestinationMode] = useState<"account" | "pix">(
      "account",
    ),
    [mode, setMode] = useState<"login" | "register">("login"),
    [view, setView] = useState<"home" | "transfer" | "cards" | "history">("home"),
    [busy, setBusy] = useState(true),
    [message, setMessage] = useState("");
  async function load() {
    try {
      const me = await api.me(),
        dir = await api.directory();
      setAccount(me);
      setDirectory(dir.filter((x) => x.id !== me.id));
      setTransactions(await api.statement(me.id));
      setPixKey(await api.pixKey().catch(() => null));
    } catch {
      setAccount(null);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function authenticate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = e.currentTarget,
      d = new FormData(f);
    const cpf = mode === "register" ? cpfDigits(String(d.get("cpf"))) : "";
    if (mode === "register" && !isValidCpf(cpf)) {
      setMessage("Informe um CPF brasileiro válido.");
      return;
    }
    try {
      const a =
        mode === "login"
          ? await api.login(
              String(d.get("accountNumber")),
              String(d.get("password")),
            )
          : await api.register(
              String(d.get("ownerName")),
              cpf,
              String(d.get("password")),
            );
      setAccount(a);
      setMessage("");
      await load();
    } catch (x) {
      setMessage(
        mode === "register" && x instanceof ApiRequestError && x.status === 409
          ? "Não foi possível concluir o cadastro; revise os dados ou entre em contato com o suporte."
          : (x as Error).message,
      );
    } finally {
      const password = f.elements.namedItem("password");
      if (password instanceof HTMLInputElement) password.value = "";
      const cpfInput = f.elements.namedItem("cpf");
      if (cpfInput instanceof HTMLInputElement) cpfInput.value = "";
    }
  }
  async function transfer(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!account) return;
    const f = e.currentTarget,
      d = new FormData(f);
    try {
      await api.transfer(
        account.id,
        destinationMode === "pix"
          ? { pixKey: String(d.get("pixKey")) }
          : { destinationAccountId: String(d.get("destination")) },
        Number(d.get("amount")),
        String(d.get("description")),
        String(d.get("password")),
      );
      setMessage("Transferência concluída.");
      f.reset();
      await load();
      setView("history");
    } catch (x) {
      setMessage((x as Error).message);
    }
  }
  async function createPixKey() {
    try {
      setPixKey(await api.createPixKey());
      setMessage("Chave PIX criada com sucesso.");
    } catch (x) {
      setMessage((x as Error).message);
    }
  }
  if (busy) return <div className="loading">Abrindo Moura Banking…</div>;
  if (!account)
    return (
      <main className="auth">
        <section className="login-card">
          <div className="brand auth-brand">
            <span>M</span>
            <div>
              <strong>Moura</strong>
              <small>Banking</small>
            </div>
          </div>
          <p className="eyebrow">BANCO DIGITAL DO LAB</p>
          <h1>{mode === "login" ? "Acesse sua conta" : "Abra sua conta"}</h1>
          {message && <div className="notice">{message}</div>}
          <form className="form" onSubmit={authenticate}>
            {mode === "register" && (
              <>
                <label>
                  Titular
                  <input name="ownerName" maxLength={120} required />
                </label>
                <label>
                  CPF
                  <input
                    name="cpf"
                    inputMode="numeric"
                    autoComplete="off"
                    placeholder="000.000.000-00"
                    maxLength={14}
                    pattern="[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}"
                    onInput={(event) => {
                      event.currentTarget.value = formatCpf(event.currentTarget.value);
                      const digits = cpfDigits(event.currentTarget.value);
                      event.currentTarget.setCustomValidity(
                        digits.length === 11 && !isValidCpf(digits) ? "CPF inválido." : "",
                      );
                    }}
                    onBlur={(event) =>
                      event.currentTarget.setCustomValidity(
                        isValidCpf(event.currentTarget.value) ? "" : "CPF inválido.",
                      )
                    }
                    required
                  />
                  <small>Usado somente para identificar sua conta no laboratório.</small>
                </label>
              </>
            )}{" "}
            {mode === "login" && (
              <label>
                Número da conta
                <input
                  name="accountNumber"
                  inputMode="numeric"
                  pattern="[0-9]{8}"
                  required
                />
              </label>
            )}
            <label>
              Senha
              <input
                name="password"
                type="password"
                minLength={10}
                maxLength={72}
                required
              />
            </label>
            <button>
              {mode === "login" ? "Entrar com segurança" : "Criar conta"}
            </button>
          </form>
          <button
            className="switch"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setMessage("");
            }}
          >
            {mode === "login" ? "Ainda não tenho conta" : "Já tenho uma conta"}
          </button>
          <small className="security">
            Sessão protegida e expira após 15 minutos.
          </small>
        </section>
      </main>
    );
  const nav: [typeof view, string][] = [
    ["home", "Minha conta"],
    ["transfer", "Transferir"],
    ["cards", "Cartões"],
    ["history", "Histórico"],
  ];
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span>M</span>
          <div>
            <strong>Moura</strong>
            <small>Banking</small>
          </div>
        </div>
        <nav>
          {nav.map(([id, label]) => (
            <button
              key={id}
              className={view === id ? "active" : ""}
              onClick={() => setView(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        <button
          className="logout"
          onClick={async () => {
            await api.logout();
            setAccount(null);
          }}
        >
          Sair
        </button>
      </aside>
      <main>
        <header>
          <div>
            <p>CONTA DIGITAL</p>
            <h1>{nav.find((x) => x[0] === view)?.[1]}</h1>
          </div>
          <span>Conta {account.accountNumber}</span>
        </header>
        {message && <div className="notice">{message}</div>}
        {view === "home" && (
          <>
            <div className="hero">
              <div>
                <p>SALDO DISPONÍVEL</p>
                <strong>{money(account.balance)}</strong>
                <span>
                  {account.ownerName}
                  {account.cpfMasked ? ` · CPF ${account.cpfMasked}` : ""}
                </span>
              </div>
              <div className="monogram">MB</div>
            </div>
            <div className="stats">
              <article>
                <span>Número da conta</span>
                <strong>{account.accountNumber}</strong>
              </article>
              <article>
                <span>Transferências</span>
                <strong>{transactions.length}</strong>
              </article>
              <article>
                <span>Status</span>
                <strong>Ativa</strong>
              </article>
            </div>
            <section className="card pix-card">
              <div>
                <p className="eyebrow">MINHA CHAVE PIX</p>
                <h2>Receba transferências pelo identificador seguro</h2>
                {pixKey && <code>{pixKey.pixKey}</code>}
              </div>
              {pixKey ? (
                <button
                  onClick={async () => {
                    await navigator.clipboard.writeText(pixKey.pixKey);
                    setMessage("Chave PIX copiada.");
                  }}
                >
                  Copiar chave
                </button>
              ) : (
                <button onClick={createPixKey}>Criar chave PIX</button>
              )}
            </section>
          </>
        )}
        {view === "transfer" && (
          <section className="narrow">
            <form className="card form transfer" onSubmit={transfer}>
              <p className="eyebrow">PIX INTERNO</p>
              <h2>Nova transferência</h2>
              <label>
                Forma de envio
                <select
                  value={destinationMode}
                  onChange={(e) =>
                    setDestinationMode(e.target.value as "account" | "pix")
                  }
                >
                  <option value="account">Conta cadastrada</option>
                  <option value="pix">Chave PIX</option>
                </select>
              </label>
              {destinationMode === "account" ? (
                <label>
                  Destino
                  <select name="destination" required defaultValue="">
                    <option value="" disabled>
                      Selecione a conta
                    </option>
                    {directory.map((x) => (
                      <option key={x.id} value={x.id}>
                        {x.ownerName} · {x.accountNumber}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  Chave PIX
                  <input
                    name="pixKey"
                    placeholder="00000000-0000-0000-0000-000000000000"
                    pattern="[0-9a-fA-F-]{36}"
                    required
                  />
                </label>
              )}
              <div className="row">
                <label>
                  Valor
                  <input
                    name="amount"
                    type="number"
                    min=".01"
                    step=".01"
                    required
                  />
                </label>
                <label>
                  Descrição
                  <input name="description" maxLength={140} required />
                </label>
              </div>
              <label>
                Confirme sua senha
                <input
                  name="password"
                  type="password"
                  minLength={10}
                  maxLength={72}
                  autoComplete="current-password"
                  required
                />
              </label>
              <button>Confirmar transferência</button>
            </form>
          </section>
        )}
        {view === "cards" && <CardsView />}
        {view === "history" && (
          <section>
            <div className="title">
              <h2>Suas transferências</h2>
            </div>
            <div className="table card">
              {transactions.map((t) => (
                <div className="tr" key={t.id}>
                  <span>
                    <b>{t.description}</b>
                    <small>{t.id.slice(0, 8)}</small>
                  </span>
                  <span
                    className={
                      t.destinationAccountId === account.id ? "credit" : "debit"
                    }
                  >
                    {t.destinationAccountId === account.id ? "+" : "−"}
                    {money(t.amount)}
                  </span>
                  <span>
                    <em className={t.status.toLowerCase()}>{t.status}</em>
                  </span>
                  <span>{new Date(t.createdAt).toLocaleString("pt-BR")}</span>
                </div>
              ))}
              {!transactions.length && (
                <p className="empty">Nenhuma transferência encontrada.</p>
              )}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
