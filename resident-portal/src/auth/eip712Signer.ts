import {
  recoverTypedDataAddress,
  type Address,
  type Hex,
  type WalletClient,
} from "viem";
import type { SignedConsentPayload } from "../types/portal";

export interface ConsentDomain {
  name: string;
  version: string;
  chainId: number;
}

export const CONSENT_TYPES = {
  SignedConsent: [
    { name: "intakeId", type: "string" },
    { name: "consentVersion", type: "string" },
    { name: "purpose", type: "string" },
    { name: "issuedAt", type: "uint64" },
    { name: "expiresAt", type: "uint64" },
  ],
} as const;

function validateConsent(consent: SignedConsentPayload): void {
  if (!consent.intakeId || !consent.consentVersion || !consent.purpose) {
    throw new Error("CONSENT_FIELDS_REQUIRED");
  }
  if (consent.expiresAt <= consent.issuedAt) {
    throw new Error("CONSENT_EXPIRY_INVALID");
  }
}

/** Requests a wallet typed-data signature. This signs a message; it sends no transaction. */
export async function signIntakeConsent(
  wallet: WalletClient,
  account: Address,
  domain: ConsentDomain,
  consent: SignedConsentPayload,
): Promise<Hex> {
  validateConsent(consent);
  return wallet.signTypedData({
    account,
    domain,
    types: CONSENT_TYPES,
    primaryType: "SignedConsent",
    message: consent,
  });
}

/** Recover and compare the wallet that signed the typed consent. */
export async function verifyIntakeConsent(
  domain: ConsentDomain,
  consent: SignedConsentPayload,
  signature: Hex,
  expectedAddress: Address,
): Promise<boolean> {
  validateConsent(consent);
  try {
    const recovered = await recoverTypedDataAddress({
      domain,
      types: CONSENT_TYPES,
      primaryType: "SignedConsent",
      message: consent,
      signature,
    });
    return recovered.toLowerCase() === expectedAddress.toLowerCase();
  } catch {
    return false;
  }
}
