"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { Button } from "@/components/ui/button";
import { SHIPSAFE_API_URL } from "@/lib/api";

type Repo = {
  id: number;
  full_name: string;
  name: string;
  private: boolean;
  html_url: string;
  description?: string;
};

export function ReposList() {
  const { data: session, status: sessionStatus } = useSession();
  const [repos, setRepos] = useState<Repo[]>([]);
  const [connectedRepos, setConnectedRepos] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const githubId = session?.user?.id;
  const login = session?.user?.name ?? "";

  const ensureUserAndLoadConnected = useCallback(async () => {
    if (!githubId) return;
    try {
      await fetch(`${SHIPSAFE_API_URL}/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ github_id: githubId, login }),
      });
      const res = await fetch(
        `${SHIPSAFE_API_URL}/users/${encodeURIComponent(githubId)}/connected-repos`
      );
      if (res.ok) {
        const data = await res.json();
        setConnectedRepos(new Set((data.repos as string[]) ?? []));
      }
    } catch {
      setConnectedRepos(new Set());
    }
  }, [githubId, login]);

  useEffect(() => {
    if (sessionStatus !== "authenticated" || !githubId) {
      setLoading(sessionStatus === "loading");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await ensureUserAndLoadConnected();
        if (cancelled) return;
        const res = await fetch("/api/repos");
        if (!res.ok) throw new Error("Failed to load repos");
        const data = await res.json();
        if (!cancelled) {
          setRepos(data.repos ?? []);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionStatus, githubId, ensureUserAndLoadConnected]);

  const connect = async (repoFullName: string) => {
    if (!githubId) return;
    setActing(repoFullName);
    try {
      const res = await fetch(
        `${SHIPSAFE_API_URL}/users/${encodeURIComponent(githubId)}/connected-repos`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo_full_name: repoFullName }),
        }
      );
      if (res.ok) {
        setConnectedRepos((prev) => new Set(prev).add(repoFullName));
      }
    } finally {
      setActing(null);
    }
  };

  const disconnect = async (repoFullName: string) => {
    if (!githubId) return;
    setActing(repoFullName);
    try {
      const res = await fetch(
        `${SHIPSAFE_API_URL}/users/${encodeURIComponent(githubId)}/connected-repos?repo_full_name=${encodeURIComponent(repoFullName)}`,
        { method: "DELETE" }
      );
      if (res.ok) {
        setConnectedRepos((prev) => {
          const next = new Set(prev);
          next.delete(repoFullName);
          return next;
        });
      }
    } finally {
      setActing(null);
    }
  };

  if (sessionStatus === "loading" || (sessionStatus === "authenticated" && loading)) {
    return (
      <div className="mt-6 text-zinc-500 dark:text-zinc-400">
        Loading repositories…
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

  if (repos.length === 0) {
    return (
      <div className="mt-6 text-zinc-600 dark:text-zinc-400">
        No repositories found. Create a repo on GitHub or check your permissions.
      </div>
    );
  }

  return (
    <ul className="mt-6 space-y-2">
      {repos.map((repo) => {
        const isConnected = connectedRepos.has(repo.full_name);
        const isActing = acting === repo.full_name;
        const buttonLabel = isActing
          ? "…"
          : isConnected
            ? "Disconnect"
            : "Connect";
        return (
          <li
            key={repo.id}
            className="flex flex-col gap-1 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <Link
                  href={repo.html_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-zinc-900 hover:underline dark:text-zinc-50"
                >
                  {repo.full_name}
                </Link>
                {repo.private && (
                  <span className="ml-2 text-xs text-zinc-500 dark:text-zinc-400">
                    private
                  </span>
                )}
              </div>
              <Button
                variant={isConnected ? "secondary" : "outline"}
                size="sm"
                disabled={isActing}
                onClick={() =>
                  isConnected ? disconnect(repo.full_name) : connect(repo.full_name)
                }
              >
                {buttonLabel}
              </Button>
            </div>
            {repo.description && (
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                {repo.description}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
