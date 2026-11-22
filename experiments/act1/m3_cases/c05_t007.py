import argparse
import json
from dataclasses import dataclass, field


@dataclass
class NodeExecution:
    node_name: str
    duration_ms: float
    tokens: int
    outcome: str
    retry_count: int = 0


@dataclass
class SessionTrace:
    session_id: str
    task: str
    executions: list[NodeExecution] = field(default_factory=list)
    total_tokens: int = 0
    total_cost: float = 0.0
    succeeded: bool = True


def main():
    parser = argparse.ArgumentParser(description="Atlas Scaffold Optimizer")
    parser.add_argument("--sessions", default=None, help="Path to sessions JSON file")
    parser.add_argument("--demo", action="store_true", help="Run with synthetic data")
    args = parser.parse_args()

    if args.demo or not args.sessions:
        run_demo()
    else:
        with open(args.sessions) as f:
            raw = json.load(f)
        sessions = [SessionTrace(**s) for s in raw]
        issues = detect_inefficiencies(sessions)
        result = generate_optimization(issues)
        print(json.dumps(result, indent=2))
