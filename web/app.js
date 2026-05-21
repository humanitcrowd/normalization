// CharLUFS — main app, vanilla React (no JSX, no Babel runtime).
//
// Communicates with the Python backend via the pywebview bridge:
//   await pywebview.api.get_initial_state()
//   await pywebview.api.set_target_lufs(value)
//   await pywebview.api.change_folder()
//   await pywebview.api.open_folder()
//   await pywebview.api.copy_log(text)
// Python pushes events into JS via CustomEvents:
//   charlufs:status { kind, file?, out_lufs?, in_lufs? }
//   charlufs:log    "<formatted log line>"
//   charlufs:folder "<path>"
//   charlufs:counter <n>

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
    titleBar: "#2A2C31",
    accent: "#C97A3F",
  };

  const LUFS_MIN = -23;
  const LUFS_MAX = -8;
  const LUFS_STEP = 0.5;
  const LUFS_DEFAULT = -16;

  // Note: presets must fit the [LUFS_MIN, LUFS_MAX] range
  const PRESETS = [
    { value: -23, label: "EBU R128" },
    { value: -19, label: "Audible" },
    { value: -16, label: "Podcast" },
    { value: -14, label: "Spotify" },
    { value: -10, label: "Loud" },
    { value: -8,  label: "Loud as fuck" },
  ];

  // Charlie scales linearly from CHARLIE_MIN_PX (at LUFS_MIN) to
  // CHARLIE_MAX_PX (at LUFS_MAX). Done via CSS transform on the wrapper
  // so the SVG itself can stay sharp at any size.
  const CHARLIE_MIN_PX = 110;
  const CHARLIE_MAX_PX = 200;
  const CHARLIE_BASE_PX = 168;

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

  // ── tiny "Open" arrow icon ──
  function ArrowIcon(props) {
    return h("svg", {
      width: 11, height: 11, viewBox: "0 0 11 11", fill: "none",
      style: { marginLeft: 4 },
    },
      h("path", {
        d: "M3 1 L8 5.5 L3 10",
        stroke: props.color,
        strokeWidth: "1.6",
        strokeLinecap: "round",
        strokeLinejoin: "round",
        fill: "none",
      })
    );
  }

  // ── window chrome ──
  function TrafficLights() {
    const dot = (bg) => h("div", {
      style: {
        width: 12, height: 12, borderRadius: "50%", background: bg,
        boxShadow: "inset 0 0 0 0.5px rgba(0,0,0,0.18)",
      },
    });
    return h("div", { style: { display: "flex", gap: 8 } },
      dot("#ff5f57"), dot("#febc2e"), dot("#28c840")
    );
  }

  function Section(props) {
    return h("div", {
      style: Object.assign({
        paddingBottom: 14,
        marginBottom: 14,
        borderBottom: `1px solid ${THEME.borderSoft}`,
      }, props.style || {}),
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
          boxShadow: props.pulsing ? `0 0 0 0 ${props.color}66` : "none",
          animation: props.pulsing ? "charlufs-pulse 1.4s ease-out infinite" : "none",
        },
      })
    );
  }

  // ── slider ──
  function LufsSlider(props) {
    const { value, onChange, onDragStart, onDragEnd } = props;
    const pct = ((value - LUFS_MIN) / (LUFS_MAX - LUFS_MIN)) * 100;
    return h("div", { style: { position: "relative", height: 26 } },
      // track
      h("div", {
        style: {
          position: "absolute", left: 0, right: 0, top: "50%",
          height: 5, marginTop: -2.5,
          background: THEME.bgSunken,
          borderRadius: 999,
          border: `0.5px solid ${THEME.borderSoft}`,
        },
      }),
      // fill
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
      // preset tick marks
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
      // thumb
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
      // invisible native input
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
    const [folder, setFolder] = useState("~/CharLUFS");
    const [lufs, setLufs] = useState(LUFS_DEFAULT);
    const [dragging, setDragging] = useState(false);
    const [status, setStatus] = useState({ kind: "idle" });
    const [processed, setProcessed] = useState(0);
    const [log, setLog] = useState([]);
    const [dragOver, setDragOver] = useState(false);
    const [copyFlash, setCopyFlash] = useState(false);
    const logRef = useRef(null);

    // Pull initial state from Python; subscribe to push events
    useEffect(() => {
      let cancelled = false;
      (async () => {
        await bridgeReady();
        try {
          const state = await window.pywebview.api.get_initial_state();
          if (cancelled) return;
          if (state.watch_folder) setFolder(state.watch_folder);
          if (typeof state.target_lufs === "number") setLufs(state.target_lufs);
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

    // Push-event listeners (Python -> JS)
    useEffect(() => {
      const onStatus = (e) => setStatus(e.detail);
      const onLog = (e) => {
        const line = e.detail;
        setLog((l) => l.concat([parseLogLine(line)]).slice(-200));
      };
      const onFolder = (e) => setFolder(e.detail);
      const onCounter = (e) => setProcessed(e.detail);
      window.addEventListener("charlufs:status", onStatus);
      window.addEventListener("charlufs:log", onLog);
      window.addEventListener("charlufs:folder", onFolder);
      window.addEventListener("charlufs:counter", onCounter);
      return () => {
        window.removeEventListener("charlufs:status", onStatus);
        window.removeEventListener("charlufs:log", onLog);
        window.removeEventListener("charlufs:folder", onFolder);
        window.removeEventListener("charlufs:counter", onCounter);
      };
    }, []);

    // Autoscroll log
    useEffect(() => {
      if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
    }, [log]);

    // Push slider changes to Python (the snap also happens here so the UI
    // always shows the snapped value)
    function onLufsChange(raw) {
      const v = snapLufs(parseFloat(raw));
      setLufs(v);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.set_target_lufs(v).catch(() => {});
      }
    }

    async function onOpenFolder() {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.open_folder();
      }
    }
    async function onChangeFolder() {
      if (window.pywebview && window.pywebview.api) {
        await window.pywebview.api.change_folder();
        // folder is also pushed back via charlufs:folder, but pull it now for instant feedback
      }
    }
    async function onCopyLog() {
      const text = log.map((l) => `${l.ts}  ${l.text}`).join("\n");
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        // fallback via Python
        if (window.pywebview && window.pywebview.api) {
          await window.pywebview.api.copy_log(text);
        }
      }
      setCopyFlash(true);
      setTimeout(() => setCopyFlash(false), 1200);
    }

    // Drag-over affordance — pywebview generally does not deliver native
    // file drops to the page, but the visual cue is still useful when the
    // user drags from Finder over the window edge.
    function onDragOver(e) { e.preventDefault(); setDragOver(true); }
    function onDragLeave(e) { e.preventDefault(); setDragOver(false); }
    function onDrop(e) { e.preventDefault(); setDragOver(false); }

    // ── status info ──
    const statusInfo = useMemo(() => {
      switch (status.kind) {
        case "idle":        return { dot: THEME.idle,    text: "Idle" };
        case "waiting":     return { dot: THEME.accent,  text: `Waiting for ${status.file}…` };
        case "measuring":   return { dot: THEME.accent,  text: `Measuring ${status.file}` };
        case "normalizing": return { dot: THEME.accent,  text: `Normalizing ${status.file}` };
        case "working":     return { dot: THEME.accent,  text: status.text || "Working…" };
        case "done":        return {
          dot: THEME.success,
          text: status.out_lufs != null
            ? `Done: ${status.file} (${status.out_lufs.toFixed(1)} LUFS)`
            : `Done: ${status.file}`,
        };
        case "error":       return { dot: THEME.error,   text: status.text || `Couldn't process ${status.file || ""}` };
        default:            return { dot: THEME.idle,    text: "Idle" };
      }
    }, [status]);

    const isWorking = ["waiting", "measuring", "normalizing", "working"].includes(status.kind);
    const charlieT = charlieScale(lufs);

    return h("div", {
      style: {
        width: "100vw", height: "100vh",
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "transparent",
        color: THEME.text,
        padding: 24,
        boxSizing: "border-box",
      },
    },
      // window
      h("div", {
        onDragOver, onDragLeave, onDrop,
        style: {
          width: 680, height: 540,
          background: THEME.bg,
          borderRadius: 12,
          overflow: "hidden",
          display: "flex", flexDirection: "column",
          boxShadow: "0 24px 60px rgba(0,0,0,0.45), 0 0 0 0.5px rgba(0,0,0,0.6)",
          position: "relative",
          outline: dragOver ? `2px solid ${THEME.accent}` : "none",
          outlineOffset: dragOver ? -2 : 0,
          transition: "outline 160ms ease",
        },
      },
        // title bar
        h("div", {
          style: {
            height: 38, flexShrink: 0,
            background: THEME.titleBar,
            borderBottom: `0.5px solid ${THEME.border}`,
            display: "flex", alignItems: "center",
            padding: "0 14px", position: "relative",
          },
        },
          h(TrafficLights),
          h("div", {
            style: {
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              fontSize: 13, fontWeight: 600, color: THEME.textDim,
              letterSpacing: 0.2, pointerEvents: "none",
            },
          }, "CharLUFS")
        ),

        // body
        h("div", {
          style: {
            flex: 1, padding: "18px 22px 18px",
            display: "flex", flexDirection: "column",
            minHeight: 0,
          },
        },
          // ─── WATCHING ROW ───
          h(Section, null,
            h("div", {
              style: {
                display: "flex", alignItems: "baseline", gap: 10,
                justifyContent: "space-between",
              },
            },
              h("div", null,
                h("div", {
                  style: {
                    fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6,
                    color: THEME.textFaint, fontWeight: 600, marginBottom: 6,
                  },
                }, "Watching"),
                h("div", { style: { display: "flex", alignItems: "center", gap: 12 } },
                  h("div", {
                    style: {
                      fontFamily: '"SF Mono", ui-monospace, Menlo, monospace',
                      fontSize: 14, color: THEME.text, fontWeight: 500,
                      fontVariantNumeric: "tabular-nums",
                    },
                  }, folder),
                  h("button", {
                    onClick: onOpenFolder,
                    style: {
                      background: "transparent", border: "none", padding: 0,
                      color: THEME.accent, fontSize: 12, fontWeight: 600,
                      cursor: "pointer",
                      display: "inline-flex", alignItems: "center",
                    },
                  }, "Open", h(ArrowIcon, { color: THEME.accent }))
                )
              ),
              h("button", {
                onClick: onChangeFolder,
                style: {
                  background: THEME.bgRaised,
                  border: `0.5px solid ${THEME.border}`,
                  borderRadius: 6,
                  padding: "5px 12px",
                  color: THEME.text,
                  fontSize: 12, fontWeight: 500,
                  cursor: "pointer",
                },
              }, "Change folder…")
            )
          ),

          // ─── TARGET LUFS + CHARLIE ───
          h(Section, { style: { paddingTop: 22, paddingBottom: 22 } },
            h("div", { style: { display: "flex", gap: 28, alignItems: "center" } },
              // slider column
              h("div", { style: { flex: 1, minWidth: 0 } },
                h("div", {
                  style: {
                    fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6,
                    color: THEME.textFaint, fontWeight: 600, marginBottom: 10,
                  },
                }, "Target loudness"),

                // readout
                h("div", {
                  style: { display: "flex", alignItems: "baseline", gap: 8, marginBottom: 14 },
                },
                  h("span", {
                    style: {
                      fontSize: 52, fontWeight: 600, lineHeight: 1,
                      fontVariantNumeric: "tabular-nums",
                      letterSpacing: -0.5,
                      fontFeatureSettings: '"tnum"',
                      color: THEME.text,
                    },
                  }, lufs.toFixed(1)),
                  h("span", {
                    style: {
                      fontSize: 14, color: THEME.textDim, fontWeight: 500,
                      letterSpacing: 0.4,
                    },
                  }, "LUFS")
                ),

                h(LufsSlider, {
                  value: lufs,
                  onChange: onLufsChange,
                  onDragStart: () => setDragging(true),
                  onDragEnd: () => setDragging(false),
                }),

                // presets
                h("div", {
                  style: {
                    display: "flex", justifyContent: "space-between",
                    marginTop: 10,
                  },
                }, ...PRESETS.map((p) => {
                  const active = Math.abs(p.value - lufs) < 0.25;
                  return h("button", {
                    key: p.value,
                    onClick: () => onLufsChange(p.value),
                    style: {
                      background: "transparent",
                      border: "none",
                      padding: "2px 0",
                      cursor: "pointer",
                      color: active ? THEME.accent : THEME.textFaint,
                      fontSize: 10.5,
                      fontWeight: active ? 600 : 500,
                      fontVariantNumeric: "tabular-nums",
                      display: "flex", flexDirection: "column",
                      alignItems: "center", gap: 2,
                      transition: "color 200ms ease",
                    },
                    title: `Set to ${p.value} LUFS`,
                  },
                    h("span", { style: { fontFamily: '"SF Mono", ui-monospace, Menlo, monospace' } },
                      p.value
                    ),
                    h("span", { style: { fontSize: 9.5, letterSpacing: 0.3 } }, p.label)
                  );
                }))
              ),

              // Charlie — scales with LUFS via CSS transform
              h("div", {
                style: {
                  width: CHARLIE_MAX_PX + 12, height: CHARLIE_MAX_PX + 12,
                  flexShrink: 0,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  position: "relative",
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

          // ─── STATUS ───
          h(Section, null,
            h("div", { style: { display: "flex", alignItems: "center", gap: 12 } },
              h(StatusDot, { color: statusInfo.dot, pulsing: isWorking }),
              h("div", {
                style: {
                  fontSize: 14, fontWeight: 600, color: THEME.text,
                  fontVariantNumeric: "tabular-nums",
                  flex: 1,
                  textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap",
                },
              }, dragOver ? "Drop here to normalize" : statusInfo.text),
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
              marginTop: 0,
              display: "flex", flexDirection: "column",
              flex: 1, minHeight: 0,
            },
          },
            h("div", {
              style: {
                display: "flex", justifyContent: "space-between", alignItems: "baseline",
                marginBottom: 6,
              },
            },
              h("div", {
                style: {
                  fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6,
                  color: THEME.textFaint, fontWeight: 600,
                },
              }, "Log"),
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
                padding: "10px 12px",
                fontFamily: '"SF Mono", ui-monospace, Menlo, monospace',
                fontSize: 11.5,
                lineHeight: 1.65,
                color: THEME.logText,
                flex: 1, minHeight: 88,
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
      )
    );
  }

  // ── log parsing ──
  // We accept either raw log lines from Python ("2026-05-21 11:50:25 INFO ...")
  // or pre-shaped objects. The line format is intentionally simple.
  function parseLogLine(line) {
    if (line && typeof line === "object" && line.text != null) {
      return line;
    }
    const s = String(line);
    // ISO timestamp at the start
    const m = s.match(/^(\d{4}-\d{2}-\d{2}\s+)?(\d{2}:\d{2}:\d{2})\s+(\w+)\s+(.*)$/);
    if (!m) return { ts: nowStamp(), kind: "info", text: s };
    const ts = m[2];
    const level = (m[3] || "INFO").toUpperCase();
    const text = m[4] || "";
    let kind = "info";
    if (level === "ERROR") kind = "error";
    else if (level === "WARNING") kind = "warning";
    else if (text.startsWith("Done:")) kind = "done";
    return { ts, kind, text };
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function nowStamp() {
    const d = new Date();
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }

  // mount
  const root = ReactDOM.createRoot(document.getElementById("root"));
  root.render(h(App));
})();
