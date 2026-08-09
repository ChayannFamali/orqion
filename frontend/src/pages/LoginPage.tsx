import { useState, type FormEvent } from "react";
import { useLogin } from "../hooks/useAuth";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import type { ApiError } from "../api/types";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;
    login.mutate({ email, password });
  };

  const error: ApiError | null = login.error as ApiError | null;

  return (
    <div className="flex h-screen items-center justify-center">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-background p-8"
      >
        <h1 className="text-2xl font-bold text-foreground">orqion</h1>

        <div className="space-y-2">
          <label htmlFor="email" className="text-sm font-medium text-foreground">
            Email
          </label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
            required
            autoFocus
          />
        </div>

        <div className="space-y-2">
          <label htmlFor="password" className="text-sm font-medium text-foreground">
            Пароль
          </label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
          />
        </div>

        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error.reason}
            {error.hint && <span className="block text-xs opacity-70">{error.hint}</span>}
          </p>
        )}

        <Button
          type="submit"
          className="w-full"
          disabled={login.isPending || !email || !password}
        >
          {login.isPending ? "Вход…" : "Войти"}
        </Button>
      </form>
    </div>
  );
}
