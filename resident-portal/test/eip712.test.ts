import { describe, expect, it } from "vitest";
import { privateKeyToAccount } from "viem/accounts";
import {
  signIntakeConsent,
  verifyIntakeConsent,
  type ConsentDomain,
} from "../src/auth/eip712Signer";
import type { SignedConsentPayload } from "../src/types/portal";

const domain: ConsentDomain = { name: "BEAST Resident Portal", version: "1", chainId: 1 };
const consent: SignedConsentPayload = {
  intakeId: "intake-123456",
  consentVersion: "2026-01",
  purpose: "authorize intake processing",
  issuedAt: 1_800_000_000n,
  expiresAt: 1_800_003_600n,
};

// Deterministic test-only key. Never reuse this key outside tests.
const account = privateKeyToAccount(
  "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
);

 describe("EIP-712 intake consent", () => {
  it("signs consent and verifies the expected signer", async () => {
    const signature = await signIntakeConsent(
      { signTypedData: account.signTypedData } as never,
      account.address,
      domain,
      consent,
    );
    await expect(
      verifyIntakeConsent(domain, consent, signature, account.address),
    ).resolves.toBe(true);
    await expect(
      verifyIntakeConsent(domain, consent, signature, "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    ).resolves.toBe(false);
  });

  it("rejects expired or empty consent data before signing", async () => {
    await expect(
      signIntakeConsent(
        { signTypedData: account.signTypedData } as never,
        account.address,
        domain,
        { ...consent, expiresAt: consent.issuedAt },
      ),
    ).rejects.toThrow("CONSENT_EXPIRY_INVALID");
  });
});
