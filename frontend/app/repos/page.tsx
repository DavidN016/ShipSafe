import { redirect } from "next/navigation";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { AuthHeader } from "@/components/auth-header";
import { LocalPrepushInstructions } from "@/components/local-prepush-instructions";
import { ReposList } from "@/components/repos-list";

export default async function ReposPage() {
  const session = await getServerSession(authOptions);
  if (!session) {
    redirect("/");
  }

  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 font-sans dark:bg-zinc-950">
      <AuthHeader />
      <main className="flex-1 px-4 py-8">
        <div className="mx-auto max-w-2xl">
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
            Your repositories
          </h1>
          <p className="mt-1 text-zinc-600 dark:text-zinc-400">
            Choose which repos to connect with ShipSafe for security scanning.
          </p>
          <LocalPrepushInstructions />
          <ReposList />
        </div>
      </main>
    </div>
  );
}
