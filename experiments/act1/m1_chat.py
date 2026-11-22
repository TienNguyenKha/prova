"""M1: a terminal chat that keeps the full history and prints token usage every turn.

Run by hand:
  uv run python experiments/act1/m1_chat.py          # interactive; empty line or Ctrl-D quits
  uv run python experiments/act1/m1_chat.py --auto   # 15 scripted prompts with the cheap model

The usage log is written to experiments/runs/ (git-ignored) when the chat ends.
"""

import argparse
import time

import anthropic
from anthropic.types import MessageParam

from prova.config import load_settings
from prova.llm.usage import UsageLog, UsageRecord

# One coherent topic, each question building on the previous answers, so the history keeps growing.
# The last question refers back to everything above: it only works because we resend the history.
LENGTH_HINT = " Answer in about 100 words, in Vietnamese."
AUTO_PROMPTS = [
    "Python là gì và ai tạo ra nó?",
    "Vì sao Python được xem là dễ học?",
    "Giải thích sự khác nhau giữa list và tuple.",
    "Khi nào nên dùng dictionary?",
    "Giải thích vòng lặp for với một ví dụ.",
    "Hàm trong Python được định nghĩa thế nào?",
    "Giải thích khái niệm lớp (class) và đối tượng.",
    "Kế thừa là gì? Cho một ví dụ.",
    "Xử lý ngoại lệ bằng try/except hoạt động ra sao?",
    "Môi trường ảo (venv) dùng để làm gì?",
    "Giải thích decorator ngắn gọn.",
    "Generator khác list ở điểm nào?",
    "Type hint giúp ích gì?",
    "Kiểm thử bằng pytest có lợi gì?",
    "Từ tất cả những điều trên, hãy tóm tắt lộ trình học Python cho người mới.",
]


def run_turn(
    client: anthropic.Anthropic,
    model: str,
    messages: list[MessageParam],
    log: UsageLog,
    user_text: str,
    *,
    max_tokens: int,
    preview: int | None = None,
) -> None:
    """Send one user message with the whole history, print the answer and usage, record usage."""
    turn = len(log.records) + 1
    messages.append({"role": "user", "content": user_text})

    started = time.perf_counter()
    response = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
    latency = time.perf_counter() - started

    text = "".join(block.text for block in response.content if block.type == "text")
    # Forgetting this line makes the model "forget" its own previous answer.
    messages.append({"role": "assistant", "content": text})

    usage = response.usage
    rec = UsageRecord(
        turn=turn,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        latency_s=latency,
    )
    log.add(rec)

    shown = text if preview is None or len(text) <= preview else text[:preview] + "..."
    print(f"assistant> {shown}")
    if response.stop_reason != "end_turn":
        print(f"  (stop_reason={response.stop_reason}: the answer may be incomplete)")
    print(
        f"[turn {turn}] input={rec.input_tokens} output={rec.output_tokens} | "
        f"cumulative input={log.total_input_tokens} output={log.total_output_tokens} | {latency:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="M1 chat experiment")
    parser.add_argument("--auto", action="store_true", help="send 15 scripted prompts with the cheap model")
    args = parser.parse_args()

    settings = load_settings()
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.request_timeout_s,
    )
    runs_dir = settings.project_root / "experiments" / "runs"
    csv_path = runs_dir / ("m1_usage_auto.csv" if args.auto else "m1_usage.csv")

    messages: list[MessageParam] = []  # the API is stateless: we resend all of this every turn
    log = UsageLog()

    try:
        if args.auto:
            model = settings.cheap_model
            print(f"Auto mode with {model}: {len(AUTO_PROMPTS)} turns")
            for prompt in AUTO_PROMPTS:
                print(f"\nyou> {prompt}")
                run_turn(client, model, messages, log, prompt + LENGTH_HINT, max_tokens=600, preview=120)
        else:
            print("Type a message. Empty line or Ctrl-D quits.")
            while True:
                try:
                    user_text = input("\nyou> ").strip()
                except EOFError:
                    break
                if not user_text:
                    break
                run_turn(client, settings.model, messages, log, user_text, max_tokens=512)
    finally:
        # Written even on Ctrl-C or an API error, so a long session is never lost.
        if log.records:
            log.to_csv(csv_path)
            print(f"\nUsage log written to {csv_path}")


if __name__ == "__main__":
    main()
