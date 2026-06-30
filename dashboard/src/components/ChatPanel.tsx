import { useState, useRef, useEffect } from "react";

interface ProcessStep {
  text: string;
  expanded: boolean;
}

interface ChatMessage {
  role: "user" | "assistant";
  content?: string;
  steps?: ProcessStep[];
  projectOptions?: string[];  // For project selection UI
}

let CONFIG: { region: string; runtimeArn: string; graphsBaseUrl?: string } | null = null;
async function getConfig() {
  if (CONFIG) return CONFIG;
  try { const r = await fetch("/config.json"); CONFIG = await r.json(); }
  catch { CONFIG = { region: "us-east-1", runtimeArn: "" }; }
  return CONFIG!;
}

export default function ChatPanel({ token }: { token: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: "Hi! Paste a GitHub URL to analyze a codebase, or ask questions about an existing project." },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const [sessionId] = useState(() => `ua-session-${crypto.randomUUID()}`);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, status]);

  function toggleStep(msgIdx: number, stepIdx: number) {
    setMessages((prev) => {
      const updated = [...prev];
      const msg = { ...updated[msgIdx] };
      if (msg.steps) {
        msg.steps = [...msg.steps];
        msg.steps[stepIdx] = { ...msg.steps[stepIdx], expanded: !msg.steps[stepIdx].expanded };
        updated[msgIdx] = msg;
      }
      return updated;
    });
  }

  function handleProjectSelect(projectId: string) {
    setInput(projectId);
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setLoading(true);
    setStatus("Connecting...");

    setMessages((prev) => [...prev, { role: "assistant", steps: [], content: undefined }]);

    try {
      const config = await getConfig();
      const escapedArn = encodeURIComponent(config.runtimeArn);
      const url = `https://bedrock-agentcore.${config.region}.amazonaws.com/runtimes/${escapedArn}/invocations?qualifier=DEFAULT`;

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
        },
        body: JSON.stringify({ prompt: text }),
      });

      if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).substring(0, 200)}`);

      setStatus("Processing...");
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith("data: ")) continue;
          try {
            let event = JSON.parse(trimmed.substring(6));
            if (typeof event === "string") {
              try { event = JSON.parse(event); } catch { continue; }
            }

            if (event.subtype === "init") { setStatus("Agent initialized..."); continue; }
            if (event.subtype === "hook_started" || event.subtype === "hook_response") continue;
            if (event.subtype === "api_retry") { setStatus("⚠️ Retrying..."); continue; }

            // Tool use → process step
            if (event.subtype === "tool_use" || event.data?.subtype === "tool_use") {
              const name = event.data?.name || event.name || "tool";
              setMessages((prev) => {
                const updated = [...prev];
                const msg = { ...updated[updated.length - 1] };
                msg.steps = [...(msg.steps || []), { text: `🔧 ${name}`, expanded: false }];
                updated[updated.length - 1] = msg;
                return updated;
              });
              setStatus(`🔧 ${name}`);
              continue;
            }

            // Project selection event
            if (event.type === "project_selection" && event.options) {
              setMessages((prev) => {
                const updated = [...prev];
                const msg = { ...updated[updated.length - 1] };
                msg.projectOptions = event.options;
                if (event.content?.[0]?.text) {
                  msg.content = event.content[0].text;
                }
                updated[updated.length - 1] = msg;
                return updated;
              });
              setStatus("");
              continue;
            }

            // Content text → process step
            if (event.content && Array.isArray(event.content)) {
              for (const block of event.content) {
                if (block.thinking) { setStatus("💭 Thinking..."); continue; }
                if (block.text) {
                  setMessages((prev) => {
                    const updated = [...prev];
                    const msg = { ...updated[updated.length - 1] };
                    msg.steps = [...(msg.steps || []), { text: block.text, expanded: false }];
                    updated[updated.length - 1] = msg;
                    return updated;
                  });
                  setStatus("");
                }
              }
              continue;
            }

            // Success → final result
            if (event.subtype === "success" && event.result) {
              setMessages((prev) => {
                const updated = [...prev];
                const msg = { ...updated[updated.length - 1] };
                msg.content = event.result;
                updated[updated.length - 1] = msg;
                return updated;
              });
              setStatus("");
              continue;
            }

            if (event.subtype === "error" || event.is_error) {
              const err = event.data?.error || event.error || "Unknown error";
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: `❌ ${err}` };
                return updated;
              });
              setStatus("");
            }
          } catch { /* skip non-JSON */ }
        }
      }
    } catch (err: any) {
      setMessages((prev) => {
        const updated = [...prev];
        updated[updated.length - 1] = { role: "assistant", content: `Error: ${err.message}` };
        return updated;
      });
    } finally {
      setLoading(false);
      setStatus("");
    }
  }

  return (
    <div className="flex flex-col h-full bg-zinc-950 text-zinc-100">
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.map((msg, i) => (
          <div key={i}>
            {/* User message */}
            {msg.role === "user" && (
              <div className="flex justify-end">
                <div className="max-w-[80%] px-4 py-2 rounded-2xl text-sm bg-blue-600 text-white">
                  {msg.content}
                </div>
              </div>
            )}

            {/* Assistant message */}
            {msg.role === "assistant" && (
              <div className="space-y-2">
                {/* Process steps (collapsible) */}
                {msg.steps && msg.steps.length > 0 && (
                  <div className="ml-2 border-l-2 border-zinc-800 pl-3 space-y-1">
                    <div className="text-[10px] uppercase tracking-wider text-zinc-600 font-medium">Process</div>
                    {msg.steps.map((step, j) => (
                      <div key={j} className="text-xs">
                        <button
                          onClick={() => toggleStep(i, j)}
                          className="text-left w-full hover:bg-zinc-900 rounded px-2 py-1 transition-colors"
                        >
                          <span className="text-zinc-500">{step.expanded ? "▼" : "▶"}</span>{" "}
                          <span className="text-zinc-400">
                            {step.text.length > 80 && !step.expanded
                              ? step.text.substring(0, 80) + "..."
                              : step.expanded ? "" : step.text.substring(0, 80)}
                          </span>
                        </button>
                        {step.expanded && (
                          <div className="ml-5 mt-1 px-2 py-1 bg-zinc-900 rounded text-zinc-300 whitespace-pre-wrap text-xs max-h-60 overflow-y-auto">
                            {step.text}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Project selection options */}
                {msg.projectOptions && msg.projectOptions.length > 0 && (
                  <div className="flex flex-wrap gap-2 ml-2 mt-2">
                    {msg.projectOptions.map((pid) => (
                      <button
                        key={pid}
                        onClick={() => handleProjectSelect(pid)}
                        className="px-3 py-1.5 rounded-lg text-sm bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 hover:border-blue-500 text-zinc-200 transition-colors"
                      >
                        {pid}
                      </button>
                    ))}
                  </div>
                )}

                {/* Final result */}
                {msg.content && (
                  <div className="flex justify-start">
                    <div className="max-w-[80%] px-4 py-2 rounded-2xl text-sm bg-zinc-800 text-zinc-100 whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                )}

                {/* No content yet, still loading */}
                {!msg.content && !msg.steps?.length && !msg.projectOptions?.length && (
                  <div className="text-zinc-500 text-sm px-4">...</div>
                )}
              </div>
            )}
          </div>
        ))}

        {/* Status bar */}
        {status && (
          <div className="sticky bottom-0 bg-zinc-950/90 backdrop-blur-sm py-2">
            <div className="px-3 py-1.5 rounded text-[11px] bg-zinc-900 border border-zinc-800 text-zinc-500 font-mono">
              {status}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-zinc-800 px-4 py-3">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Enter a GitHub URL or ask anything..."
            className="flex-1 bg-zinc-800 text-zinc-100 rounded-xl px-4 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-500 placeholder-zinc-500"
            disabled={loading}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-xl text-sm font-medium transition-colors"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
