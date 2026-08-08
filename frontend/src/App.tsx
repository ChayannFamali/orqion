import { useHealth } from "./hooks/useHealth";

export default function App() {
  const { data, isLoading, isError } = useHealth();

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center text-muted-foreground">Loading…</div>;
  }

  if (isError) {
    return <div className="flex h-screen items-center justify-center text-destructive">orqion: unavailable</div>;
  }

  return (
    <div className="flex h-screen items-center justify-center">
      <h1 className="text-2xl font-bold text-foreground">orqion: {data?.status ?? "unknown"}</h1>
    </div>
  );
}
