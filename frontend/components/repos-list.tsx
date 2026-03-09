"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

type Repo = {
  id: number;
  full_name: string;
  name: string;
  private: boolean;
  html_url: string;
  description?: string;
};

export function ReposList() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/repos")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load repos");
        return res.json();
      })
      .then((data: { repos: Repo[] }) => {
        setRepos(data.repos);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
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
      {repos.map((repo) => (
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
            <Button variant="outline" size="sm" disabled>
              Connect (coming soon)
            </Button>
          </div>
          {repo.description && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {repo.description}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
