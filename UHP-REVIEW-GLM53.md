# UHP-bones implementation review — GLM 5.3 (zcode headless)

Reviewed: uncommitted `serve.py` (+358/−6, UHP surface) and `atlas-mac/models.py` (binary-path fix).
Method: full read of both files, cross-check against `atlas-mac/agent.py` + `orchestrator.py` (the
actual NDJSON producer), the published UHP spec at unifiedharnessprotocol.org (chapters Streaming,
Tasks, Sessions, Harnesses, v2026-09-12), and empirical probes: a subprocess/pipe experiment
reproducing `_atlas_stream`'s exact IO pattern, plus the real `serve.py` handler code exercised
over HTTP on a scratch port (19099) with a stub atlas CLI. Probe transcript evidence is cited as
P1–P9 (repro scripts preserved in `/tmp/uhp_review/`). No source files were edited; nothing was
started on 9093.

**No Critical findings.** Nothing here threatens the host, secrets, or durable data — the server
binds loopback by default and atlas side effects are workspace-local. There are six Majors: three
correctness/state-machine holes proven live, one IO-design bug proven mechanically, one protocol
promise (session context) that is bookkeeping-only, and one conformance shape gap. M1–M3 should be
fixed before commit; the rest can be immediate follow-ups.

---

## Verdict per fix (handoff fix list)

