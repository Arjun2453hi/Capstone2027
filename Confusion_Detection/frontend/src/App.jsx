import { useState, useRef, useEffect } from "react";

const API = "http://localhost:8000";

/* ── palette ──────────────────────────────────────────────────────────── */
const C = {
  bg:        "#0f1117",
  surface:   "#1a1d27",
  border:    "#2a2d3a",
  accent:    "#6c8fff",
  accentDim: "#3d5acc",
  user:      "#1e2d4a",
  assistant: "#1a1d27",
  text:      "#e2e4f0",
  muted:     "#6b7094",
  success:   "#4caf82",
  error:     "#e05c5c",
};

/* ── tiny style helpers ───────────────────────────────────────────────── */
const s = {
  app: {
    display: "flex", flexDirection: "column", height: "100vh",
    background: C.bg, color: C.text,
    fontFamily: "'Inter', 'Segoe UI', sans-serif", fontSize: 14,
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "14px 20px", borderBottom: `1px solid ${C.border}`,
    background: C.surface,
  },
  headerLeft: { display: "flex", alignItems: "center", gap: 10 },
  dot: {
    width: 8, height: 8, borderRadius: "50%", background: C.accent,
    boxShadow: `0 0 6px ${C.accent}`,
  },
  title: { fontWeight: 600, fontSize: 15, letterSpacing: 0.3 },
  processBtn: (busy) => ({
    padding: "7px 16px", borderRadius: 6, border: "none", cursor: busy ? "not-allowed" : "pointer",
    background: busy ? C.accentDim : C.accent, color: "#fff",
    fontWeight: 600, fontSize: 13, opacity: busy ? 0.7 : 1,
    transition: "background 0.15s",
  }),
  messages: {
    flex: 1, overflowY: "auto", padding: "20px 0",
    display: "flex", flexDirection: "column", gap: 2,
  },
  bubble: (role) => ({
    maxWidth: 680, margin: "0 auto", width: "100%",
    padding: "10px 20px",
  }),
  bubbleInner: (role) => ({
    padding: "10px 14px", borderRadius: 10,
    background: role === "user" ? C.user : C.assistant,
    border: `1px solid ${role === "user" ? "#2a3f6a" : C.border}`,
    lineHeight: 1.65, whiteSpace: "pre-wrap", wordBreak: "break-word",
  }),
  roleLabel: (role) => ({
    fontSize: 11, fontWeight: 600, letterSpacing: 0.8,
    color: role === "user" ? C.accent : C.muted,
    marginBottom: 4, textTransform: "uppercase",
    textAlign: role === "user" ? "right" : "left",
  }),
  typing: {
    padding: "10px 20px", maxWidth: 680, margin: "0 auto", width: "100%",
  },
  typingDots: {
    display: "inline-flex", gap: 4, padding: "10px 14px",
    background: C.surface, borderRadius: 10, border: `1px solid ${C.border}`,
  },
  dot2: (i) => ({
    width: 6, height: 6, borderRadius: "50%", background: C.muted,
    animation: `bounce 1.2s ${i * 0.2}s infinite`,
  }),
  statusBar: {
    padding: "6px 20px", fontSize: 12, color: C.muted,
    borderTop: `1px solid ${C.border}`, minHeight: 26,
    background: C.surface,
  },
  inputRow: {
    display: "flex", gap: 8, padding: "12px 20px",
    borderTop: `1px solid ${C.border}`, background: C.surface,
    maxWidth: 720, margin: "0 auto", width: "100%",
    boxSizing: "border-box",
  },
  textarea: {
    flex: 1, background: C.bg, color: C.text,
    border: `1px solid ${C.border}`, borderRadius: 8,
    padding: "9px 12px", fontSize: 14, resize: "none",
    fontFamily: "inherit", lineHeight: 1.5, outline: "none",
    minHeight: 42, maxHeight: 140,
  },
  sendBtn: (disabled) => ({
    padding: "0 18px", borderRadius: 8, border: "none",
    background: disabled ? C.border : C.accent,
    color: disabled ? C.muted : "#fff",
    cursor: disabled ? "not-allowed" : "pointer",
    fontWeight: 600, fontSize: 14, flexShrink: 0,
    transition: "background 0.15s",
  }),
  clearBtn: {
    padding: "0 12px", borderRadius: 8, border: `1px solid ${C.border}`,
    background: "transparent", color: C.muted,
    cursor: "pointer", fontSize: 13, flexShrink: 0,
  },
  empty: {
    flex: 1, display: "flex", flexDirection: "column",
    alignItems: "center", justifyContent: "center", gap: 8, color: C.muted,
  },
  emptyIcon: { fontSize: 32, marginBottom: 4 },
};

