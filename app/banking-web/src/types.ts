export type Account = {
  id: string;
  accountNumber: string;
  ownerName: string;
  balance: number;
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
