import { useCurrentUser } from "./hooks/useAuth";
import { LoginPage } from "./pages/LoginPage";
import { ChatPage } from "./pages/ChatPage";

export default function App() {
  const { data, isLoading, isError } = useCurrentUser();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-muted-foreground">
        Loading…
      </div>
    );
  }

  if (isError || !data) {
    return <LoginPage />;
  }

  return <ChatPage />;
}
