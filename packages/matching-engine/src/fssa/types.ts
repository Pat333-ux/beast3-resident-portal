export type FssaProgramCode = "SNAP" | "TANF" | "HIP_2_0" | "CCDF";

export interface HouseholdMember {
  id: string;
  age: number;
  isStudent?: boolean;
  isDiscoveredDisabled?: boolean;
  isPregnant?: boolean;
  earnedMonthlyIncome: number;
  unearnedMonthlyIncome: number;
}

export interface FssaIntakeProfile {
  county: string;
  householdMembers: HouseholdMember[];
  monthlyShelterCost: number;
  monthlyUtilityStandard: boolean;
  monthlyOutofPocketMedical: number;
  dependentCareCosts: number;
}

export interface VerificationRequirement {
  code: string;
  category: "INCOME" | "RESIDENCY" | "IDENTITY" | "EXPENSE" | "HOUSEHOLD";
  description: string;
  acceptableDocuments: string[];
}

export interface ProgramEligibilityResult {
  program: FssaProgramCode;
  eligible: boolean;
  reasonCode: string;
  estimatedMonthlyBenefit: number | null;
  grossIncomeLimit: number;
  netIncomeLimit: number | null;
  calculatedGrossIncome: number;
  calculatedNetIncome: number | null;
  verificationsRequired: VerificationRequirement[];
}

export interface FssaEvaluationSummary {
  evaluatedAt: string;
  county: string;
  householdSize: number;
  /** Estimates only; only Indiana FSSA can determine eligibility and award amounts. */
  results: Record<FssaProgramCode, ProgramEligibilityResult>;
}
