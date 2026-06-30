import { useState } from "react";

interface LoginPageProps {
  onLogin: (username: string, password: string) => Promise<void>;
  error: string | null;
}

export default function LoginPage({ onLogin, error }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onLogin(username, password);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-zinc-950">
      <form onSubmit={handleSubmit} className="w-80 space-y-4">
        <h1 className="text-xl font-semibold text-zinc-100 text-center">
          understand-anything
        </h1>
        <p className="text-sm text-zinc-400 text-center">Sign in to continue</p>

        {error && (
          <div className="px-3 py-2 bg-red-900/30 border border-red-800 rounded-lg text-red-200 text-xs">
            {error}
          </div>
        )}

        <input
          type="email"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Email"
          className="w-full bg-zinc-800 text-zinc-100 rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-zinc-500"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full bg-zinc-800 text-zinc-100 rounded-lg px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-zinc-500"
          required
        />
        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