| # | Fix | Verdict |
|---|-----|---------|
| 1 | `_atlas_stream` select-based deadline loop + proc registry | **HOLED** — registry works, but the deadline loop has two proven failure modes (see M1): `select` can't see lines already buffered in the `TextIOWrapper` (false timeout kills healthy pipelines), and `readline()` can block past the deadline (budget overshoot). |
| 2 | UHPHandlerMixin routes + validation | **HOLED** — harness 404 / tier 422 / previous_response_id 404 are correct per spec, but any malformed body produces **no HTTP response at all** (P1–P4), and discovery is missing required fields (M5). |
| 3 | Streaming SSE path | **HOLED** — sequence numbers from 0, `response.created` first, full response on terminal, broken-pipe guard, EOF-after-terminal all verified good; but the terminal event for a cancelled stream is `response.completed` where the spec mandates `response.failed` carrying `status:"cancelled"` (P6, M2c). |
| 4 | Non-streaming path + `[Pipeline: X]` parsing | **HOLED** — the parsing target is real (`agent.py:941` prints `[{summary()}]`, `orchestrator.py:53` emits `Pipeline: {status}`), but the path sets `error` on `incomplete` (spec: error only when failed), discards partial output on timeout, and clobbers a cancel into `failed` (P7, M2b). |
| 5 | Cancellation status semantics | **HOLED** — rc<0→cancelled intent and stamp-before-kill are right, but cancel of an already-terminal response **flips completed→cancelled** (P5, spec: "MUST succeed and change nothing"), and the non-stream overwrite records `failed` for a client-cancelled task (P7, spec: "MUST end with status: 'cancelled', never failed"). |
| 6 | `ThreadingHTTPServer` in main() | **SOUND** — P6/P7 exercise exactly this: cancel on a second connection reaches a running stream; daemon threads; no deadlock. (Note: the handoff's claimed `threading.Lock` does not exist in the code — zero `Lock()` occurrences. The races are logical-order bugs, not GIL corruption; see M2e.) |
| — | `models.py` LLAMA_SERVER_BIN fix | **SOUND** — `os.environ.get("ATLAS_LLAMA_SERVER_BIN", "/usr/local/bin/llama-server")` (models.py:179-181) preserves the override; `~/bin/atlas-mac:8` exports the same default; no other code-path hardcodes the Linux binary. One stale Linux path survives in an error *message* (models.py:328, m8). |

## Verdict per attack probe

1. **Cancellation race (finally-pop vs terminal emit)** — CONFIRMED, plus two worse siblings. The pop at `serve.py:127` runs before finalization, so a cancel in that window stamps `cancelled` on a response the pipeline actually finished; there is no terminal-state guard in `_handle_v1_response_cancel` (P5 proves the flip on an already-completed response). The wider window is at the *start*: `UHP_RESPONSES[rid] = resp` (serve.py:1221) precedes registry insertion (serve.py:94) by the whole Popen spawn, so a cancel there 200s as `cancelled` but finds no proc to kill — the pipeline runs to completion and is recorded as cancelled. Details and fix in M2.
2. **TimeoutExpired double-kill** — REFUTED (safe). After the inner `proc.wait()`, `returncode` is set; Python 3.13's `Popen.send_signal` polls first and returns early when reaped, and additionally catches `ProcessLookupError` (verified against the installed 3.13.2 source). The second kill at serve.py:122-123 is redundant but cannot raise or mask the `TimeoutExpired`.
3. **Sequence numbers across paths / response shape** — SOUND. `sequence_number` is a streaming-event concept; the non-stream object correctly carries none. Required response fields per Tasks §3 are all present (`id` resp_-prefixed, `object:"response"`, `created_at`, `status`, `error`, `output`, `model`, `usage:null`, `metadata.session_id`, `previous_response_id`, `store`), and `usage: null` matches the spec's exact "never a fabricated zero" rule. P6 observed seq `[0,1,2,3]`, strictly monotonic. One nuance: `model` records the *requested* tier, not "the model that actually ran" if atlas internally falls back (Info, i2).
4. **Broken-pipe guard completeness** — SOUND with one note. Every stream write goes through `emit()` (serve.py:1269-1280), which latches `client_gone` on `BrokenPipeError/ConnectionResetError/OSError`; finalization continues, satisfying "a dropped connection MUST NOT abort the task." The unguarded write is `self._sse()` (serve.py:1265): a disconnect between POST and header-write raises out of the handler, leaving the stored response `in_progress` forever (no proc leak — `_atlas_stream` was never reached). Narrow, cosmetic-adjacent; noted in m5.
5. **models.py env override + other hardcoded paths** — SOUND in code. Only `models.py:328` (error text) and historical `handoff-*.md` docs still mention Linux paths; nothing executable does. See m8.
6. **Thread safety / lock** — HOLED vs the handoff's description: **there is no lock** (0 `Lock()` in serve.py). CPython's GIL makes the individual dict/list ops atomic, so no corruption, but the logical races are real and proven (P5/P7), and a GET during the stream-finalize window can observe `status:"completed"` (set by the `[Pipeline: X]` line at serve.py:1292-1295) with `output: []` (attached only at serve.py:1349). M2e.
7. **max_step → --max-rounds** — SOUND. `serve.py:1226-1230` appends `--max-rounds` when `int()`-able; `agent.py:767` declares it (`type=int`). Edge: non-numeric `max_step` is silently dropped (not added to `ignored_fields` — defensible since it isn't a reserved field, but worth naming); `max_step: 0`/negative passes through to atlas.
8. **Tier validation** — SOUND. `UHP_TIERS` (serve.py:1121) is exactly `agent.py:765`'s `--tier` choices `{8b, 7bs, 4bs, 14b, 35b, auto}`. No false 422s, no passthrough. (Behavior of `35b`/`14b` — remote-only placeholders per models.py:136-166 — is atlas's concern, and atlas guards them.)
9. **SSE header correctness** — SOUND with notes. `text/event-stream` + `Cache-Control: no-cache` + data-only frames are compliant; P6 read the stream to EOF cleanly. `Connection: keep-alive` under the default HTTP/1.0 `protocol_version` (never raised in serve.py) followed by `close_connection = True` is a cosmetic contradiction; no `X-Accel-Buffering` matters only behind a proxy. m9.
10. **JSON body size / error handling** — CONFIRMED HOLED. Malformed JSON, a valid-JSON non-object body, a non-object `metadata`, or a non-integer `Content-Length` each crash the handler thread with **zero bytes of HTTP response** (client sees `RemoteDisconnected`); there is also no body-size cap. M3.

---

## Findings (severity-ranked)

### MAJOR

**M1 — `_atlas_stream`'s deadline loop cannot actually enforce the deadline (two proven modes)**
`serve.py:102-119` (loop), `serve.py:84-92` (`bufsize=1`, text mode).

- *Mode A, false timeout:* `select` watches the OS fd; `readline()` may leave subsequent complete
  lines inside the `TextIOWrapper`/`BufferedReader`, invisible to `select`. If the child then goes
  quiet, `select` times out at the deadline and the loop **kills a healthy, streaming pipeline**
  and reports `response.incomplete`/`timeout` — with the unconsumed events silently missing from
  `output`. Reproduced 1:1 with the production pattern (pipe experiment): 3 lines written in one
  burst, 1 consumed, deadline fired with 2 complete lines parked in the Python buffer.
  This also silently violates the Tasks budget rule in spirit — truncated work is reported as a
  *timeout*, and the stored `output` is missing items the pipeline already produced.
- *Mode B, budget overshoot:* if the child flushes a **partial** line and stalls (e.g. a progress
  fragment before hanging in a model call), `select` reports ready, and `readline()` blocks until
  newline/EOF — the deadline is never consulted again. Reproduced: deadline 2.0s, `readline` still
  blocked at 6.2s. Violates Tasks: "MUST stop the task at or after the budget."

Both modes share one root cause: mixing a deadline `select` loop with a userspace-buffered text
stream. Note the live-verified "5s budget → incomplete" result is exactly the symptom Mode A
produces, so the earlier verification cannot distinguish genuine from false timeout.

*Fix sketch:* drop text mode — `Popen(stdout=PIPE, bufsize=0)` (binary), keep the `select` loop,
but `os.read(fd, 65536)` after ready, append to a local `bytearray`, split complete `\n`-lines
manually, and feed `on_line` per line; check the deadline at the top of each iteration (which now
genuinely bounds every chunk) and, on timeout, kill + drain + re-raise. That makes both modes
impossible: nothing is buffered above the fd, and no single `os.read` can block past a ready
`select`.

**M2 — Cancel state machine: four races + a missing guard produce false records** (all proven on
the real handler code)

- **(a) Cancel of a terminal response rewrites history** — `_handle_v1_response_cancel`
  (serve.py:1397-1412) has no terminal-state guard: it stamps `status="cancelled"` on *any* stored
  id. P5: FAST run → `completed` → POST cancel → 200 `cancelled` → GET shows `cancelled`. Spec
  (Sessions §4): "Cancelling an already-terminal task MUST succeed and change nothing."
  *Fix:* if `resp["status"]` is terminal, return it unchanged.
- **(b) Non-stream path clobbers cancelled→failed** — P7: cancel says `cancelled`; when the POST
  finally returns it is `failed` with `"atlas exited -9"`, and GET agrees. The rc-mapping at
  serve.py:1244-1254 never checks whether status was already set. Spec: a cancelled task "MUST end
  with status: 'cancelled', never failed." *Fix:* in the non-stream rc-mapping, skip overwriting a
  non-`in_progress` status (mirroring the stream path's guard at serve.py:1336), and map `rc < 0`
  to `cancelled` there too.
- **(c) Wrong terminal event for cancelled streams** — P6: terminal event was
  `response.completed` carrying `status:"cancelled"` and `error.code:"cancelled"`. Spec
  (Streaming): "A cancelled task terminates with **response.failed** carrying status:
  'cancelled'… the status field, not the event name, is authoritative." The event/type selection at
  serve.py:1351-1356 only knows failed/incomplete/else-completed. Same selection also emits
  `response.completed` for a *non-terminal* status in the rc==0-no-summary edge (m3).
  *Fix:* map status→event explicitly: `completed→completed`, `failed→failed`,
  `incomplete→incomplete`, `cancelled→response.failed` (payload status `cancelled`); never emit a
  terminal for a non-terminal status.
- **(d) Pre-registration window** — `UHP_RESPONSES[rid] = resp` (serve.py:1221) happens before the
  Popen exists, and the registry entry appears only at serve.py:94. A cancel in between returns
  200 `cancelled` but kills nothing; the pipeline then runs to completion and is recorded
  `cancelled` (with real output attached). *Fix:* register a placeholder (`UHP_PROCS[rid] = None`
  or an `Event`) at store time; the cancel handler waits briefly on it (spec wants a response
  within ~1s) and kills if a proc appears; the stream thread re-checks a `cancelled` flag
  immediately after registration and aborts.
- **(e) No lock, and torn interim reads** — the handoff claims a lock; there is none. Beyond
  (a)/(b), `on_line` flips `resp["status"]` on the `[Pipeline: X]` line (serve.py:1292-1295) before
  `output` is attached (serve.py:1349), so a concurrent GET can see terminal status + empty output.
  *Fix:* one `threading.Lock` around store mutations and terminal transitions; attach `output`
  *before* setting a terminal status.

**M3 — Malformed requests crash with no HTTP response at all**
P1: `{"bad json` → traceback, connection reset. P2: `[1,2,3]` → `body.get` AttributeError.
P3: `"metadata": "nope"` → `meta_in.get` AttributeError. P4: `Content-Length: abc` → ValueError.
All at serve.py:1178/1179/1186 and `_body` serve.py:1458-1462. A client that sends OpenAI-style
array `input` or any slightly-off body gets `RemoteDisconnected` with zero diagnostics; there is
also no cap on `Content-Length` (unbounded `rfile.read` — memory DoS, mitigated by loopback bind).
*Fix sketch:* `_body()` wraps `int()`/`json.loads` and returns `None` on failure → handler replies
400 with a UHP-style error object; `isinstance(body, dict)` and `isinstance(metadata, dict)` checks;
cap length (e.g. 1 MiB → 413). ~15 lines total.

**M4 — Session continuation is bookkeeping-only; the spec's core promise is unimplemented**
`serve.py:1214` copies `session_id` from the previous response; `serve.py:1223` builds
`cmd = ["work", spec]` — **no prior context reaches atlas**. Spec (Sessions §1) requires the server
to "give the harness the conversational context of the earlier tasks" and run the new task "in the
same session, with the same working directory and its files." A client that chains via
`previous_response_id` gets a fresh pipeline that has never heard of the earlier turns — the wire
says "continued session," the behavior says "new session." (Working directory is coincidentally
stable via `cwd=os.getcwd()`.)
*Fix sketch (pick one, deliberately):* (1) keep a per-session transcript of `output` items and
prepend a context file to the atlas invocation once atlas supports a context/history input for
`work`; or (2) until then, reject `previous_response_id` with a clear `error` (`code:
"not_supported"`, 501) so clients don't build on a fiction — silently pretending is the worst
option.

**M5 — Discovery object misses required fields; harness id violates the prefix rule**
`_handle_v1_harnesses` (serve.py:1165-1175) returns `{id:"atlas", object, name, description,
models}`. Spec (Harnesses) required fields: `id` — "**chrn_-prefixed**", `object`, `name`,
`base` ("which harness runtime"), `createdAt` (Unix **milliseconds**). `base` and `createdAt` are
absent entirely; the id has no `chrn_` prefix. This is the one endpoint whose 200 was live-verified
— verified as *reachable*, but not as *conformant*. Related: `GET /v1/harnesses/{id}` is unmounted
and falls through to the GUI (m5), and unknown `/v1/*` paths return HTML 200 generally.
*Fix sketch:* stable id `chrn_<hex>`, `"base": "atlas"`, `"createdAt": int(time.time()*1000)`;
keep `description`/`models` as additive extras; route `GET /v1/harnesses/{id}` and make unknown
`/v1/*` return a JSON 404 instead of `_html()`.

**M6 — `Idempotency-Key` unimplemented (spec MUST)**
Tasks: repeated keys "MUST return the original result without a second execution," retained
≥24h. serve.py never reads the header (grep: absent). Practical failure: a client HTTP-timeout on a
long pipeline, then a retry, runs the whole atlas `work` a second time — double workspace mutation
and double commits, since atlas `work` is side-effectful. *Fix sketch:* module-level
`{key: (response_snapshot, expiry)}` consulted in `_handle_v1_responses_post` before spawning;
return the stored snapshot on hit; TTL 24h or document process-lifetime (in-memory store is
already restart-lossy, m-document).

### MINOR

- **m1 — `error` non-null on `incomplete` (non-stream only).** serve.py:1247-1249 sets
  `error.pipeline_failed` when `[Pipeline: incomplete]` is parsed; Tasks §3: error is "Present and
  non-null only when status is failed." Also diverges from the stream path, which sets no error
  there. Put the reason in `incomplete_details` instead.
- **m2 — Timeout discards partial output (non-stream only).** `resp["output"] = output`
  (serve.py:1240) sits *inside* the `try`, so the `TimeoutExpired` path (and generic exceptions)
  loses every collected item; the stream path attaches unconditionally (serve.py:1349). Move the
  attach before the `try` or into a `finally`.
- **m3 — `response.completed` can carry non-terminal status.** With `rc == 0` and no
  `[Pipeline: X]` line ever seen, status stays `in_progress` and the else-branch terminal emit
  (serve.py:1351-1356) sends `response.completed` wrapping `status:"in_progress"` — malformed
  terminal. Guard: only emit a terminal after forcing a terminal status (folded into M2c's map).
- **m4 — `[Pipeline: <unknown>]` coerced to `completed` (stream) vs ignored (non-stream).**
  serve.py:1294 vs 1369-1371. atlas currently only emits completed/incomplete (orchestrator.py:46),
  so latent — but the two paths disagree by construction, and the stream coercion can upgrade a
  future failure word into `completed`.
- **m5 — Protocol routes fall through to the GUI.** `GET /v1/responses` (collection) and
  `GET /v1/harnesses/atlas` return the HTML GUI with 200 (P8; route chain serve.py:1473-1490). A
  UHP client probing discovery receives HTML. Return JSON 404/405 for any `/v1/*` miss. (Related
  narrow window: unguarded `self._sse()` at serve.py:1265 can strand a stored response
  `in_progress` if the client died pre-headers.)
- **m6 — `findings` is always `""`.** `orchestrator.py:197` emits only
  `{event, specialist, verdict}` — no findings — so `_uhp_collect`/`on_line`
  (serve.py:1311, 1386) build function-call arguments of `{"verdict": ..., "findings": ""}`
  forever. Fidelity gap, atlas-side emission fix (add `result.findings` to the event).
- **m7 — atlas stderr is drained and then black-holed.** The drain thread (serve.py:96-100) fills
  `stderr_lines` that nothing ever reads; failures surface as `"atlas exited 1"` (serve.py:1254,
  1347) with no diagnostics. Include the tail of stderr in `error.message` on failure.
- **m8 — Stale Linux path in a user-facing error.** models.py:326-329: "Build upstream from
  /home/rell/llama.cpp/build-upstream/". Cosmetic (fires only when the binary is missing) but
  misdirects a Mac user. Suggest naming `ATLAS_LLAMA_SERVER_BIN` instead.
- **m9 — HTTP/1.0 + `Connection: keep-alive` + forced close.** `protocol_version` is never raised
  from the default HTTP/1.0, so the keep-alive header (serve.py:1446) is fiction and JSON responses
  don't reuse connections; the SSE EOF-after-terminal behavior itself is fine. Set
  `protocol_version = "HTTP/1.1"` (Content-Length is already correct on JSON paths) or drop the
  header.
- **m10 — Duplicate `call_id` across specialists.** `call_id=f"call_{round}"`
  (serve.py:1303, 1380) uses the per-specialist round counter, so editor round 1 and verifier round
  1 both yield `call_1`. Item `id`s (`fc_*`) are unique, but clients pairing calls to outputs by
  `call_id` see ambiguity. Use a monotonically increasing counter or fold the specialist name in.
- **m11 — Unvalidated budgets.** `timeout_seconds` has no upper cap (serve.py:1205-1209) and
  `max_step <= 0` passes through to atlas; a typo'd `timeout_seconds: "5m"` silently becomes 300
  without being named in `ignored_fields`.

### INFO

- **i1 — Spec-conformant positives worth keeping:** sequence numbers from 0 strictly monotonic
  (P6); `response.created` first; terminal carries the complete response; broken-pipe keeps
  finalizing ("dropped connection MUST NOT abort the task"); `usage: null` matches the spec's
  "never a fabricated zero" wording exactly; 404 `harness_not_found` / 422 `model_unavailable` /
  404 `response_not_found` codes all exactly per spec; default-harness use is reported in
  `metadata.harness_id`.
- **i2 — `model` records the requested tier, not necessarily "the model that actually ran"** when
  atlas internally falls back (orchestrator logs "falling back to 8B"). UHP's substitution
  metadata (`metadata.requested_model` / `model_fallback`) is unused. Latent until atlas fallbacks
  are common.
- **i3 — The `error` NDJSON branch (serve.py:1321-1325) is currently unreachable**: orchestrator
  emits only `dispatch`/`result`/`complete` (orchestrator.py:172,197,231). Harmless defensive code.
- **i4 — `DELETE /v1/responses/{id}`** unimplemented (BaseHTTPRequestHandler 501) — compliant by
  absence ("MUST NOT let this cancel a running task"). `POST /v1/sessions/{id}/cancel` absent —
  bones scope.
- **i5 — External-kill misattribution:** `rc < 0` maps to "cancelled by client"
  (serve.py:1341-1344) even when no client cancelled (e.g. a manual `kill`). Cosmetic; the
  cancelled stamp-vs-message wording could be neutral ("terminated by signal").
- **i6 — Orphaned grandchildren on cancel:** `proc.kill()` (serve.py:1409) signals only the direct
  child; anything atlas spawned in its own session survives. In practice atlas's llama-server
  outlives the CLI by design, so impact is limited to transient tool subprocesses.
- **i7 — In-memory store is restart-lossy** — documented in-code (serve.py:1123) and in the
  handoff; a strict reading of Tasks store rules expects retention "after completion," so this is a
  known bones limitation, not a bug to fix today.
- **i8 — Verified-shape notes:** the `[Pipeline: X]` match string is correct against the real
  producer (`agent.py:941` prints `[{summary()}]`; `orchestrator.py:53` starts the summary with
  `Pipeline: {status}`); a spec that begins with `-` (e.g. input `--help`) makes atlas argparse
  exit 2 → recorded `failed` (graceful, no crash); `model="auto"` omitted from cmd matches atlas
  default behavior.

---

## Bottom line

**Not safe to commit as-is — but it's close, and the ordering matters.** The wire skeleton is
genuinely good: sequence numbers, event ordering, the response-object shape, the error-code triple,
and the threading model all check out against the published spec, and several details (`usage:null`
wording, ignored_fields, stamp-before-kill intent) show the spec was actually read. What blocks
commit is one IO-design bug and one state machine. First, rewrite the `_atlas_stream` read loop to
unbuffered binary `os.read` + manual line splitting (M1) — the current loop can kill healthy
pipelines as a false "timeout" and, in the other direction, cannot stop a stalled pipeline at its
budget; both failure modes are proven, and the existing "verified" timeout evidence is consistent
with the false positive. Second, land the small cancel fixes as one patch (M2a-c: terminal-guard in
cancel, don't clobber non-`in_progress` status in the non-stream rc-mapping, map
`cancelled→response.failed` at the terminal emit) plus the ~15-line `_body()` hardening (M3) —
these are cheap, surgical, and eliminate every proven lie the stored records can currently tell.
Then make an explicit product call on M4 (session context): either wire real context through atlas
or reject `previous_response_id` loudly — silently fake continuation is the one behavior here I'd
call trap-grade for future clients. M5/M6 (discovery shape, Idempotency-Key) are quick follow-ups
that can land in the next commit; the Minors are quality passes. With M1–M3 done and M4 decided,
this is a solid bones commit.
