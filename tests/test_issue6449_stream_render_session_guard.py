"""Regression tests for the cross-session streamed-render leak (upstream PR 6502).

While session A streams, switching to session B used to render A's tokens into
B's message pane. Routing was never the problem: the server only emits to the
owning room and INFLIGHT is keyed by session_id. The leak was in the view layer
-- ``attachLiveStream``'s throttled render pipeline defers DOM writes through
requestAnimationFrame/setTimeout, and the deferred callbacks carried no
``_isActiveSession()`` guard even though the SSE handlers that schedule them do.

The interesting case is a *timing* one, so it is covered behaviorally with a
Node harness rather than by asserting on source text: schedule a render while
the session is active, switch away, then let the rAF callback fire. A structural
check cannot distinguish that from the already-working synchronous path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _extract_function(source: str, name: str) -> str:
    """Return the full source of ``function name(...)`` from messages.js.

    Brace-depth tracking that skips strings and comments -- messages.js is large
    and hand-formatted, so naive substring slicing picks up the wrong closer.
    """
    start = source.find(f"function {name}(")
    assert start >= 0, f"{name} not found in messages.js"

    # Walk the parameter list first: a default like `options={}` puts a brace
    # before the body, and starting the depth count there ends the extraction
    # after one character.
    paren = source.index("(", start)
    paren_depth = 0
    for i in range(paren, len(source)):
        if source[i] == "(":
            paren_depth += 1
        elif source[i] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                brace_start = source.index("{", i)
                break
    else:
        raise AssertionError(f"unbalanced parens in the signature of {name}")

    depth = 0
    quote = None
    escaped = False
    line_comment = False
    block_comment = False

    for i in range(brace_start, len(source)):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""

        if line_comment:
            if ch == "\n":
                line_comment = False
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue

        if ch == "/" and nxt == "/":
            line_comment = True
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            continue
        if ch in "\"'`":
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]

    raise AssertionError(f"unbalanced braces while extracting {name}")


# Everything _scheduleRender touches up to (and just past) the rAF guard. The
# guard short-circuits before any real render work, so the stubs only need to
# cover the scheduling path -- if a guard regresses, execution falls through to
# the DOM writes and the harness throws instead of silently passing.
_HARNESS = r"""
%(scheduleRender)s

let _cachedParsed=null, _cachedParsedText='', _cachedParsedReasoning='';
let assistantText='hello', liveReasoningText='';
let _renderPending=false, _streamFinalized=false;
let _pendingRafHandle=null, _lastRenderMs=0;
let _sessionActive=true;
let clockMs=1000;

const calls={raf:0, timeout:0, domWrite:0};
const rafQueue=[];

function _isActiveSession(){ return _sessionActive; }
function _shouldUseLiveProseFade(){ return false; }
const performance={ now(){ return clockMs; } };
function requestAnimationFrame(cb){ calls.raf++; rafQueue.push(cb); return calls.raf; }
function setTimeout(cb, _ms){ calls.timeout++; rafQueue.push(cb); return calls.timeout; }
// First side effect past the rAF guard. Reaching it means the render made it
// to the DOM-write phase. Throw a sentinel to stop there: everything after it
// is real rendering work this harness deliberately does not stub, so letting
// execution continue would fail with an unrelated ReferenceError.
const SENTINEL='__reached_dom_write__';
const window={ _fixMobileScrollJank(){ calls.domWrite++; throw new Error(SENTINEL); } };

function flushDeferred(){
  while(rafQueue.length){
    try { rafQueue.shift()(); }
    catch(err){ if(!String(err && err.message).includes(SENTINEL)) throw err; }
  }
}

%(body)s
"""


def _run_node(body: str, tmp_path: Path) -> dict:
    assert NODE is not None, "node is required"
    script = _HARNESS % {
        "scheduleRender": _extract_function(MESSAGES_JS, "_scheduleRender"),
        "body": body,
    }
    script_path = tmp_path / "issue6449-stream-render-session-guard.mjs"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(script_path)],
        cwd=str(REPO),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    lines = [ln.strip() for ln in completed.stdout.splitlines() if ln.strip()]
    assert lines, f"node produced no output\nstdout={completed.stdout}\nstderr={completed.stderr}"
    return json.loads(lines[-1])


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_render_reaches_dom_while_session_stays_active(tmp_path):
    """Control: the guards must not break the normal same-session render."""
    metrics = _run_node(
        """
        _scheduleRender();
        flushDeferred();
        console.log(JSON.stringify({domWrite:calls.domWrite, pending:_renderPending}));
        """,
        tmp_path,
    )
    assert metrics["domWrite"] == 1, (
        "with the session still active the scheduled render must reach the DOM-write "
        "phase -- otherwise the guards have broken streaming altogether"
    )


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_pending_raf_render_is_dropped_when_session_switches_after_schedule(tmp_path):
    """The bug: schedule while active, switch session, then let the rAF fire.

    This is the window the SSE-handler guards cannot cover -- they run at
    schedule time, and the session switch happens after.
    """
    metrics = _run_node(
        """
        _scheduleRender();            // scheduled while A is the active pane
        const scheduled = calls.raf + calls.timeout;
        _sessionActive = false;       // user switches to session B
        flushDeferred();              // deferred callback fires against B
        console.log(JSON.stringify({scheduled:scheduled, domWrite:calls.domWrite}));
        """,
        tmp_path,
    )
    assert metrics["scheduled"] == 1, "the render should have been scheduled while active"
    assert metrics["domWrite"] == 0, (
        "a render scheduled for session A must not write DOM after the user switched "
        "to session B -- this is the cross-session text leak"
    )


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_scheduling_is_refused_once_the_session_is_no_longer_active(tmp_path):
    """A render must not even be scheduled for a session that is not on screen."""
    metrics = _run_node(
        """
        _sessionActive = false;
        _scheduleRender();
        console.log(JSON.stringify({
          scheduled: calls.raf + calls.timeout,
          pending: _renderPending,
          domWrite: calls.domWrite,
        }));
        """,
        tmp_path,
    )
    assert metrics["scheduled"] == 0, "no rAF/timeout should be armed for an inactive session"
    assert metrics["domWrite"] == 0
    assert metrics["pending"] is False, (
        "_renderPending must stay false when scheduling is refused, otherwise the flag "
        "latches on and blocks every later render for this stream"
    )


def test_flush_pending_segment_render_guards_before_cancelling_the_pending_frame():
    """Belt-and-braces guard on the synchronous flush path.

    Its callers already gate on _isActiveSession(); the guard exists so a future
    call-site cannot leak. Asserted structurally -- there is no timing window
    here to reproduce, only the ordering that matters: the guard must precede
    the first side effect (cancelling the armed frame).
    """
    body = _extract_function(MESSAGES_JS, "_flushPendingSegmentRender")
    guard = body.find("if(!_isActiveSession()) return;")
    assert guard >= 0, (
        "_flushPendingSegmentRender must bail when its stream's session is not the "
        "active pane"
    )
    cancel = body.find("_cancelAnimationFramePendingStreamRender()")
    assert cancel >= 0, "expected the pending-frame cancel to still be there"
    assert guard < cancel, (
        "the active-session guard must run before the pending frame is cancelled -- "
        "otherwise a wrong-session flush tears down the real pane's armed render"
    )
