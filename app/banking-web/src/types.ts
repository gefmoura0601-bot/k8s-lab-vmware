export type Account = {
  id: string;
  accountNumber: string;
  ownerName: string;
  balance: number;
  cpfMasked?: string;
};
export type DirectoryEntry = {
  id: string;
  accountNumber: string;
  ownerName: string;
};
export type PixKey = { pixKey: string; accountId: string };
export type Transaction = {
  id: string;
  sourceAccountId: string;
  destinationAccountId: string;
  amount: number;
  description: string;
  status: "PENDING" | "COMPLETED" | "FAILED";
  createdAt: string;
  completedAt?: string;
  failureCode?: string;
};
export type ApiError = { code?: string; message?: string };

export type CardType = "DEBIT" | "CREDIT";
export type CardFormFactor = "VIRTUAL" | "PHYSICAL";
export type CardStatus = "ACTIVE" | "BLOCKED" | "CANCELLED";

export type CardSummary = {
  id: string;
  type: CardType;
  formFactor: CardFormFactor;
  status: CardStatus;
  last4: string;
  expiryMonth: number;
  expiryYear: number;
  creditLimit: number | null;
  usedAmount: number | null;
  availableAmount: number;
  createdAt: string;
};

export type CardDetails = CardSummary & {
  holderName: string;
  number: string;
  cvv: string;
};

export type CardPurchase = {
  paymentId: string;
  cardId: string | null;
  merchantId: string;
  merchantName: string;
  orderReference: string;
  amount: number;
  currency: string;
  paymentType: CardType;
  installments: number;
  status: "CAPTURED" | "DECLINED";
  authorizationCode?: string;
  declineCode?: string;
  createdAt: string;
};
