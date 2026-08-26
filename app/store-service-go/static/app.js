const productsElement = document.querySelector("#products");
const checkoutSection = document.querySelector("#checkout-section");
const selectedProductElement = document.querySelector("#selected-product");
const quantityElement = document.querySelector("#quantity");
const totalElement = document.querySelector("#total");
const form = document.querySelector("#checkout-form");
const resultElement = document.querySelector("#result");
const payButton = document.querySelector("#pay-button");
const installmentsLabel = document.querySelector("#installments-label");

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
let products = [];
let selectedProduct = null;
let retryKey = null;

function cardDigits(value) {
  return String(value).replace(/\D/g, "").slice(0, 16);
}

function validLuhn(number) {
  let sum = 0;
  let double = false;
  for (let index = number.length - 1; index >= 0; index -= 1) {
    let digit = Number(number[index]);
    if (double) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    double = !double;
  }
  return sum % 10 === 0;
}

function validLabCardNumber(value) {
  const digits = cardDigits(value);
  return digits.length === 16 && digits.startsWith("999999") && validLuhn(digits);
}

function escapeText(value) {
  const element = document.createElement("span");
  element.textContent = String(value);
  return element.innerHTML;
}

function renderProducts() {
  productsElement.innerHTML = products.map((product) => `
    <article class="product">
      <div class="product-icon" aria-hidden="true">${escapeText(product.icon)}</div>
      <h3>${escapeText(product.name)}</h3>
      <p>${escapeText(product.description)}</p>
      <div class="product-bottom">
        <strong>${brl.format(product.price)}</strong>
        <button type="button" data-product="${escapeText(product.id)}">Comprar</button>
      </div>
    </article>`).join("");
}

function updateTotal() {
  if (!selectedProduct) return;
  const quantity = Math.min(10, Math.max(1, Number(quantityElement.value) || 1));
  quantityElement.value = String(quantity);
  totalElement.textContent = brl.format(selectedProduct.price * quantity);
}

function chooseProduct(id) {
  selectedProduct = products.find((product) => product.id === id);
  if (!selectedProduct) return;
  retryKey = null;
  selectedProductElement.innerHTML = `<h3>${escapeText(selectedProduct.name)}</h3><p>${escapeText(selectedProduct.description)}</p>`;
  quantityElement.value = "1";
  updateTotal();
  checkoutSection.classList.remove("hidden");
  resultElement.classList.add("hidden");
  checkoutSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

productsElement.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-product]");
  if (button) chooseProduct(button.dataset.product);
});
quantityElement.addEventListener("change", () => { retryKey = null; updateTotal(); });

form.addEventListener("change", (event) => {
  retryKey = null;
  if (event.target.name === "paymentType") {
    const credit = event.target.value === "CREDIT";
    installmentsLabel.classList.toggle("hidden", !credit);
    if (!credit) form.elements.installments.value = "1";
  }
});

form.elements.number.addEventListener("input", (event) => {
  const digits = cardDigits(event.target.value);
  event.target.value = digits.replace(/(.{4})/g, "$1 ").trim();
  event.target.setCustomValidity("");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedProduct) return;
  const data = new FormData(form);
  const number = cardDigits(data.get("number"));
  if (!validLabCardNumber(number)) {
    form.elements.number.setCustomValidity("Use um cartão virtual do laboratório iniciado em 999999.");
    form.elements.number.reportValidity();
    form.elements.number.focus();
    return;
  }
  form.elements.number.setCustomValidity("");
  retryKey ||= crypto.randomUUID();
  const payload = {
    productId: selectedProduct.id,
    quantity: Number(quantityElement.value),
    paymentType: String(data.get("paymentType")),
    installments: Number(data.get("installments") || 1),
    card: {
      number,
      holderName: String(data.get("holderName")),
      expiryMonth: Number(data.get("expiryMonth")),
      expiryYear: Number(data.get("expiryYear")),
      cvv: String(data.get("cvv")),
    },
  };

  payButton.disabled = true;
  payButton.textContent = "Consultando adquirência…";
  resultElement.classList.add("hidden");
  try {
    const response = await fetch("/store/api/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": retryKey },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({ message: `Falha HTTP ${response.status}` }));
    if (response.status === 409) {
      resultElement.className = "result declined";
      resultElement.innerHTML = '<p class="eyebrow">PAGAMENTO NÃO REENVIADO</p><h2>Conflito de idempotência</h2><p class="error">A chave desta tentativa já está associada a outro pagamento.</p><p>Os dados e a mesma chave foram mantidos para uma nova tentativa segura.</p>';
      resultElement.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    if (!response.ok && !result.status) throw new Error(result.message || `Falha HTTP ${response.status}`);

    const approved = result.status === "CAPTURED";
    resultElement.className = `result ${approved ? "" : "declined"}`;
    resultElement.innerHTML = `
      <p class="eyebrow">RESULTADO DA ADQUIRÊNCIA</p>
      <h2>${approved ? "Compra autorizada" : "Compra recusada"}</h2>
      <p>${approved ? "A transação foi capturada pelo Moura Banking." : `Motivo: ${escapeText(result.declineCode || "não informado")}`}</p>
      <dl>
        <dt>Pagamento</dt><dd>${escapeText(result.paymentId)}</dd>
        <dt>Pedido</dt><dd>${escapeText(result.orderId)}</dd>
        <dt>Cartão</dt><dd>•••• ${escapeText(result.last4)} · ${escapeText(result.cardType)}</dd>
        <dt>Valor</dt><dd>${brl.format(result.amount)}</dd>
        ${result.authorizationCode ? `<dt>Autorização</dt><dd>${escapeText(result.authorizationCode)}</dd>` : ""}
      </dl>`;
		retryKey = null;
		form.elements.number.value = "";
		form.elements.cvv.value = "";
    resultElement.scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    resultElement.className = "result declined";
    resultElement.innerHTML = `<p class="eyebrow">NÃO FOI POSSÍVEL CONCLUIR</p><h2>Falha na comunicação</h2><p class="error">${escapeText(error.message)}</p><p>Tente novamente; a mesma chave idempotente será reutilizada.</p>`;
  } finally {
    payButton.disabled = false;
    payButton.textContent = "Autorizar compra";
  }
});

fetch("/store/api/catalog")
  .then((response) => {
    if (!response.ok) throw new Error(`Falha HTTP ${response.status}`);
    return response.json();
  })
  .then((catalog) => { products = catalog.products; renderProducts(); })
  .catch(() => { productsElement.innerHTML = '<p class="error">Catálogo indisponível. Atualize a página para tentar novamente.</p>'; });
