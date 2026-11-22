"use client";

import { useState, type FormEvent } from "react";
import type { IntakeEnvelope, ResidentProfile } from "../types/portal";

interface IntakeFormProps {
  onSubmit: (envelope: IntakeEnvelope) => void | Promise<void>;
}

const focusOptions = ["housing", "food", "utilities", "healthcare", "veteran_support"] as const;

export function IntakeForm({ onSubmit }: IntakeFormProps) {
  const [householdSize, setHouseholdSize] = useState("1");
  const [incomeDollars, setIncomeDollars] = useState("");
  const [state, setState] = useState("IN");
  const [county, setCounty] = useState("Vigo");
  const [city, setCity] = useState("Terre Haute");
  const [targetFocusAreas, setTargetFocusAreas] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  function toggleFocus(area: string) {
    setTargetFocusAreas((current) =>
      current.includes(area) ? current.filter((value) => value !== area) : [...current, area],
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErrorCode(null);
    const income = Number(incomeDollars);
    const size = Number(householdSize);
    if (!Number.isFinite(income) || income < 0 || !Number.isInteger(income * 100)) {
      setErrorCode("INVALID_INCOME");
      return;
    }
    if (!Number.isInteger(size) || size < 1 || size > 30 || !state.trim() || !county.trim() || !city.trim()) {
      setErrorCode("INVALID_PROFILE");
      return;
    }
    if (targetFocusAreas.length === 0) {
      setErrorCode("FOCUS_AREA_REQUIRED");
      return;
    }

    const profile: ResidentProfile = {
      householdSize: size,
      grossMonthlyIncomeCents: Math.round(income * 100),
      location: { state: state.trim(), county: county.trim(), city: city.trim() },
      targetFocusAreas,
    };
    const envelope: IntakeEnvelope = {
      schemaVersion: "1.0.0",
      intakeId: crypto.randomUUID(),
      correlation_id: crypto.randomUUID(),
      submittedAt: new Date().toISOString(),
      profile,
    };

    setSubmitting(true);
    try {
      await onSubmit(envelope);
    } catch {
      // Keep errors fixed and non-identifying; never log form values or server payloads.
      setErrorCode("SUBMISSION_FAILED");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <label>
        Household size
        <input
          type="number"
          min={1}
          max={30}
          step={1}
          value={householdSize}
          onChange={(event) => setHouseholdSize(event.target.value)}
          required
        />
      </label>
      <label>
        Gross monthly household income (USD)
        <input
          type="number"
          min={0}
          step="0.01"
          inputMode="decimal"
          value={incomeDollars}
          onChange={(event) => setIncomeDollars(event.target.value)}
          required
        />
      </label>
      <label>
        State
        <input value={state} onChange={(event) => setState(event.target.value)} required />
      </label>
      <label>
        County
        <input value={county} onChange={(event) => setCounty(event.target.value)} required />
      </label>
      <label>
        City
        <input value={city} onChange={(event) => setCity(event.target.value)} required />
      </label>
      <fieldset>
        <legend>Areas where you would like support</legend>
        {focusOptions.map((area) => (
          <label key={area}>
            <input
              type="checkbox"
              checked={targetFocusAreas.includes(area)}
              onChange={() => toggleFocus(area)}
            />
            {area.replaceAll("_", " ")}
          </label>
        ))}
      </fieldset>
      {errorCode && <p role="alert">{errorCode}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? "Submitting…" : "Continue"}
      </button>
    </form>
  );
}
