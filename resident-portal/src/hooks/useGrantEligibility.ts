import { useEffect, useState } from "react";
import type { ResidentProfile } from "../types/portal";

export interface GrantMatch {
  id: string;
  name: string;
  score: number;
  readinessChecklist: string[];
}

/** Adapter contract for the installed @grant-hub/matching-engine workspace package. */
export interface GrantMatcher {
  matchGrants(profile: ResidentProfile): Promise<GrantMatch[]>;
}

type EligibilityState =
  | { status: "idle" | "loading"; grants: []; errorCode: null }
  | { status: "ready"; grants: GrantMatch[]; errorCode: null }
  | { status: "error"; grants: []; errorCode: "ELIGIBILITY_UNAVAILABLE" };

export function useGrantEligibility(
  profile: ResidentProfile | null,
  matcher: GrantMatcher,
): EligibilityState {
  const [state, setState] = useState<EligibilityState>({
    status: "idle",
    grants: [],
    errorCode: null,
  });

  useEffect(() => {
    if (!profile) {
      setState({ status: "idle", grants: [], errorCode: null });
      return;
    }

    let active = true;
    setState({ status: "loading", grants: [], errorCode: null });
    void matcher
      .matchGrants(profile)
      .then((matches) => {
        if (!active) return;
        const valid = matches.filter(
          (grant) => Number.isFinite(grant.score) && grant.score >= 0 && grant.score <= 1,
        );
        setState({
          status: "ready",
          grants: [...valid].sort((a, b) => b.score - a.score),
          errorCode: null,
        });
      })
      .catch(() => {
        if (active) {
          setState({ status: "error", grants: [], errorCode: "ELIGIBILITY_UNAVAILABLE" });
        }
      });

    return () => {
      active = false;
    };
  }, [profile, matcher]);

  return state;
}
