import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, money } from "./api";
import type { Account, Transaction } from "./types";

type View = "overview" | "accounts" | "transfer" | "history";
const HISTORY_KEY = "atlas-banking-transactions";

function initials(name: string) {
  return name.split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase();
}

export function App() {
  const [view, setView] = useState<View>("overview");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const nextAccounts = await api.listAccounts();
      const ids = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as string[];
      const settled = await Promise.allSettled(ids.map(api.getTransaction));
      setAccounts(nextAccounts);
      setTransactions(settled.flatMap(item => item.status === "fulfilled" ? [item.value] : []));
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Não foi possível carregar os dados.");
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const total = useMemo(() => accounts.reduce((sum, account) => sum + Number(account.balance), 0), [accounts]);

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api.createAccount(String(data.get("ownerName")), Number(data.get("initialBalance")));
      event.currentTarget.reset();
      setMessage("Conta criada com sucesso.");
      await load();
    } catch (error) { setMessage((error as Error).message); }
  }

  async function transfer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const transaction = await api.createTransaction(
        String(data.get("source")), String(data.get("destination")),
        Number(data.get("amount")), String(data.get("description"))
      );
      const ids = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? "[]") as string[];
      localStorage.setItem(HISTORY_KEY, JSON.stringify([transaction.id, ...ids.filter(id => id !== transaction.id)].slice(0, 20)));
      form.reset();
      setMessage("Transferência processada.");
      await load();
      setView("history");
    } catch (error) { setMessage((error as Error).message); }
  }

  const nav: [View, string][] = [["overview", "Visão geral"], ["accounts", "Contas"], ["transfer", "Transferir"], ["history", "Histórico"]];
  return <div className="shell">
    <aside>
      <div className="brand"><span>A</span><div><strong>Atlas</strong><small>Banking Lab</small></div></div>
      <nav>{nav.map(([id, label]) => <button className={view === id ? "active" : ""} onClick={() => setView(id)} key={id}>{label}</button>)}</nav>
      <div className="platform"><i />Plataforma operacional</div>
    </aside>
    <main>
      <header><div><p>AMBIENTE DE LABORATÓRIO</p><h1>{nav.find(([id]) => id === view)?.[1]}</h1></div><button className="refresh" onClick={() => void load()}>Atualizar</button></header>
      {message && <div className="notice">{message}</div>}
      {busy ? <div className="loading">Sincronizando dados…</div> : <>
        {view === "overview" && <section>
          <div className="hero"><div><p>PATRIMÔNIO SOB GESTÃO</p><strong>{money(total)}</strong><span>{accounts.length} contas ativas</span></div><div className="monogram">AB</div></div>
          <div className="stats"><article><span>Contas</span><strong>{accounts.length}</strong></article><article><span>Transferências locais</span><strong>{transactions.length}</strong></article><article><span>Concluídas</span><strong>{transactions.filter(t => t.status === "COMPLETED").length}</strong></article></div>
          <Title text="Contas recentes" action={() => setView("accounts")} />
          <AccountGrid accounts={accounts.slice(0, 3)} />
        </section>}
        {view === "accounts" && <section>
          <div className="split"><div><Title text="Todas as contas" /><AccountGrid accounts={accounts} /></div>
            <form className="card form" onSubmit={createAccount}><h2>Nova conta</h2><label>Titular<input name="ownerName" maxLength={120} required /></label><label>Saldo inicial<input name="initialBalance" type="number" min="0" step="0.01" required /></label><button>Criar conta</button></form>
          </div>
        </section>}
        {view === "transfer" && <section className="narrow"><form className="card form transfer" onSubmit={transfer}><p className="eyebrow">TRANSFERÊNCIA INTERNA</p><h2>Movimentar entre contas</h2>
          <label>Conta de origem<select name="source" required defaultValue=""><option value="" disabled>Selecione</option>{accounts.map(a => <option value={a.id} key={a.id}>{a.ownerName} · {money(a.balance)}</option>)}</select></label>
          <label>Conta de destino<select name="destination" required defaultValue=""><option value="" disabled>Selecione</option>{accounts.map(a => <option value={a.id} key={a.id}>{a.ownerName}</option>)}</select></label>
          <div className="row"><label>Valor<input name="amount" type="number" min="0.01" step="0.01" required /></label><label>Descrição<input name="description" maxLength={140} required /></label></div>
          <button>Confirmar transferência</button><small>A operação usa chave idempotente e não será debitada duas vezes.</small>
        </form></section>}
        {view === "history" && <section><Title text="Histórico neste navegador" /><div className="table card"><div className="tr head"><span>Descrição</span><span>Valor</span><span>Status</span><span>Data</span></div>{transactions.map(t => <div className="tr" key={t.id}><span><b>{t.description}</b><small>{t.id.slice(0, 8)}</small></span><span>{money(t.amount)}</span><span><em className={t.status.toLowerCase()}>{t.status}</em></span><span>{new Date(t.createdAt).toLocaleString("pt-BR")}</span></div>)}{!transactions.length && <p className="empty">As transferências feitas neste navegador aparecerão aqui.</p>}</div></section>}
      </>}
    </main>
  </div>;
}

function Title({text, action}: {text: string; action?: () => void}) {
  return <div className="title"><h2>{text}</h2>{action && <button onClick={action}>Ver todas</button>}</div>;
}
function AccountGrid({accounts}: {accounts: Account[]}) {
  return <div className="account-grid">{accounts.map(account => <article className="card account" key={account.id}><div className="avatar">{initials(account.ownerName)}</div><div><h3>{account.ownerName}</h3><small>{account.id.slice(0, 8)} · conta digital</small><strong>{money(account.balance)}</strong></div></article>)}{!accounts.length && <p className="empty">Nenhuma conta cadastrada.</p>}</div>;
}
