import type {
  FssaEvaluationSummary,
  FssaIntakeProfile,
  FssaProgramCode,
  HouseholdMember,
  ProgramEligibilityResult,
  VerificationRequirement,
} from "./types";

/**
 * Illustrative screening assumptions supplied for this prototype. These values
 * must be checked against current federal and Indiana FSSA policy before use.
 * This is not an official eligibility determination or benefits calculator.
 */
export const FPL_MONTHLY_2026: Readonly<Record<number, number>> = Object.freeze({
  1: 1255, 2: 1703, 3: 2151, 4: 2600,
  5: 3048, 6: 3496, 7: 3945, 8: 4393,
});
const FPL_PER_ADDITIONAL_PERSON = 448;

export function getMonthlyFPL(householdSize: number): number {
  if (!Number.isInteger(householdSize) || householdSize < 1) {
    throw new RangeError("householdSize must be a positive integer");
  }
  if (householdSize <= 8) return FPL_MONTHLY_2026[householdSize];
  return FPL_MONTHLY_2026[8] + (householdSize - 8) * FPL_PER_ADDITIONAL_PERSON;
}

const VERIFICATIONS: Readonly<Record<string, VerificationRequirement>> = {
  EARNED_INCOME: {
    code: "VERIF_EARNED_INCOME", category: "INCOME",
    description: "Proof of earned income for the past 30 days.",
    acceptableDocuments: ["Recent consecutive pay stubs", "Employer verification", "W-2 or tax return"],
  },
  UNEARNED_INCOME: {
    code: "VERIF_UNEARNED_INCOME", category: "INCOME",
    description: "Proof of unearned income.",
    acceptableDocuments: ["Social Security award letter", "Child support history", "Unemployment statement"],
  },
  SHELTER_EXPENSE: {
    code: "VERIF_SHELTER", category: "EXPENSE",
    description: "Proof of shelter expenses.",
    acceptableDocuments: ["Current lease", "Rent receipt", "Mortgage statement"],
  },
  RESIDENCY: {
    code: "VERIF_RESIDENCY", category: "RESIDENCY",
    description: "Proof of Indiana residency.",
    acceptableDocuments: ["Indiana state ID", "Utility bill", "Lease agreement"],
  },
};

function validateProfile(profile: FssaIntakeProfile): void {
  if (!profile || typeof profile.county !== "string" || !profile.county.trim()) {
    throw new TypeError("county is required");
  }
  if (!Array.isArray(profile.householdMembers) || profile.householdMembers.length < 1) {
    throw new RangeError("householdMembers must contain at least one member");
  }
  const validNonNegative = (n: number) => Number.isFinite(n) && n >= 0;
  for (const member of profile.householdMembers) {
    if (!member.id || !Number.isInteger(member.age) || member.age < 0 || member.age > 120
      || !validNonNegative(member.earnedMonthlyIncome)
      || !validNonNegative(member.unearnedMonthlyIncome)) {
      throw new TypeError("household member data is invalid");
    }
  }
  if (!validNonNegative(profile.monthlyShelterCost)
    || !validNonNegative(profile.monthlyOutofPocketMedical)
    || !validNonNegative(profile.dependentCareCosts)
    || typeof profile.monthlyUtilityStandard !== "boolean") {
    throw new TypeError("household expenses are invalid");
  }
}

function incomeTotals(profile: FssaIntakeProfile) {
  return profile.householdMembers.reduce(
    (totals, member) => ({
      earned: totals.earned + member.earnedMonthlyIncome,
      unearned: totals.unearned + member.unearnedMonthlyIncome,
    }),
    { earned: 0, unearned: 0 },
  );
}

function requiredVerifications(earned: number, unearned: number, shelter: boolean): VerificationRequirement[] {
  const result = [VERIFICATIONS.RESIDENCY];
  if (earned > 0) result.push(VERIFICATIONS.EARNED_INCOME);
  if (unearned > 0) result.push(VERIFICATIONS.UNEARNED_INCOME);
  if (shelter) result.push(VERIFICATIONS.SHELTER_EXPENSE);
  return result.map((item) => ({ ...item, acceptableDocuments: [...item.acceptableDocuments] }));
}

function result(
  program: FssaProgramCode,
  eligible: boolean,
  reasonCode: string,
  estimatedMonthlyBenefit: number | null,
  grossIncomeLimit: number,
  netIncomeLimit: number | null,
  calculatedGrossIncome: number,
  calculatedNetIncome: number | null,
  verificationsRequired: VerificationRequirement[],
): ProgramEligibilityResult {
  return { program, eligible, reasonCode, estimatedMonthlyBenefit, grossIncomeLimit,
    netIncomeLimit, calculatedGrossIncome, calculatedNetIncome, verificationsRequired };
}

