import { getToken } from "next-auth/jwt";
import type { NextRequest } from "next/server";

export type GitHubRepo = {
  id: number;
  full_name: string;
  name: string;
  private: boolean;
  html_url: string;
  description: string | null;
};

export async function GET(req: NextRequest) {
  const token = await getToken({
    req,
    secret: process.env.AUTH_SECRET,
  });

  const accessToken =
    token && typeof token.access_token === "string" ? token.access_token : null;
  if (!accessToken) {
    return Response.json(
      { error: "Not authenticated or no GitHub token" },
      { status: 401 }
    );
  }

  try {
    const res = await fetch("https://api.github.com/user/repos?per_page=100&sort=updated", {
      headers: {
        Accept: "application/vnd.github.v3+json",
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (!res.ok) {
      const text = await res.text();
      return Response.json(
        { error: "GitHub API error", details: text },
        { status: res.status }
      );
    }

    const data = (await res.json()) as GitHubRepo[];
    const repos = data.map((r) => ({
      id: r.id,
      full_name: r.full_name,
      name: r.name,
      private: r.private,
      html_url: r.html_url,
      description: r.description ?? undefined,
    }));

    return Response.json({ repos });
  } catch (e) {
    return Response.json(
      { error: "Failed to fetch repos", details: String(e) },
      { status: 500 }
    );
  }
}
