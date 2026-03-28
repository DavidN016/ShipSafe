"use client";

import { useEffect, useMemo, useState } from "react";
import { SHIPSAFE_API_URL } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function LocalPrepushInstructions() {
  const [origin, setOrigin] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  const installCommand = useMemo(() => {
    if (!origin) return "";
    return `SHIPSAFE_API_URL=${SHIPSAFE_API_URL} curl -fsSL ${origin}/install.sh | bash`;
  }, [origin]);

  const copy = async (label: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(null);
    }
  };

  return (
    <section
      className="mt-8 rounded-lg border border-zinc-200 bg-white p-5 text-left dark:border-zinc-800 dark:bg-zinc-900"
      aria-labelledby="local-prepush-heading"
    >
      <h2
        id="local-prepush-heading"
        className="text-lg font-semibold text-zinc-900 dark:text-zinc-50"
      >
        Run ShipSafe from your machine (pre-push)
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
        Connect a repo above for GitHub webhooks. To block risky commits before they leave your
        laptop, install the git pre-push hook in each local clone. The hook sends the push diff to{" "}
        <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">
          {SHIPSAFE_API_URL}
        </code>
        .
      </p>

      <ol className="mt-4 list-decimal space-y-3 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
        <li>
          Open a terminal in the root of your git repository (
          <code className="font-mono text-xs">cd path/to/your-repo</code>).
        </li>
        <li>
          <span className="font-medium text-zinc-900 dark:text-zinc-100">
            Install the hook (required once per clone)
          </span>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-start">
            <pre className="min-w-0 flex-1 overflow-x-auto rounded-md bg-zinc-950 p-3 font-mono text-[13px] leading-relaxed text-zinc-100">
              {origin ? (
                installCommand
              ) : (
                <span className="text-zinc-500">
                  SHIPSAFE_API_URL={SHIPSAFE_API_URL} curl -fsSL &lt;this-app&gt;/install.sh | bash
                </span>
              )}
            </pre>
            {origin && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => copy("install", installCommand)}
              >
                {copied === "install" ? "Copied" : "Copy"}
              </Button>
            )}
          </div>
          <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-500">
            This creates <code className="font-mono">.shipsafe/hooks/pre-push</code> and runs{" "}
            <code className="font-mono">git config core.hooksPath .shipsafe/hooks</code> for this
            repo. Commit <code className="font-mono">.shipsafe/</code> if you want the same hook for
            teammates after clone.
          </p>
        </li>
        <li>
          <span className="font-medium text-zinc-900 dark:text-zinc-100">
            Environment variables you may need
          </span>
          <dl className="mt-2 space-y-2 border-l-2 border-zinc-200 pl-3 dark:border-zinc-700">
            <div>
              <dt className="font-mono text-xs text-zinc-800 dark:text-zinc-200">
                SHIPSAFE_API_URL
              </dt>
              <dd className="text-xs text-zinc-600 dark:text-zinc-400">
                Base URL of the ShipSafe API. Should match this app&apos;s backend (
                <code className="font-mono">{SHIPSAFE_API_URL}</code>
                ). Set at install time (see command) or per shell session to override the value
                baked into the hook.
              </dd>
            </div>
            <div>
              <dt className="font-mono text-xs text-zinc-800 dark:text-zinc-200">
                SHIPSAFE_TOKEN
              </dt>
              <dd className="text-xs text-zinc-600 dark:text-zinc-400">
                Required when the server enables <code className="font-mono">SHIPSAFE_PREPUSH_TOKEN</code>
                : use the same secret value as the Bearer token. The hook sends{" "}
                <code className="font-mono">Authorization: Bearer …</code> to{" "}
                <code className="font-mono">/hooks/prepush</code>.
              </dd>
            </div>
            <div>
              <dt className="font-mono text-xs text-zinc-800 dark:text-zinc-200">
                SHIPSAFE_SKIP_PREPUSH
              </dt>
              <dd className="text-xs text-zinc-600 dark:text-zinc-400">
                Set to <code className="font-mono">1</code> for a single push to skip the hook (for
                example: <code className="font-mono">SHIPSAFE_SKIP_PREPUSH=1 git push</code>).
              </dd>
            </div>
          </dl>
        </li>
      </ol>

      <p className="mt-4 text-xs text-zinc-500 dark:text-zinc-500">
        Requires <code className="font-mono">curl</code> and <code className="font-mono">python3</code>{" "}
        on your PATH. The hook POSTs your diff to the API; pushes are blocked when the auditor
        confirms findings.
      </p>
    </section>
  );
}
