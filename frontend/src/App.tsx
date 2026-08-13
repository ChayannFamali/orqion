import { Loader2 } from "lucide-react";
import { useCurrentUser } from "./hooks/useAuth";
import { LoginPage } from "./pages/LoginPage";
import { AppLayout } from "./components/AppLayout";

export default function App() {
  const { data, isLoading, isError } = useCurrentUser();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  if (isError || !data) {
    return <LoginPage />;
  }

  return (
    <AppLayout
      email={data.email}
      capabilities={data.capabilities}
      isImpersonating={data.is_impersonating ?? false}
      impersonatedByEmail={data.impersonated_by_email ?? null}
    />
  );
}
