#!/usr/bin/env python3

import http.server
import socketserver
import json
import os
import glob
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, unquote
from collections import Counter, defaultdict

UTC = timezone.utc
SESSIONS_DIR = "/Users/fari/.openclaw/agents/saia/sessions/"
M_CLAUDE = "claude-sonnet-4-20250514"
M_GEMINI = "gemini-2.5-flash"
M_GEMMA = "gemma4:31b"
M_QWEN = "qwen3.5:122b-a10b"
CANONICAL_ORDER = (M_CLAUDE, M_GEMINI, M_GEMMA, M_QWEN)

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_SAIA = os.path.dirname(DASHBOARD_DIR)
TASKS_JSON = os.path.join(WORKSPACE_SAIA, "board-tasks.json")


def _tasks_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_tasks_disk():
    if not os.path.isfile(TASKS_JSON):
        save_tasks_disk([])
        return []
    try:
        with open(TASKS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        save_tasks_disk([])
        return []
    if not isinstance(data, list):
        save_tasks_disk([])
        return []
    return data


def save_tasks_disk(tasks):
    with open(TASKS_JSON, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)
        f.write("\n")


def _session_log_files():
    out = []
    for p in glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl")):
        base = os.path.basename(p)
        if ".checkpoint." in base:
            continue
        out.append(p)
    return out


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def canonical_model_id(raw):
    if not raw:
        return None
    u = raw.lower()
    if "gemini" in u:
        return M_GEMINI
    if "gemma" in u:
        return M_GEMMA
    if ("qwen3.5" in u or "qwen3_5" in u) and "122" in u:
        return M_QWEN
    if "claude" in u or "sonnet" in u or "anthropic" in u:
        return M_CLAUDE
    return None


def agent_from_canonical(cid):
    return {
        M_GEMINI: "Coding Agent",
        M_GEMMA: "Primary Worker",
        M_QWEN: "SEO Specialist",
        M_CLAUDE: "SAIA Manager",
    }.get(cid)


def _tool_label(name, arguments):
    if not isinstance(arguments, dict):
        arguments = {}
    if name == "sessions_spawn":
        t = arguments.get("task") or arguments.get("label") or ""
        return (t[:72] + "…") if len(t) > 72 else (t or name)
    if name == "exec":
        c = arguments.get("command") or ""
        return (c[:56] + "…") if len(c) > 56 else (c or name)
    return name or "tool"


def model_to_agent(model):
    cid = canonical_model_id(model)
    if cid:
        return agent_from_canonical(cid)
    m = (model or "").lower()
    if "gemini" in m:
        return "Coding Agent"
    if "gemma" in m:
        return "Primary Worker"
    if "qwen" in m:
        return "SEO Specialist"
    if "claude" in m or "anthropic" in m or "sonnet" in m:
        return "SAIA Manager"
    return "SAIA Manager"


class OpenClawDashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            directory="/Users/fari/.openclaw/workspace-saia/agent-dashboard",
            **kwargs,
        )

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json_response(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def do_GET(self):
        parsed_path = urlparse(self.path)
        p = parsed_path.path.rstrip("/") or "/"
        if p == "/openclaw-config":
            self.serve_openclaw_config()
        elif p == "/session-data":
            self.serve_real_session_data()
        elif p == "/activity-data":
            self.serve_activity_data()
        elif p in ("/tasks", "/api/tasks"):
            self.serve_kanban_tasks()
        else:
            super().do_GET()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        p = parsed_path.path.rstrip("/") or "/"
        if p == "/api/tasks":
            self.handle_tasks_post()
        else:
            self.send_error(404)

    def do_PUT(self):
        parsed_path = urlparse(self.path)
        p = parsed_path.path.rstrip("/") or "/"
        if p == "/api/tasks":
            self.handle_tasks_put()
        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed_path = urlparse(self.path)
        p = parsed_path.path.rstrip("/") or "/"
        if p.startswith("/api/tasks/"):
            tid = unquote(p.split("/api/tasks/", 1)[-1])
            if tid:
                self.handle_tasks_delete(tid)
                return
        self.send_error(404)

    def serve_kanban_tasks(self):
        try:
            tasks = load_tasks_disk()
            self._json_response(200, tasks)
        except Exception as e:
            print(f"kanban tasks GET: {e}")
            self.send_error(500, str(e))

    def handle_tasks_post(self):
        try:
            raw = self._read_body()
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        tasks = load_tasks_disk()
        now = _tasks_now_iso()
        tid = f"task_{uuid.uuid4().hex[:12]}"
        task = {
            "id": tid,
            "title": (data.get("title") or "").strip() or "Untitled",
            "category": data.get("category") or "Work",
            "priority": data.get("priority") or "Normal",
            "trackStatus": data.get("trackStatus") or "On Track",
            "status": data.get("status") or "todo",
            "dueDate": data.get("dueDate") or "",
            "createdAt": now,
            "updatedAt": now,
        }
        tasks.append(task)
        save_tasks_disk(tasks)
        self._json_response(201, task)

    def handle_tasks_put(self):
        try:
            raw = self._read_body()
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        tid = data.get("id")
        if not tid:
            self.send_error(400, "Missing id")
            return
        tasks = load_tasks_disk()
        for i, t in enumerate(tasks):
            if t.get("id") == tid:
                merged = {**t, **{k: v for k, v in data.items() if k != "id"}}
                merged["id"] = tid
                merged["updatedAt"] = _tasks_now_iso()
                tasks[i] = merged
                save_tasks_disk(tasks)
                self._json_response(200, merged)
                return
        self.send_error(404, "Task not found")

    def handle_tasks_delete(self, tid):
        tasks = load_tasks_disk()
        n = len(tasks)
        tasks = [t for t in tasks if t.get("id") != tid]
        if len(tasks) == n:
            self.send_error(404, "Task not found")
            return
        save_tasks_disk(tasks)
        self._json_response(200, {"ok": True, "id": tid})

    def parse_session_logs(self):
        model_usage = Counter()
        tasks_by_date = Counter()
        agent_activity = defaultdict(lambda: {"tasks": 0, "last_activity": None})
        agent_last = {}
        agent_tasks_today = defaultdict(int)
        total_messages = 0
        successful_tasks = 0
        failed_tasks = 0
        now = datetime.now(UTC)
        today = now.date()
        week_start = today - timedelta(days=6)

        for log_file in _session_log_files():
            try:
                with open(log_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        total_messages += 1
                        ts = _parse_ts(entry.get("timestamp", ""))
                        if ts is None:
                            continue
                        d = ts.date()

                        if entry.get("type") == "model_change":
                            mid = entry.get("modelId") or ""
                            prov = entry.get("provider") or ""
                            mk = f"{prov}/{mid}".strip("/") if prov else mid
                            cid = canonical_model_id(mk) or canonical_model_id(mid)
                            if cid:
                                model_usage[cid] += 1
                            continue

                        if entry.get("type") != "message":
                            continue

                        msg = entry.get("message") or {}
                        role = msg.get("role")

                        if role == "toolResult":
                            if msg.get("isError"):
                                failed_tasks += 1
                            else:
                                successful_tasks += 1
                            continue

                        if role != "assistant":
                            continue

                        mdl = msg.get("model") or ""
                        cid = canonical_model_id(mdl)
                        if cid:
                            model_usage[cid] += 1

                        agent = (
                            agent_from_canonical(cid)
                            if cid
                            else model_to_agent(mdl)
                        )
                        content = msg.get("content")
                        if not isinstance(content, list):
                            continue

                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") != "toolCall":
                                continue
                            tname = block.get("name") or "tool"
                            args = block.get("arguments")
                            if not isinstance(args, dict):
                                args = {}
                            label = _tool_label(tname, args)
                            ts_str = entry.get("timestamp", "")
                            tasks_by_date[d] += 1
                            agent_activity[agent]["tasks"] += 1
                            if d == today:
                                agent_tasks_today[agent] += 1
                            prev = agent_activity[agent]["last_activity"]
                            if not prev or ts_str > prev:
                                agent_activity[agent]["last_activity"] = ts_str
                            old = agent_last.get(agent)
                            if not old or ts_str > old[0]:
                                agent_last[agent] = (ts_str, label)

            except OSError as e:
                print(f"Error reading {log_file}: {e}")

        tasks_today = tasks_by_date.get(today, 0)
        weekly_tasks = sum(
            c for day, c in tasks_by_date.items() if week_start <= day <= today
        )
        denom = successful_tasks + failed_tasks
        success_rate = (
            (successful_tasks / denom * 100) if denom else 100.0
        )

        return {
            "model_usage": dict(model_usage),
            "tasks_today": tasks_today,
            "weekly_tasks": weekly_tasks,
            "agent_activity": dict(agent_activity),
            "agent_tasks_today": dict(agent_tasks_today),
            "agent_last_task": {k: v[1] for k, v in agent_last.items()},
            "total_messages": total_messages,
            "success_rate": success_rate,
        }

    @staticmethod
    def four_model_distribution(model_usage):
        dist = {}
        total = sum(model_usage.get(m, 0) for m in CANONICAL_ORDER)
        if total <= 0:
            for m in CANONICAL_ORDER:
                dist[m] = 0.0
            return dist
        for m in CANONICAL_ORDER:
            dist[m] = round(model_usage.get(m, 0) / total * 100, 1)
        return dist

    def serve_real_session_data(self):
        try:
            stats = self.parse_session_logs()
            act = stats["agent_activity"]
            att = stats.get("agent_tasks_today") or {}
            lt = stats["agent_last_task"]

            agents = [
                {
                    "name": "SAIA Manager",
                    "role": "Strategic Orchestrator",
                    "deployment": "Cloud",
                    "model": "claude-sonnet-4-20250514",
                    "lastTask": lt.get("SAIA Manager"),
                    "lastActivity": act.get("SAIA Manager", {}).get("last_activity"),
                    "tasksToday": att.get("SAIA Manager", 0),
                    "status": "active",
                },
                {
                    "name": "Coding Agent",
                    "role": "Full Stack Developer",
                    "deployment": "Cloud",
                    "model": "gemini-2.5-flash",
                    "lastTask": lt.get("Coding Agent"),
                    "lastActivity": act.get("Coding Agent", {}).get("last_activity"),
                    "tasksToday": att.get("Coding Agent", 0),
                    "status": "active",
                },
                {
                    "name": "Primary Worker",
                    "role": "Content Generator",
                    "deployment": "Local",
                    "model": "gemma4:31b",
                    "lastTask": lt.get("Primary Worker"),
                    "lastActivity": act.get("Primary Worker", {}).get("last_activity"),
                    "tasksToday": att.get("Primary Worker", 0),
                    "status": "active",
                },
                {
                    "name": "SEO Specialist",
                    "role": "Research & SEO Analyst",
                    "deployment": "Local",
                    "model": "qwen3.5:122b-a10b",
                    "lastTask": lt.get("SEO Specialist"),
                    "lastActivity": act.get("SEO Specialist", {}).get("last_activity"),
                    "tasksToday": att.get("SEO Specialist", 0),
                    "status": "active",
                },
            ]

            mu = stats["model_usage"]
            model_dist = self.four_model_distribution(mu)

            most_active = max(
                agents,
                key=lambda a: (
                    att.get(a["name"], 0),
                    act.get(a["name"], {}).get("tasks", 0),
                ),
                default=agents[0],
            )

            data = {
                "agents": agents,
                "totalAgents": len(agents),
                "totalModels": len([m for m in CANONICAL_ORDER if mu.get(m, 0) > 0]),
                "tasksToday": stats["tasks_today"],
                "tasksThisWeek": stats["weekly_tasks"],
                "successRate": round(stats["success_rate"], 1),
                "mostActiveAgent": {
                    "name": most_active["name"],
                    "tasks": most_active["tasksToday"],
                },
                "modelDistribution": model_dist,
                "totalMessages": stats["total_messages"],
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        except Exception as e:
            print(f"Error serving real session data: {e}")
            self.send_error(500, f"Failed to parse session data: {e}")

    def serve_openclaw_config(self):
        try:
            config_path = "/Users/fari/.openclaw/openclaw.json"
            with open(config_path, "r") as f:
                config_data = json.load(f)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(config_data).encode())
        except Exception as e:
            print(f"Error loading OpenClaw config: {e}")
            self.send_error(500, f"Failed to load config: {e}")

    def collect_activity_rows(self):
        rows = []
        files = _session_log_files()
        files.sort(key=os.path.getmtime, reverse=True)
        for log_file in files[:12]:
            try:
                with open(log_file, "r") as f:
                    chunk = f.readlines()[-800:]
                for line in chunk:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "message":
                        continue
                    msg = entry.get("message") or {}
                    if msg.get("role") != "assistant":
                        continue
                    mdl = msg.get("model") or ""
                    ag = model_to_agent(mdl)
                    ts = entry.get("timestamp", "")
                    for block in msg.get("content") or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "toolCall":
                            continue
                        tname = block.get("name") or "tool"
                        args = block.get("arguments")
                        if not isinstance(args, dict):
                            args = {}
                        rows.append(
                            {
                                "timestamp": ts,
                                "message": _tool_label(tname, args),
                                "type": "toolCall",
                                "tool": tname,
                                "agent": ag,
                            }
                        )
            except OSError as e:
                print(f"activity scan {log_file}: {e}")
        rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return rows[:24]

    def serve_activity_data(self):
        try:
            rows = self.collect_activity_rows()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(rows).encode())
        except Exception as e:
            print(f"Error serving activity data: {e}")
            self.send_error(500, f"Failed to load activity: {e}")

if __name__ == "__main__":
    PORT = 8080
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), OpenClawDashboardHandler) as httpd:
        print(f"OpenClaw Dashboard Server running on http://localhost:{PORT}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("Server stopped")
