"""
beast3-resident-portal | Credit Line & Mortgage Readiness Simulator
Calculates Indiana IHCDA mortgage buying power and LUCR-backed revolving line of credit limits.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/simulator", tags=["Readiness Simulator"])


class ReadinessInput(BaseModel):
    gross_monthly_income: float = Field(..., ge=500, examples=[3200.0])
    monthly_debt_payments: float = Field(..., ge=0, examples=[350.0])
    current_fico_score: int = Field(..., ge=300, le=850, examples=[595])
    target_home_price: float = Field(..., ge=50000, examples=[185000.0])
    lucr_escrow_allocation_usd: float = Field(default=1000.0, ge=0)


class ReadinessProjection(BaseModel):
    current_fico: int
    projected_fico_after_audit: int
    estimated_mortgage_rate_pct: float
    ihcda_down_payment_grant_usd: float
    estimated_monthly_mortgage_usd: float
    dti_ratio_pct: float
    mortgage_ready: bool
    unlocked_credit_line_usd: float
    readiness_summary: str


@router.post("/project", response_model=ReadinessProjection)
async def project_readiness(data: ReadinessInput):
    # 1. Estimate FICO boost from FCRA dispute audit + LUCR tradeline paydown
    score_gap = max(0, 720 - data.current_fico_score)
    escrow_boost = min(35, int(data.lucr_escrow_allocation_usd / 50))
    audit_boost = min(45, int(score_gap * 0.35))
    projected_fico = min(820, data.current_fico_score + escrow_boost + audit_boost)

    # 2. Estimate interest rate based on post-audit FICO tier
    if projected_fico >= 720:
        est_rate = 6.15
    elif projected_fico >= 660:
        est_rate = 6.55
    elif projected_fico >= 620:
        est_rate = 6.95
    else:
        est_rate = 7.45

    # 3. Indiana IHCDA First Place Grant (up to 6% of purchase price)
    ihcda_grant = round(data.target_home_price * 0.06, 2)
    loan_principal = max(0.0, data.target_home_price - ihcda_grant)

    # Standard 30-year fixed principal & interest + estimated Indiana taxes/insurance (~1.35%/yr)
    monthly_rate = (est_rate / 100.0) / 12.0
    n_payments = 360
    pi_payment = (
        loan_principal
        * (monthly_rate * (1 + monthly_rate) ** n_payments)
        / ((1 + monthly_rate) ** n_payments - 1)
    )
    taxes_and_insurance = (data.target_home_price * 0.0135) / 12.0
    total_monthly_mortgage = round(pi_payment + taxes_and_insurance, 2)

    # 4. Debt-to-Income (DTI) Check (Standard FHA/IHCDA front/back-end ceiling ~45%)
    total_monthly_obligations = data.monthly_debt_payments + total_monthly_mortgage
    dti_pct = round((total_monthly_obligations / data.gross_monthly_income) * 100, 1)
    is_ready = projected_fico >= 620 and dti_pct <= 45.0

    # 5. Revolving Line of Credit Calculation (Safety Net & Growth Pillar)
    # Backed by post-audit credit tier + LUCR reserve ratio
    base_credit_line = data.gross_monthly_income * (1.5 if projected_fico >= 660 else 0.75)
    escrow_backing = data.lucr_escrow_allocation_usd * 2.0
    unlocked_credit_line = round(min(15000.0, base_credit_line + escrow_backing), 2)

    if is_ready:
        summary = (
            f"Ready for MISMO 3.4 submission: IHCDA covers ${ihcda_grant:,.0f} down payment, "
            f"and your post-audit score ({projected_fico}) unlocks a ${unlocked_credit_line:,.0f} revolving credit line."
        )
    else:
        summary = (
            f"Action Plan: Complete FCRA dispute cycle to lift score from {data.current_fico_score} to {projected_fico} "
            f"and reduce DTI from {dti_pct}% below 45% to unlock your mortgage and ${unlocked_credit_line:,.0f} credit line."
        )

    return ReadinessProjection(
        current_fico=data.current_fico_score,
        projected_fico_after_audit=projected_fico,
        estimated_mortgage_rate_pct=est_rate,
        ihcda_down_payment_grant_usd=ihcda_grant,
        estimated_monthly_mortgage_usd=total_monthly_mortgage,
        dti_ratio_pct=dti_pct,
        mortgage_ready=is_ready,
        unlocked_credit_line_usd=unlocked_credit_line,
        readiness_summary=summary,
    )
