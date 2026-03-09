"use client";

import Image from "next/image";
import Link from "next/link";
import { useSession, signIn, signOut } from "next-auth/react";
import { Button } from "@/components/ui/button";

export function AuthHeader() {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <header className="flex w-full items-center justify-between border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <span className="text-sm text-zinc-500">Loading…</span>
      </header>
    );
  }

  return (
    <header className="flex w-full items-center justify-between border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center gap-4">
        <Link
          href="/"
          className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
        >
          ShipSafe
        </Link>
        {session?.user && (
          <Link
            href="/repos"
            className="text-sm text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
          >
            Repos
          </Link>
        )}
      </div>
      <div className="flex items-center gap-3">
        {session?.user ? (
          <>
            <span className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
              {session.user.image && (
                <Image
                  src={session.user.image}
                  alt=""
                  width={24}
                  height={24}
                  className="rounded-full"
                />
              )}
              {session.user.name ?? session.user.email}
            </span>
            <Button variant="outline" size="sm" onClick={() => signOut()}>
              Sign out
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            onClick={() => signIn("github", { callbackUrl: "/" })}
          >
            Sign in with GitHub
          </Button>
        )}
      </div>
    </header>
  );
}
