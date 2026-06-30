import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import ChatPanel from "./components/ChatPanel";
import LoginPage from "./components/LoginPage";
import { useAuth } from "./hooks/useAuth";

function Root() {
  const [tab, setTab] = useState<"chat" | "dashboard">("chat");
  const { token, loading, error, signIn, signOut, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-zinc-950 text-zinc-400">
        Loading...
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage onLogin={signIn} error={error} />;
  }

  return (
    <div className="h-screen flex flex-col bg-zinc-950">
      {/* Tab Bar */}
      <div className="flex items-center border-b border-zinc-800 px-4">
        <span className="text-zinc-100 font-semibold text-sm mr-6 py-3">
          understand-anything
        </span>
        <button
          onClick={() => setTab("chat")}
          className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
            tab === "chat"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          💬 Chat
        </button>
        <button
          onClick={() => setTab("dashboard")}
          className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
            tab === "dashboard"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          📊 Dashboard
        </button>
        <div className="ml-auto">
          <button
            onClick={signOut}
            className="text-xs text-zinc-500 hover:text-zinc-300 py-2 px-3"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-hidden">
        {tab === "chat" ? <ChatPanel token={token!} /> : <App />}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