export class IndianaFssaRulesEngine {
  static evaluateAll(profile: FssaIntakeProfile): FssaEvaluationSummary {
    validateProfile(profile);
    const results = {
      SNAP: this.evaluateSNAP(profile),
      TANF: this.evaluateTANF(profile),
      HIP_2_0: this.evaluateHIP(profile),
      CCDF: this.evaluateCCDF(profile),
    };
    return {
      evaluatedAt: new Date().toISOString(), county: profile.county,
      householdSize: profile.householdMembers.length, results,
    };
  }

  static evaluateSNAP(profile: FssaIntakeProfile): ProgramEligibilityResult {
    validateProfile(profile);
    const size = profile.householdMembers.length;
    const fpl = getMonthlyFPL(size);
    const grossLimit = Math.round(fpl * 1.3);
    const netLimit = fpl;
    const { earned, unearned } = incomeTotals(profile);
    const gross = earned + unearned;
    const hasElderlyOrDisabled = profile.householdMembers.some((m) => m.age >= 60 || m.isDiscoveredDisabled === true);
    const docs = requiredVerifications(earned, unearned, true);
    if (!hasElderlyOrDisabled && gross > grossLimit) {
      return result("SNAP", false, "EXCEEDS_GROSS_INCOME_LIMIT", 0, grossLimit, netLimit, gross, null, docs);
    }

    const earnedDeduction = earned * 0.2;
    const standardDeduction = size <= 3 ? 198 : size === 4 ? 208 : 244;
    const utilityDeduction = profile.monthlyUtilityStandard ? 443 : 0;
    const adjustedIncome = gross - earnedDeduction - standardDeduction - profile.dependentCareCosts;
    const halfAdjusted = Math.max(0, adjustedIncome / 2);
    let shelterDeduction = Math.max(0, profile.monthlyShelterCost + utilityDeduction - halfAdjusted);
    if (!hasElderlyOrDisabled) shelterDeduction = Math.min(shelterDeduction, 672);
    const net = Math.max(0, adjustedIncome - shelterDeduction);
    const eligible = net <= netLimit;
    const allotments: Record<number, number> = { 1: 291, 2: 535, 3: 766, 4: 973 };
    const max = allotments[Math.min(size, 4)] ?? (973 + (size - 4) * 211);
    const estimated = eligible ? Math.max(23, Math.round(max - net * 0.3)) : 0;
    return result("SNAP", eligible, eligible ? "PRE_QUALIFIED" : "EXCEEDS_NET_INCOME_LIMIT",
      estimated, grossLimit, netLimit, gross, net, docs);
  }

  static evaluateHIP(profile: FssaIntakeProfile): ProgramEligibilityResult {
    validateProfile(profile);
    const fpl = getMonthlyFPL(profile.householdMembers.length);
    const grossLimit = Math.round(fpl * 1.38);
    const { earned, unearned } = incomeTotals(profile);
    const gross = earned + unearned;
    const magi = Math.max(0, gross - Math.round(fpl * 0.05));
    const eligible = magi <= grossLimit;
    return result("HIP_2_0", eligible, eligible ? "PRE_QUALIFIED" : "EXCEEDS_MAGI_INCOME_LIMIT",
      null, grossLimit, null, gross, magi, requiredVerifications(earned, unearned, false));
  }

  static evaluateTANF(profile: FssaIntakeProfile): ProgramEligibilityResult {
    validateProfile(profile);
    const size = profile.householdMembers.length;
    const fpl = getMonthlyFPL(size);
    const hasChild = profile.householdMembers.some((member) => member.age < 18);
    const { earned, unearned } = incomeTotals(profile);
    const gross = earned + unearned;
    const docs = requiredVerifications(earned, unearned, true);
    if (!hasChild) return result("TANF", false, "NO_QUALIFYING_DEPENDENT_CHILD", 0, 0, 0, gross, gross, []);
    const limit = Math.round(fpl * 0.5);
    const eligible = gross <= limit;
    return result("TANF", eligible, eligible ? "PRE_QUALIFIED" : "EXCEEDS_TANF_INCOME_STANDARD",
      eligible ? Math.max(0, Math.round(limit - gross * 0.75)) : 0, limit, limit, gross, gross, docs);
  }

  static evaluateCCDF(profile: FssaIntakeProfile): ProgramEligibilityResult {
    validateProfile(profile);
    const size = profile.householdMembers.length;
    const fpl = getMonthlyFPL(size);
    const { earned, unearned } = incomeTotals(profile);
    const gross = earned + unearned;
    if (!profile.householdMembers.some((member) => member.age < 13)) {
      return result("CCDF", false, "NO_QUALIFYING_CHILD_UNDER_13", null, 0, null, gross, null, []);
    }
    const limit = Math.round(fpl * 1.5);
    const eligible = gross <= limit;
    return result("CCDF", eligible, eligible ? "PRE_QUALIFIED" : "EXCEEDS_CCDF_INITIAL_LIMIT",
      null, limit, null, gross, null, requiredVerifications(earned, unearned, false));
  }
}
