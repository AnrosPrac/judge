# Lumetrix Judge — Integration Guide

> Version 2.0 · Last updated 2026-06-11

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Endpoints at a glance](#2-endpoints-at-a-glance)
3. [Online Compiler — `POST /run`](#3-online-compiler--post-run)
4. [Judge — `POST /judge` + `GET /status/:id`](#4-judge--post-judge--get-statusid)
5. [Response field reference](#5-response-field-reference)
6. [Displaying responses in the frontend](#6-displaying-responses-in-the-frontend)
7. [Error handling](#7-error-handling)
8. [Rate limits & capacity](#8-rate-limits--capacity)
9. [Supported languages & limits](#9-supported-languages--limits)
10. [Full frontend code examples](#10-full-frontend-code-examples)

---

## 1. Authentication

Every request (except `GET /health` and `GET /`) must include an API key header:

```
X-API-Key: YOUR_API_KEY
```

Missing or invalid key → `401` / `403`.

---

## 2. Endpoints at a glance

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/run` | Online compiler — run code with stdin, get output immediately | Yes |
| `POST` | `/judge` | Submit code against testcases (async) | Yes |
| `GET` | `/status/{task_id}` | Poll result of a judge submission | Yes |
| `GET` | `/health` | Service health + live capacity metrics | No |
| `GET` | `/` | Service info | No |
| `POST` | `/auth/generate-key` | Generate a new API key | Yes |

---

## 3. Online Compiler — `POST /run`

Use this for the **"Run Code"** button on your editor page. No testcases — just code + stdin → stdout.

### Request

```http
POST /run
X-API-Key: YOUR_API_KEY
Content-Type: application/json
```

```json
{
  "language": "python",
  "sourceCode": "a = int(input())\nb = int(input())\nprint(a + b)",
  "stdin": "5\n3"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `language` | string | Yes | `python`, `c`, `cpp`, `java` |
| `sourceCode` | string | Yes | Max 100 KB |
| `stdin` | string | No | Newline-separated inputs. Empty string if the program takes no input. Max 10 KB |

**Multi-line stdin:** each `input()` call reads one line, so separate values with `\n`:
```json
{ "stdin": "Alice\n25\n" }
```

### Response

```json
{
  "stdout": "8\n",
  "stderr": "",
  "execution_time_ms": 41.2,
  "memory_mb": 9.1,
  "exit_code": 0
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `stdout` | string | Everything the program printed |
| `stderr` | string | Compiler errors, runtime exceptions, TLE/MLE messages. **Empty on success.** |
| `execution_time_ms` | float | Wall-clock run time in milliseconds |
| `memory_mb` | float | Peak memory used in MB |
| `exit_code` | int | `0` = success. Any other value = something went wrong |

### How to display it

```
if exit_code == 0 and stderr == "":
    → show stdout in green output box

if stderr != "":
    → show stderr in red error box
    (covers: compilation errors, runtime errors, TLE, MLE)
```

---

## 4. Judge — `POST /judge` + `GET /status/:id`

Use this for the **"Submit Solution"** button. Runs code against all testcases and returns a verdict.

This is **asynchronous** — you submit, get back a `task_id`, then poll until it's done.

---

### Step 1 — Submit: `POST /judge`

```http
POST /judge
X-API-Key: YOUR_API_KEY
Content-Type: application/json
```

```json
{
  "language": "python",
  "sourceCode": "a, b = map(int, input().split())\nprint(a + b)",
  "testcases": [
    { "input": "1 2", "output": "3" },
    { "input": "10 20", "output": "30" },
    { "input": "-5 5", "output": "0" }
  ]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `language` | string | Yes | `python`, `c`, `cpp`, `java` |
| `sourceCode` | string | Yes | Max 100 KB |
| `testcases` | array | Yes | 1–20 items |
| `testcases[].input` | string | Yes | stdin for this test. Max 10 KB |
| `testcases[].output` | string | Yes | Expected stdout. Max 10 KB |

**Response (immediate):**

```json
{
  "status": "queued",
  "task_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "message": "Submission queued. Poll /status/{task_id} for results.",
  "queue_position": 2
}
```

Save the `task_id`. You will poll with it.

---

### Step 2 — Poll: `GET /status/{task_id}`

```http
GET /status/f47ac10b-58cc-4372-a567-0e02b2c3d479
X-API-Key: YOUR_API_KEY
```

Poll every **1–2 seconds** until `status` is `completed` or `failed`.

**While running:**
```json
{
  "status": "processing",
  "message": "Executing submission...",
  "created_at": "2026-06-11T10:00:00"
}
```

**When done:**
```json
{
  "status": "completed",
  "created_at": "2026-06-11T10:00:00",
  "completed_at": "2026-06-11T10:00:03",
  "result": {
    "verdict": "Accepted",
    "passed": 3,
    "total": 3,
    "test_results": [ ... ],
    "avg_execution_time_ms": 38.4,
    "max_execution_time_ms": 45.1,
    "avg_memory_mb": 9.2,
    "max_memory_mb": 10.1,
    "error": null
  }
}
```

---

## 5. Response field reference

### Overall result fields

| Field | Type | Meaning |
|-------|------|---------|
| `verdict` | string | Overall result — see verdict table below |
| `passed` | int | Number of testcases that passed |
| `total` | int | Total testcases submitted |
| `avg_execution_time_ms` | float | Average run time across all testcases |
| `max_execution_time_ms` | float | Slowest testcase |
| `avg_memory_mb` | float | Average memory usage |
| `max_memory_mb` | float | Peak memory usage |
| `error` | string or null | Compilation error message if verdict is `Compilation Error` |

### Verdict values

| Verdict | Meaning | What to show user |
|---------|---------|-------------------|
| `Accepted` | All testcases passed | Green ✓ |
| `Wrong Answer` | Output didn't match expected | Red — show actual vs expected |
| `Compilation Error` | Code didn't compile | Red — show `error` field |
| `Runtime Error` | Program crashed | Red — show `error` field |
| `Time Limit Exceeded` | Took longer than 2 seconds | Yellow — "Program too slow" |
| `Memory Limit Exceeded` | Used more than 256 MB | Yellow — "Program used too much memory" |
| `Output Limit Exceeded` | Printed more than 10 KB | Yellow — "Program printed too much output" |
| `System Error` | Internal server error | Red — retry or contact support |

### Per-testcase result fields (`test_results` array)

| Field | Type | Meaning |
|-------|------|---------|
| `test_case_id` | int | 1-based index |
| `passed` | bool | Whether this testcase passed |
| `verdict` | string | Same values as overall verdict |
| `error` | string or null | For Wrong Answer: `"Expected:\n...\n\nGot:\n..."`. For Runtime Error: the actual exception message |
| `output` | string or null | First 200 chars of actual output (passed cases) |
| `expected` | string or null | First 200 chars of expected output |
| `execution_time_ms` | float | Time for this specific testcase |
| `memory_used_mb` | float | Memory for this specific testcase |

---

## 6. Displaying responses in the frontend

### Online Compiler (`/run`)

```javascript
async function runCode(language, sourceCode, stdin) {
  const res = await fetch('/run', {
    method: 'POST',
    headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ language, sourceCode, stdin })
  });
  const data = await res.json();

  if (data.exit_code === 0 && !data.stderr) {
    showOutput(data.stdout, 'success');       // green box
  } else {
    showOutput(data.stderr || data.stdout, 'error');  // red box
  }

  showStats(`${data.execution_time_ms.toFixed(1)}ms · ${data.memory_mb.toFixed(1)}MB`);
}
```

### Judge (`/judge` + polling)

```javascript
async function submitSolution(language, sourceCode, testcases) {
  // Step 1: submit
  const submitRes = await fetch('/judge', {
    method: 'POST',
    headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ language, sourceCode, testcases })
  });
  const { task_id } = await submitRes.json();

  // Step 2: poll
  while (true) {
    await sleep(1500);
    const pollRes = await fetch(`/status/${task_id}`, {
      headers: { 'X-API-Key': API_KEY }
    });
    const data = await pollRes.json();

    if (data.status === 'queued' || data.status === 'processing') continue;

    displayResult(data.result);
    break;
  }
}

function displayResult(result) {
  // Overall verdict badge
  showVerdict(result.verdict);                   // e.g. "Accepted"
  showScore(`${result.passed} / ${result.total}`);

  // Compilation error — show full message
  if (result.verdict === 'Compilation Error') {
    showError(result.error);
    return;
  }

  // Per-testcase breakdown
  result.test_results.forEach(tc => {
    const icon = tc.passed ? '✓' : '✗';
    const detail = tc.error ?? `${tc.execution_time_ms.toFixed(1)}ms`;
    showTestRow(tc.test_case_id, icon, tc.verdict, detail);
  });

  showStats(
    `Avg: ${result.avg_execution_time_ms.toFixed(1)}ms · ` +
    `Max: ${result.max_execution_time_ms.toFixed(1)}ms · ` +
    `Mem: ${result.max_memory_mb.toFixed(1)}MB`
  );
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
```

### Verdict colour mapping

```javascript
const VERDICT_COLORS = {
  'Accepted':              '#22c55e',  // green
  'Wrong Answer':          '#ef4444',  // red
  'Compilation Error':     '#ef4444',  // red
  'Runtime Error':         '#ef4444',  // red
  'Time Limit Exceeded':   '#f59e0b',  // amber
  'Memory Limit Exceeded': '#f59e0b',  // amber
  'Output Limit Exceeded': '#f59e0b',  // amber
  'System Error':          '#6b7280',  // grey
};
```

---

## 7. Error handling

| HTTP status | Meaning | What to do |
|-------------|---------|------------|
| `200` | Success | Read response body normally |
| `401` | Missing API key | Add `X-API-Key` header |
| `403` | Invalid API key | Check your key |
| `404` | Task not found (on `/status`) | Task expired (>1 hr) or service restarted |
| `422` | Validation error | Check request body — language/sizes/testcase count |
| `429` | Rate limit exceeded | Back off, retry after `Retry-After` seconds |
| `503` | Judge queue full | Retry after a few seconds |
| `500` | Server error | Retry once; if persistent, contact support |

---

## 8. Rate limits & capacity

### Rate limits (per API key)

| Limit | Value |
|-------|-------|
| Requests per window | 100 |
| Window duration | 60 seconds |
| Headers returned | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` |

### Concurrency & queue capacity

| Resource | Value | Notes |
|----------|-------|-------|
| Judge workers | 10 concurrent | Each runs one submission at a time |
| Judge queue buffer | 100 pending | Returns 503 when full |
| `/run` concurrent slots | 5 | Separate pool — never blocks judge workers |
| Task result retention | 1 hour | After that, `/status` returns 404 |

### Practical throughput estimates

| Scenario | Throughput |
|----------|------------|
| Simple submissions (1 testcase, fast code) | ~60–80 submissions/min |
| Typical submissions (5 testcases, ~200ms each) | ~30–40 submissions/min |
| Worst case (20 testcases, all TLE at 2s each) | ~3 submissions/min per worker slot |
| `/run` requests (quick programs) | ~20–30 runs/min |

### How long will a user wait?

- **Queue empty:** result in 1–5 seconds (compilation + execution)
- **Queue at 50% (50 pending):** add ~5–20 seconds depending on workload
- **Queue full (100 pending):** request rejected with `503` — tell user to retry

---

## 9. Supported languages & limits

### Languages

| Language | Compiler / Runtime | Standard |
|----------|--------------------|----------|
| `python` | Python 3.11 | — |
| `c` | GCC (gcc) | C17 |
| `cpp` | G++ (g++) | C++17 |
| `java` | OpenJDK (javac + java) | Java 17 |

### Per-execution limits

| Limit | Value |
|-------|-------|
| Time per testcase | 2 seconds |
| Memory per process | 256 MB |
| Stack size | 64 MB |
| Max output (stdout) | 10 KB per testcase |
| Max stderr captured | 4 KB |
| Max source code size | 100 KB |
| Max testcases per submission | 20 |
| Max stdin size | 10 KB per testcase |
| Max file a process can write | 16 MB |
| Max child processes | 32 (fork bomb protection) |
| Compilation timeout | 10 seconds |

### Python sandbox restrictions

The following are blocked at the AST level (before execution):

**Blocked imports:** `os`, `sys`, `subprocess`, `socket`, `shutil`, `pathlib`, `importlib`, `ctypes`, `multiprocessing`, `threading`, `signal`, `pty`, `tty`, `termios`, `fcntl`, `resource`, `gc`, `inspect`, `ast`, `dis`, `code`, `codeop`, `runpy`, `zipimport`, `pkgutil`, `site`

**Blocked builtins:** `__import__`, `open`, `eval`, `exec`, `compile`, `vars`, `dir`, `getattr`, `setattr`, `delattr`, `globals`, `locals`

**Blocked dunder attributes:** `__subclasses__`, `__bases__`, `__mro__`, `__globals__`, `__builtins__`

Standard library modules **not** blocked (safe for algorithms): `math`, `random`, `collections`, `itertools`, `functools`, `heapq`, `bisect`, `string`, `re`, `json`, `decimal`, `fractions`, `datetime`, `time`, `io`, `copy`, `struct`, `hashlib`, `typing`.

---

## 10. Full frontend code examples

### React — Online Compiler component

```jsx
import { useState } from 'react';

const API_BASE = 'https://your-judge-service.com';
const API_KEY  = 'your-api-key';

export function OnlineCompiler() {
  const [language, setLanguage]   = useState('python');
  const [code, setCode]           = useState('');
  const [stdin, setStdin]         = useState('');
  const [output, setOutput]       = useState('');
  const [error, setError]         = useState('');
  const [stats, setStats]         = useState('');
  const [loading, setLoading]     = useState(false);

  async function handleRun() {
    setLoading(true);
    setOutput(''); setError(''); setStats('');

    try {
      const res = await fetch(`${API_BASE}/run`, {
        method: 'POST',
        headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ language, sourceCode: code, stdin }),
      });

      if (res.status === 429) {
        setError('Rate limit reached. Please wait a moment.');
        return;
      }

      const data = await res.json();

      if (data.exit_code === 0 && !data.stderr) {
        setOutput(data.stdout || '(no output)');
      } else {
        setError(data.stderr || 'Unknown error');
      }
      setStats(`${data.execution_time_ms.toFixed(1)}ms · ${data.memory_mb.toFixed(1)} MB`);
    } catch (e) {
      setError('Network error — could not reach judge service.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <select value={language} onChange={e => setLanguage(e.target.value)}>
        <option value="python">Python</option>
        <option value="c">C</option>
        <option value="cpp">C++</option>
        <option value="java">Java</option>
      </select>

      <textarea value={code} onChange={e => setCode(e.target.value)} placeholder="Write your code here..." />
      <textarea value={stdin} onChange={e => setStdin(e.target.value)} placeholder="Input (stdin) — one value per line" />

      <button onClick={handleRun} disabled={loading}>
        {loading ? 'Running...' : 'Run'}
      </button>

      {output && <pre style={{ color: 'green' }}>{output}</pre>}
      {error  && <pre style={{ color: 'red'   }}>{error}</pre>}
      {stats  && <small>{stats}</small>}
    </div>
  );
}
```

### React — Judge submission with polling

```jsx
const VERDICT_COLOR = {
  'Accepted':              '#22c55e',
  'Wrong Answer':          '#ef4444',
  'Compilation Error':     '#ef4444',
  'Runtime Error':         '#ef4444',
  'Time Limit Exceeded':   '#f59e0b',
  'Memory Limit Exceeded': '#f59e0b',
  'Output Limit Exceeded': '#f59e0b',
  'System Error':          '#6b7280',
};

export function JudgeSubmit({ language, code, testcases }) {
  const [result, setResult]   = useState(null);
  const [status, setStatus]   = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    setLoading(true); setResult(null); setStatus('Submitting...');

    try {
      // 1. Submit
      const submitRes = await fetch(`${API_BASE}/judge`, {
        method: 'POST',
        headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ language, sourceCode: code, testcases }),
      });

      if (!submitRes.ok) {
        const err = await submitRes.json();
        setStatus(`Error: ${err.error || submitRes.statusText}`);
        return;
      }

      const { task_id } = await submitRes.json();
      setStatus('Queued — waiting for a worker...');

      // 2. Poll
      while (true) {
        await new Promise(r => setTimeout(r, 1500));

        const pollRes = await fetch(`${API_BASE}/status/${task_id}`, {
          headers: { 'X-API-Key': API_KEY },
        });
        const data = await pollRes.json();

        if (data.status === 'queued')      { setStatus('Queued...'); continue; }
        if (data.status === 'processing')  { setStatus('Running testcases...'); continue; }
        if (data.status === 'failed')      { setStatus('System error — please retry.'); break; }

        // completed
        setResult(data.result);
        setStatus('');
        break;
      }
    } catch (e) {
      setStatus('Network error.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <button onClick={handleSubmit} disabled={loading}>
        {loading ? status : 'Submit'}
      </button>

      {result && (
        <div>
          <h3 style={{ color: VERDICT_COLOR[result.verdict] }}>{result.verdict}</h3>
          <p>{result.passed} / {result.total} testcases passed</p>

          {result.error && <pre style={{ color: 'red' }}>{result.error}</pre>}

          <small>
            Avg: {result.avg_execution_time_ms.toFixed(1)}ms ·
            Max: {result.max_execution_time_ms.toFixed(1)}ms ·
            Mem: {result.max_memory_mb.toFixed(1)} MB
          </small>

          <table>
            <thead>
              <tr><th>#</th><th>Verdict</th><th>Time</th><th>Memory</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {result.test_results.map(tc => (
                <tr key={tc.test_case_id} style={{ color: tc.passed ? '#22c55e' : '#ef4444' }}>
                  <td>{tc.test_case_id}</td>
                  <td>{tc.verdict}</td>
                  <td>{tc.execution_time_ms.toFixed(1)}ms</td>
                  <td>{tc.memory_used_mb.toFixed(1)} MB</td>
                  <td><pre style={{ fontSize: '0.75rem' }}>{tc.error ?? ''}</pre></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

---

*For questions or issues, check `GET /health` first — it shows live queue size and worker counts.*
