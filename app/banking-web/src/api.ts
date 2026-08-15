import type{Account,ApiError,DirectoryEntry,Transaction}from"./types";
async function request<T>(path:string,init?:RequestInit):Promise<T>{const r=await fetch(path,{...init,credentials:"same-origin",headers:{"Content-Type":"application/json",...init?.headers}});if(!r.ok){const e=await r.json().catch(()=>({})) as ApiError;throw new Error(e.message??`Falha HTTP ${r.status}`)}if(r.status===204)return undefined as T;return r.json() as Promise<T>}
export const api={
 me:()=>request<Account>("/bank/accounts/me"),
 directory:()=>request<DirectoryEntry[]>("/bank/accounts/directory"),
 login:(accountNumber:string,password:string)=>request<Account>("/bank/auth/login",{method:"POST",body:JSON.stringify({accountNumber,password})}),
 register:(ownerName:string,password:string)=>request<Account>("/bank/auth/register",{method:"POST",body:JSON.stringify({ownerName,password})}),
 logout:()=>request<void>("/bank/auth/logout",{method:"POST"}),
 statement:(sourceAccountId:string)=>request<Transaction[]>(`/bank/transactions?sourceAccountId=${sourceAccountId}`),
 transfer:(sourceAccountId:string,destinationAccountId:string,amount:number,description:string,password:string)=>request<Transaction>("/bank/transactions",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify({sourceAccountId,destinationAccountId,amount,description,password})}),
 transaction:(id:string)=>request<Transaction>(`/bank/transactions/${id}`)
};
export const money=(v:number)=>new Intl.NumberFormat("pt-BR",{style:"currency",currency:"BRL"}).format(v);
