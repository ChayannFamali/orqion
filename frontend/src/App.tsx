import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useCurrentUser } from "./hooks/useAuth";
import { LoginPage } from "./pages/LoginPage";
import { AppLayout } from "./components/AppLayout";
import { ChangePasswordModal } from "./pages/UsersPage";

export default function App() {
  const { data, isLoading, isError } = useCurrentUser();
  const [passwordChanged, setPasswordChanged] = useState(false);

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

  if (data.must_change_password && !passwordChanged) {
    return (
      <ChangePasswordModal
        onClose={() => setPasswordChanged(true)}
      />
    );
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
