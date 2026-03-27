"use client";

import { useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";
import { SHIPSAFE_API_URL } from "@/lib/api";

type Vulnerability = {
  type?: string;
  line_number?: number;
  description?: string;
  confidence_score?: number;
};

type FindingResult = {
  file_path: string;
  auditor_confirmed_vulnerable: boolean;
  vulnerabilities: Vulnerability[];
  audit_feedback?: string | null;
  remediation_patch?: string | null;
};

type FindingRun = {
  id: number;
  source: string;
  repository?: string | null;
  commit_sha?: string | null;
  created_at?: string | null;
  results: FindingResult[];
};

export function FindingsList() {
  const { data: session, status } = useSession();
  const [runs, setRuns] = useState<FindingRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const githubId = session?.user?.id;

  useEffect(() => {
    if (status !== "authenticated" || !githubId) {
      setLoading(status === "loading");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `${SHIPSAFE_API_URL}/users/${encodeURIComponent(githubId)}/findings`
        );
        if (!res.ok) throw new Error("Failed to load findings");
        const data = (await res.json()) as { runs?: FindingRun[] };
        if (!cancelled) {
          setRuns(data.runs ?? []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load findings");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status, githubId]);

  const flaggedRuns = useMemo(
    () =>
      runs.filter((run) =>
        run.results.some((r) => r.auditor_confirmed_vulnerable)
      ),
    [runs]
  );

  if (loading) {
    return (
      <div className="mt-6 text-zinc-500 dark:text-zinc-400">
        Loading findings...
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-red-800 dark:border-red-900 dark:bg-red-950/50 dark:text-red-200">
        {error}
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="mt-6 text-zinc-600 dark:text-zinc-400">
        No scans yet. Connect a repo and run a push/PR scan first.
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-3">
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Showing {runs.length} recent scan run(s), {flaggedRuns.length} with confirmed findings.
      </p>
      {runs.map((run) => {
        const risky = run.results.filter((r) => r.auditor_confirmed_vulnerable);
        return (
          <section
            key={run.id}
            className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="font-medium text-zinc-900 dark:text-zinc-50">
                {run.repository ?? "unknown-repo"}
              </span>
              <span className="text-zinc-500 dark:text-zinc-400">{run.source}</span>
              {run.commit_sha && (
                <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">
                  {run.commit_sha.slice(0, 10)}
                </code>
              )}
              {run.created_at && (
                <span className="text-zinc-500 dark:text-zinc-400">
                  {new Date(run.created_at).toLocaleString()}
                </span>
              )}
            </div>

            {risky.length === 0 ? (
              <p className="text-sm text-emerald-700 dark:text-emerald-400">
                No auditor-confirmed vulnerabilities.
              </p>
            ) : (
              <ul className="space-y-2">
                {risky.map((result, idx) => (
                  <li
                    key={`${run.id}-${idx}-${result.file_path}`}
                    className="rounded border border-amber-200 bg-amber-50 p-3 dark:border-amber-900/40 dark:bg-amber-950/30"
                  >
                    <p className="mb-1 text-sm font-medium text-zinc-900 dark:text-zinc-50">
                      {result.file_path}
                    </p>
                    <ul className="space-y-1 text-sm text-zinc-700 dark:text-zinc-300">
                      {result.vulnerabilities.map((v, i) => (
                        <li key={`${run.id}-${idx}-v-${i}`}>
                          {(v.type || "Finding") + ": "}
                          {v.description || "No description"}
                          {typeof v.line_number === "number"
                            ? ` (line ${v.line_number})`
                            : ""}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
