import { describe, expect, it } from "vitest";
import { getMonthlyFPL, IndianaFssaRulesEngine } from "../src/fssa/rulesEngine";
import type { FssaIntakeProfile } from "../src/fssa/types";

const familyOfThree: FssaIntakeProfile = {
  county: "Vigo",
  householdMembers: [
    { id: "m1", age: 34, earnedMonthlyIncome: 1400, unearnedMonthlyIncome: 0 },
    { id: "m2", age: 8, earnedMonthlyIncome: 0, unearnedMonthlyIncome: 0 },
    { id: "m3", age: 5, earnedMonthlyIncome: 0, unearnedMonthlyIncome: 0 },
  ],
  monthlyShelterCost: 650,
  monthlyUtilityStandard: true,
  monthlyOutofPocketMedical: 0,
  dependentCareCosts: 0,
};

describe("Indiana FSSA preliminary rules engine", () => {
  it("calculates configured monthly FPL values and additional household members", () => {
    expect(getMonthlyFPL(1)).toBe(1255);
    expect(getMonthlyFPL(4)).toBe(2600);
    expect(getMonthlyFPL(9)).toBe(4841);
    expect(() => getMonthlyFPL(0)).toThrow(RangeError);
  });

  it("provides a preliminary SNAP match and income verification checklist", () => {
    const summary = IndianaFssaRulesEngine.evaluateAll(familyOfThree);
    const snap = summary.results.SNAP;
    expect(summary.county).toBe("Vigo");
    expect(snap.eligible).toBe(true);
    expect(snap.reasonCode).toBe("PRE_QUALIFIED");
    expect(snap.calculatedGrossIncome).toBe(1400);
    expect(snap.estimatedMonthlyBenefit).toBeGreaterThan(0);
    expect(snap.verificationsRequired).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "VERIF_RESIDENCY" }),
      expect.objectContaining({ code: "VERIF_EARNED_INCOME" }),
    ]));
  });

  it("requires a qualifying child for TANF", () => {
    const adultOnly = { ...familyOfThree, householdMembers: [
      { id: "adult", age: 45, earnedMonthlyIncome: 800, unearnedMonthlyIncome: 0 },
    ] };
    const tanf = IndianaFssaRulesEngine.evaluateTANF(adultOnly);
    expect(tanf.eligible).toBe(false);
    expect(tanf.reasonCode).toBe("NO_QUALIFYING_DEPENDENT_CHILD");
  });

  it("rejects malformed or empty household input rather than producing NaN", () => {
    expect(() => IndianaFssaRulesEngine.evaluateAll({ ...familyOfThree, householdMembers: [] }))
      .toThrow(RangeError);
    expect(() => IndianaFssaRulesEngine.evaluateAll({
      ...familyOfThree,
      householdMembers: [{ id: "m1", age: 34, earnedMonthlyIncome: -1, unearnedMonthlyIncome: 0 }],
    })).toThrow(TypeError);
  });
});
