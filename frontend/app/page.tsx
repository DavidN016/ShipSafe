import Link from "next/link";
import { AuthHeader } from "@/components/auth-header";

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 font-sans dark:bg-zinc-950">
      <AuthHeader />
      <main className="flex flex-1 flex-col items-center justify-center px-4 py-16">
        <div className="flex max-w-md flex-col items-center gap-8 text-center">
          <h1 className="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            ShipSafe
          </h1>
          <p className="text-lg leading-relaxed text-zinc-600 dark:text-zinc-400">
            Event-driven security orchestrator. Sign in with GitHub, then choose
            which repos to connect for security scanning.
          </p>
          <Link
            href="/repos"
            className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
          >
            Go to Repos
          </Link>
        </div>
      </main>
    </div>
  );
}
