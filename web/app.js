// CharLUFS — drag-and-drop UI, vanilla React (no JSX, no Babel runtime).
//
// JS calls Python via the pywebview bridge:
//   await pywebview.api.get_initial_state()
//   await pywebview.api.set_target_lufs(value)
//   await pywebview.api.start_processing()
//   await pywebview.api.clear_queue()
//   await pywebview.api.remove_from_queue(index)
//   await pywebview.api.reveal_in_finder(path)
//   await pywebview.api.copy_log(text)
// Python pushes events into JS via CustomEvents:
//   charlufs:status  { kind, file?, out_lufs?, in_lufs?, text? }
//   charlufs:queue   [ { name, path, status, measured_in?, measured_out?, ... } ]
//   charlufs:counter <n>
//   charlufs:log     "<formatted log line>"
//
// Drop handling note: WKWebView strips file paths from the JS drop event,
// so the actual queueing happens in Python via a native AppKit drag hook
// (see src/webapp.py). JS only renders the drag-over visual.

(function () {
  const h = React.createElement;
  const { useState, useEffect, useRef, useMemo } = React;

  const THEME = {
    bg: "#1C1D20",
    bgRaised: "#23252A",
    bgSunken: "#16171A",
    border: "rgba(255,255,255,0.07)",
    borderSoft: "rgba(255,255,255,0.04)",
    text: "#EDEAE3",
    textDim: "rgba(237,234,227,0.60)",
    textFaint: "rgba(237,234,227,0.38)",
    success: "#6FA37C",
    error: "#B85A4A",
    idle: "rgba(237,234,227,0.45)",
    logBg: "#121316",
    logText: "#C9C5BB",
    accent: "#C97A3F",
    dropTint: "rgba(201,122,63,0.10)",
  };

  const LUFS_MIN = -23;
  const LUFS_MAX = -8;
  const LUFS_STEP = 0.5;
  const LUFS_DEFAULT = -16;

  const PRESETS = [
    { value: -23, label: "EBU R128" },
    { value: -19, label: "Audible" },
    { value: -16, label: "Podcast" },
    { value: -14, label: "Spotify" },
    { value: -10, label: "Loud" },
    { value: -8,  label: "Loud as fuck" },
  ];

  const CHARLIE_MIN_PX = 60;
  const CHARLIE_MAX_PX = 100;
  const CHARLIE_BASE_PX = 120;

  function charlieScale(lufs) {
    const t = (lufs - LUFS_MIN) / (LUFS_MAX - LUFS_MIN);
    const px = CHARLIE_MIN_PX + t * (CHARLIE_MAX_PX - CHARLIE_MIN_PX);
    return px / CHARLIE_BASE_PX;
  }

  function snapLufs(v) {
    let snapped = Math.round(v * 2) / 2;
    if (snapped < LUFS_MIN) snapped = LUFS_MIN;
    if (snapped > LUFS_MAX) snapped = LUFS_MAX;
    return snapped;
  }

  function Section(props) {
    return h("div", {
      style: Object.assign({
        paddingBottom: 10,
        marginBottom: 12,
        borderBottom: `1px solid ${THEME.borderSoft}`,
      }, props.style || {}),
    }, props.children);
  }

  function SectionLabel(props) {
    return h("div", {
      style: {
        fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6,
        color: THEME.textFaint, fontWeight: 600, marginBottom: 6,
      },
    }, props.children);
  }

  function StatusDot(props) {
    return h("span", {
      style: {
        position: "relative",
        width: 10, height: 10,
        display: "inline-block",
      },
    },
      h("span", {
        style: {
          position: "absolute", inset: 0, borderRadius: "50%",
          background: props.color,
          animation: props.pulsing ? "charlufs-pulse 1.4s ease-out infinite" : "none",
        },
      })
    );
  }

  function LufsSlider(props) {
    const { value, onChange, onDragStart, onDragEnd } = props;
    const pct = ((value - LUFS_MIN) / (LUFS_MAX - LUFS_MIN)) * 100;
    return h("div", { style: { position: "relative", height: 26 } },
      h("div", {
        style: {
          position: "absolute", left: 0, right: 0, top: "50%",
          height: 5, marginTop: -2.5,
          background: THEME.bgSunken,
          borderRadius: 999,
          border: `0.5px solid ${THEME.borderSoft}`,
        },
      }),
      h("div", {
        style: {
          position: "absolute", left: 0, top: "50%",
          height: 5, marginTop: -2.5,
          width: `${pct}%`,
          background: `linear-gradient(90deg, ${THEME.accent}80, ${THEME.accent})`,
          borderRadius: 999,
          transition: "width 80ms ease",
        },
      }),
      ...PRESETS.map((p) => {
        const tp = ((p.value - LUFS_MIN) / (LUFS_MAX - LUFS_MIN)) * 100;
        return h("div", {
          key: p.value,
          style: {
            position: "absolute", left: `${tp}%`, top: "50%",
            width: 1, height: 9, marginTop: -4.5, marginLeft: -0.5,
            background: THEME.textFaint,
            opacity: 0.5,
            pointerEvents: "none",
          },
        });
      }),
      h("div", {
        style: {
          position: "absolute", left: `${pct}%`, top: "50%",
          width: 18, height: 18, marginLeft: -9, marginTop: -9,
          borderRadius: "50%",
          background: THEME.bgRaised,
          boxShadow: `0 0 0 1.5px ${THEME.accent}, 0 2px 6px rgba(0,0,0,0.35)`,
          pointerEvents: "none",
        },
      }),
      h("input", {
        type: "range",
        min: LUFS_MIN, max: LUFS_MAX, step: LUFS_STEP,
        value: value,
        onChange: (e) => onChange(parseFloat(e.target.value)),
        onMouseDown: onDragStart, onMouseUp: onDragEnd,
        onTouchStart: onDragStart, onTouchEnd: onDragEnd,
        className: "lufs-input",
      })
    );
  }

  // ── Queue item row ──
  function QueueItem(props) {
    const { item, index, onRemove, onReveal, onRecover } = props;
    const status = item.status;

    let dotColor = THEME.idle;
    let pulsing = false;
    let detail = "";

    if (status === "pending") {
      dotColor = THEME.textFaint;
      detail = "Pending";
    } else if (status === "processing") {
      dotColor = THEME.accent;
      pulsing = true;
      detail = "Processing…";
    } else if (status === "done") {
      dotColor = THEME.success;
      detail = item.measured_out != null
        ? `${item.measured_out.toFixed(1)} LUFS`
        : "Done";
    } else if (status === "error") {
      dotColor = THEME.error;
      detail = item.error || "Error";
    }

    const removable = status !== "processing";
    const recoverable = status === "done" && item.recoverable;

    return h("div", {
      style: {
        display: "flex", alignItems: "center", gap: 10,
        padding: "6px 8px",
        borderRadius: 6,
        background: status === "processing" ? "rgba(201,122,63,0.06)" : "transparent",
      },
    },
      h(StatusDot, { color: dotColor, pulsing }),
      h("div", {
        onClick: () => onReveal(item.path),
        title: item.path,
        style: {
          flex: 1, minWidth: 0,
          fontSize: 12.5, color: THEME.text,
          fontFamily: '"SF Mono", ui-monospace, Menlo, monospace',
          fontVariantNumeric: "tabular-nums",
          textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap",
          cursor: "pointer",
        },
      }, item.name),
      h("div", {
        style: {
          fontSize: 11, color: THEME.textDim,
          fontVariantNumeric: "tabular-nums",
          flexShrink: 0,
        },
      }, detail),
      recoverable && h("button", {
        onClick: () => onRecover(item.path, item.name),
        title: "Restore the original from CharBackup, deleting the normalized file",
        style: {
          background: "transparent",
          border: `0.5px solid ${THEME.border}`,
          borderRadius: 4,
          padding: "2px 8px",
          color: THEME.textDim, fontSize: 10.5, cursor: "pointer",
          fontWeight: 600, letterSpacing: 0.3,
          textTransform: "uppercase",
        },
      }, "Recover"),
      removable && h("button", {
        onClick: () => onRemove(index),
        title: status === "done"
          ? "Forget this entry (file stays as-is, backup is kept on disk)"
          : "Remove from queue",
        style: {
          background: "transparent", border: "none", padding: "2px 6px",
          color: THEME.textFaint, fontSize: 13, cursor: "pointer",
          lineHeight: 1,
        },
      }, "×")
    );
  }

  // ── bridge helpers ──
  function bridgeReady() {
    return new Promise((resolve) => {
      if (window.pywebview && window.pywebview.api) {
        resolve();
        return;
      }
      window.addEventListener("pywebviewready", () => resolve(), { once: true });
    });
  }

  // ── App ──
  function App() {
    const [lufs, setLufs] = useState(LUFS_DEFAULT);
    const [status, setStatus] = useState({ kind: "idle" });
    const [queue, setQueue] = useState([]);
    const [processed, setProcessed] = useState(0);
    const [log, setLog] = useState([]);
    const [dragOver, setDragOver] = useState(false);
    const [copyFlash, setCopyFlash] = useState(false);
    const dragCounter = useRef(0);
    const logRef = useRef(null);

    useEffect(() => {
      let cancelled = false;
      (async () => {
        await bridgeReady();
        try {
          const state = await window.pywebview.api.get_initial_state();
          if (cancelled) return;
          if (typeof state.target_lufs === "number") setLufs(state.target_lufs);
          if (Array.isArray(state.queue)) setQueue(state.queue);
          if (Array.isArray(state.log)) {
            setLog(state.log.map((line) => parseLogLine(line)));
          }
          if (typeof state.files_processed === "number") {
            setProcessed(state.files_processed);
          }
        } catch (e) {
          console.error("get_initial_state failed", e);
        }
      })();
      return () => { cancelled = true; };
    }, []);

    useEffect(() => {
      const onStatus = (e) => setStatus(e.detail);
      const onQueue = (e) => setQueue(e.detail || []);
      const onCounter = (e) => setProcessed(e.detail);
      const onLog = (e) => {
        setLog((l) => l.concat([parseLogLine(e.detail)]).slice(-200));
      };
      const onDragReset = () => {
        dragCounter.current = 0;
        setDragOver(false);
      };
      window.addEventListener("charlufs:status", onStatus);
      window.addEventListener("charlufs:queue", onQueue);
      window.addEventListener("charlufs:counter", onCounter);
      window.addEventListener("charlufs:log", onLog);
      window.addEventListener("charlufs:drag_reset", onDragReset);
      return () => {
        window.removeEventListener("charlufs:status", onStatus);
        window.removeEventListener("charlufs:queue", onQueue);
        window.removeEventListener("charlufs:counter", onCounter);
        window.removeEventListener("charlufs:log", onLog);
        window.removeEventListener("charlufs:drag_reset", onDragReset);
      };
    }, []);

    useEffect(() => {
      if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [log]);

    function onLufsChange(raw) {
      const v = snapLufs(parseFloat(raw));
      setLufs(v);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.set_target_lufs(v).catch(() => {});
      }
    }

    async function onStart() {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.start_processing();
      }
    }
    async function onClear() {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.clear_queue();
      }
    }
    async function onRemove(index) {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.remove_from_queue(index);
      }
    }
    async function onReveal(path) {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.reveal_in_finder(path);
      }
    }
    async function onRecover(path, name) {
      const ok = window.confirm(
        `Restore the original of "${name}"?\n\n` +
        `The normalized file currently at this location will be deleted ` +
        `and replaced with the pristine copy from "CharBackup".`
      );
      if (!ok) return;
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.recover_file(path);
      }
    }
    async function onCopyLog() {
      const text = log.map((l) => `${l.ts}  ${l.text}`).join("\n");
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        if (window.pywebview && window.pywebview.api) {
          await window.pywebview.api.copy_log(text);
        }
      }
      setCopyFlash(true);
      setTimeout(() => setCopyFlash(false), 1200);
    }

    // dragenter/dragleave fire on every child crossing; use a counter so
    // the visual overlay only goes away when we've truly left the window.
    function onDragEnter(e) {
      e.preventDefault();
      dragCounter.current += 1;
      if (dragCounter.current === 1) setDragOver(true);
    }
    function onDragLeave(e) {
      e.preventDefault();
      dragCounter.current = Math.max(0, dragCounter.current - 1);
      if (dragCounter.current === 0) setDragOver(false);
    }
    function onDragOver(e) { e.preventDefault(); }
    function onDrop(e) {
      e.preventDefault();
      dragCounter.current = 0;
      setDragOver(false);
      // Actual file paths are routed in via Python's native AppKit handler;
      // JS receives the event purely for the visual cue.
    }

    const statusInfo = useMemo(() => {
      const pending = queue.filter((q) => q.status === "pending").length;
      const processing = queue.filter((q) => q.status === "processing").length;
      if (dragOver) return { dot: THEME.accent, text: "Drop to add to queue", pulsing: true };
      if (processing > 0) {
        const others = pending > 0 ? `, ${pending} pending` : "";
        return {
          dot: THEME.accent,
          text: `Processing ${processing} file${processing === 1 ? "" : "s"}${others}`,
          pulsing: true,
        };
      }
      if (pending > 0) {
        return { dot: THEME.textFaint, text: `${pending} file${pending === 1 ? "" : "s"} ready — click Start`, pulsing: false };
      }
      switch (status.kind) {
        case "done":
          return {
            dot: THEME.success,
            text: status.out_lufs != null
              ? `Last done: ${status.file} (${status.out_lufs.toFixed(1)} LUFS)`
              : `Last done: ${status.file}`,
            pulsing: false,
          };
        case "error":
          return { dot: THEME.error, text: status.text || "Error", pulsing: false };
        default:
          return { dot: THEME.idle, text: "Idle — drop audio files anywhere to queue them", pulsing: false };
      }
    }, [status, queue, dragOver]);

    const canStart = queue.some((q) => q.status === "pending");
    const isProcessing = queue.some((q) => q.status === "processing");
    const charlieT = charlieScale(lufs);

    return h("div", {
      onDragEnter, onDragOver, onDragLeave, onDrop,
      style: {
        width: "100vw", height: "100vh",
        background: THEME.bg,
        color: THEME.text,
        display: "flex", flexDirection: "column",
        padding: "16px 20px",
        boxSizing: "border-box",
        position: "relative",
        outline: dragOver ? `3px solid ${THEME.accent}` : "none",
        outlineOffset: -3,
        transition: "outline 120ms ease",
      },
    },
      // Drag overlay (only while dragging)
      dragOver && h("div", {
        style: {
          position: "absolute", inset: 0,
          background: THEME.dropTint,
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, fontWeight: 600, color: THEME.accent,
          pointerEvents: "none",
          zIndex: 10,
        },
      }, "Drop audio files to queue"),

      // Body
      h("div", {
        style: {
          flex: 1, display: "flex", flexDirection: "column", minHeight: 0,
        },
      },
        // ─── TARGET LUFS + CHARLIE ───
        h(Section, { style: { paddingBottom: 12 } },
          h("div", { style: { display: "flex", gap: 16, alignItems: "center" } },
            h("div", { style: { flex: 1, minWidth: 0 } },
              h(SectionLabel, null, "Target loudness"),
              h("div", {
                style: { display: "flex", alignItems: "baseline", gap: 6, marginBottom: 8 },
              },
                h("span", {
                  style: {
                    fontSize: 30, fontWeight: 600, lineHeight: 1,
                    fontVariantNumeric: "tabular-nums",
                    letterSpacing: -0.5,
                    color: THEME.text,
                  },
                }, lufs.toFixed(1)),
                h("span", {
                  style: {
                    fontSize: 12, color: THEME.textDim, fontWeight: 500,
                    letterSpacing: 0.4,
                  },
                }, "LUFS")
              ),
              h(LufsSlider, {
                value: lufs,
                onChange: onLufsChange,
                onDragStart: () => {},
                onDragEnd: () => {},
              }),
              h("div", {
                style: {
                  display: "flex", justifyContent: "space-between", marginTop: 6,
                },
              }, ...PRESETS.map((p) => {
                const active = Math.abs(p.value - lufs) < 0.25;
                return h("button", {
                  key: p.value,
                  onClick: () => onLufsChange(p.value),
                  title: `Set to ${p.value} LUFS`,
                  style: {
                    background: "transparent", border: "none", padding: "2px 0",
                    cursor: "pointer",
                    color: active ? THEME.accent : THEME.textFaint,
                    fontSize: 10, fontWeight: active ? 600 : 500,
                    fontVariantNumeric: "tabular-nums",
                    display: "flex", flexDirection: "column",
                    alignItems: "center", gap: 1,
                  },
                },
                  h("span", { style: { fontFamily: '"SF Mono", ui-monospace, Menlo, monospace' } }, p.value),
                  h("span", { style: { fontSize: 9, letterSpacing: 0.3 } }, p.label)
                );
              }))
            ),
            // Charlie
            h("div", {
              style: {
                width: CHARLIE_MAX_PX + 8, height: CHARLIE_MAX_PX + 8,
                flexShrink: 0,
                display: "flex", alignItems: "center", justifyContent: "center",
              },
            },
              h("div", {
                className: "charlie-wrap",
                style: { transform: `scale(${charlieT})` },
              },
                h(window.Charlie, { size: CHARLIE_BASE_PX })
              )
            )
          )
        ),

        // ─── FILE QUEUE ───
        h("div", {
          style: {
            display: "flex", flexDirection: "column",
            flex: 1, minHeight: 0, marginBottom: 12,
          },
        },
          h("div", {
            style: {
              display: "flex", justifyContent: "space-between",
              alignItems: "baseline", marginBottom: 6,
            },
          },
            h(SectionLabel, null, `Queue${queue.length ? ` · ${queue.length}` : ""}`),
            h("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
              queue.length > 0 && h("button", {
                onClick: onClear,
                disabled: isProcessing && queue.every((q) => q.status === "processing"),
                style: {
                  background: "transparent", border: "none", padding: 0,
                  color: THEME.textDim, fontSize: 11,
                  cursor: "pointer", fontWeight: 500,
                },
              }, "Clear"),
              h("button", {
                onClick: onStart,
                disabled: !canStart || isProcessing,
                style: {
                  background: (canStart && !isProcessing) ? THEME.accent : THEME.bgRaised,
                  border: `0.5px solid ${(canStart && !isProcessing) ? THEME.accent : THEME.border}`,
                  borderRadius: 6,
                  padding: "5px 14px",
                  color: (canStart && !isProcessing) ? "#FFFFFF" : THEME.textFaint,
                  fontSize: 12, fontWeight: 600,
                  cursor: (canStart && !isProcessing) ? "pointer" : "default",
                  letterSpacing: 0.3,
                },
              }, isProcessing ? "Running…" : "Start")
            )
          ),
          h("div", {
            style: {
              background: THEME.bgSunken,
              border: `1px solid ${THEME.border}`,
              borderRadius: 8,
              padding: 6,
              flex: 1, minHeight: 100,
              overflowY: "auto",
              display: "flex", flexDirection: "column",
              gap: 2,
            },
            className: "queue-body",
          },
            queue.length === 0
              ? h("div", {
                  style: {
                    flex: 1,
                    display: "flex", flexDirection: "column",
                    alignItems: "center", justifyContent: "center",
                    color: THEME.textFaint, fontSize: 12.5,
                    textAlign: "center", padding: "20px 16px",
                    gap: 4,
                  },
                },
                  h("div", null, "Drag audio files anywhere onto this window"),
                  h("div", { style: { fontSize: 11, color: THEME.textFaint } },
                    "Originals are preserved in “CharBackup” next to each file"
                  )
                )
              : queue.map((item, i) => h(QueueItem, {
                  key: item.path + i,
                  item, index: i,
                  onRemove, onReveal, onRecover,
                }))
          )
        ),

        // ─── STATUS ───
        h(Section, null,
          h("div", { style: { display: "flex", alignItems: "center", gap: 12 } },
            h(StatusDot, { color: statusInfo.dot, pulsing: statusInfo.pulsing }),
            h("div", {
              style: {
                fontSize: 13, fontWeight: 500, color: THEME.text,
                flex: 1,
                textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap",
              },
            }, statusInfo.text),
            h("div", {
              style: {
                fontSize: 12, color: THEME.textDim,
                fontVariantNumeric: "tabular-nums",
              },
            }, `${processed} file${processed === 1 ? "" : "s"} this session`)
          )
        ),

        // ─── LOG ───
        h("div", {
          style: {
            display: "flex", flexDirection: "column",
            minHeight: 0,
          },
        },
          h("div", {
            style: {
              display: "flex", justifyContent: "space-between",
              alignItems: "baseline", marginBottom: 4,
            },
          },
            h(SectionLabel, null, "Log"),
            h("button", {
              onClick: onCopyLog,
              style: {
                background: "transparent", border: "none", padding: 0,
                color: copyFlash ? THEME.accent : THEME.textDim,
                fontSize: 11, cursor: "pointer",
                fontWeight: 500,
                transition: "color 160ms ease",
              },
            }, copyFlash ? "Copied" : "Copy log")
          ),
          h("div", {
            ref: logRef,
            className: "log-body",
            style: {
              background: THEME.logBg,
              border: `1px solid ${THEME.border}`,
              borderRadius: 8,
              padding: "8px 12px",
              fontFamily: '"SF Mono", ui-monospace, Menlo, monospace',
              fontSize: 11.5,
              lineHeight: 1.55,
              color: THEME.logText,
              height: 100,
              overflowY: "auto",
              fontVariantNumeric: "tabular-nums",
            },
          }, ...log.map((entry, i) => h("div", {
            key: i,
            style: { display: "flex", gap: 10, alignItems: "flex-start" },
          },
            h("span", {
              style: {
                width: 6, height: 6, borderRadius: "50%", marginTop: 7, flexShrink: 0,
                background: entry.kind === "done" ? THEME.success
                          : entry.kind === "error" ? THEME.error
                          : "transparent",
              },
            }),
            h("span", { style: { color: THEME.textFaint, flexShrink: 0 } }, entry.ts),
            h("span", null, entry.text)
          )))
        )
      )
    );
  }

  function parseLogLine(line) {
    if (line && typeof line === "object" && line.text != null) {
      return line;
    }
    const s = String(line);
    const m = s.match(/^(\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})\s+(\w+)\s+(.*)$/);
    if (!m) return { ts: nowStamp(), kind: "info", text: s };
    const ts = m[2];
    const level = (m[3] || "INFO").toUpperCase();
    const text = m[4] || "";
    let kind = "info";
    if (level === "ERROR") kind = "error";
    else if (level === "WARNING") kind = "warning";
    else if (text.startsWith("Done")) kind = "done";
    return { ts, kind, text };
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function nowStamp() {
    const d = new Date();
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  const root = ReactDOM.createRoot(document.getElementById("root"));
  root.render(h(App));
})();