/* ── keyframes injected once ─────────────────────────────────────────── */
const injectStyles = () => {
  if (document.getElementById("chat-keyframes")) return;
  const el = document.createElement("style");
  el.id = "chat-keyframes";
  el.textContent = `
    @keyframes bounce {
      0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
      40%            { transform: translateY(-5px); opacity: 1; }
    }
    ::-webkit-scrollbar { width: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #2a2d3a; border-radius: 4px; }
    * { box-sizing: border-box; }
  `;
  document.head.appendChild(el);
};

/* ── component ───────────────────────────────────────────────────────── */
export default function App() {
  const [messages, setMessages] = useState([]);   // { role, content }
  const [input, setInput]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [processing, setProcessing] = useState(false);
  const [status, setStatus]     = useState("");
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => { injectStyles(); }, []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  /* auto-grow textarea */
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  }, [input]);

  /* ── send chat message ─────────────────────────────────────────────── */
  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setMessages(prev => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    setStatus("Thinking…");

    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Server error");
      const data = await res.json();
      setMessages(prev => [...prev, { role: "assistant", content: data.content }]);
      setStatus("");
    } catch (err) {
      setStatus(`Error: ${err.message}`);
      setMessages(prev => [...prev, { role: "assistant", content: `⚠️ ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  };

  /* ── trigger gap detection pipeline ───────────────────────────────── */
  const runProcess = async () => {
    if (processing) return;
    setProcessing(true);
    setStatus("Starting gap detection pipeline…");

    try {
      const res = await fetch(`${API}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),   // uses hardcoded paths on server
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Server error");
      const data = await res.json();
      setStatus(`✓ ${data.message}`);
    } catch (err) {
      setStatus(`Process error: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  };

  /* ── clear history ─────────────────────────────────────────────────── */
  const clearChat = async () => {
    setMessages([]);
    setStatus("");
    await fetch(`${API}/history`, { method: "DELETE" }).catch(() => {});
  };

  const canSend = input.trim().length > 0 && !loading;

  return (
    <div style={s.app}>

      {/* header */}
      <div style={s.header}>
        <div style={s.headerLeft}>
          <div style={s.dot} />
          <span style={s.title}>Capstone Chat</span>
        </div>
        <button style={s.processBtn(processing)} onClick={runProcess} disabled={processing}>
          {processing ? "Processing…" : "Process"}
        </button>
      </div>

      {/* messages */}
      <div style={s.messages}>
        {messages.length === 0 && !loading && (
          <div style={s.empty}>
            <div style={s.emptyIcon}>💬</div>
            <span>Ask anything about your slides</span>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} style={s.bubble(m.role)}>
            <div style={s.roleLabel(m.role)}>{m.role === "user" ? "You" : "Assistant"}</div>
            <div style={s.bubbleInner(m.role)}>{m.content}</div>
          </div>
        ))}

        {/* typing indicator */}
        {loading && (
          <div style={s.typing}>
            <div style={s.roleLabel("assistant")}>Assistant</div>
            <div style={s.typingDots}>
              {[0, 1, 2].map(i => <div key={i} style={s.dot2(i)} />)}
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* status bar */}
      {status && <div style={s.statusBar}>{status}</div>}

      {/* input */}
      <div style={{ borderTop: `1px solid ${C.border}`, background: C.surface }}>
        <div style={s.inputRow}>
          <textarea
            ref={textareaRef}
            style={s.textarea}
            value={input}
            placeholder="Ask a question…"
            rows={1}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
          />
          <button style={s.clearBtn} onClick={clearChat} title="Clear chat">✕</button>
          <button style={s.sendBtn(!canSend)} onClick={send} disabled={!canSend}>Send</button>
        </div>
      </div>

    </div>
  );
}