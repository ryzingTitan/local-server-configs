#!/usr/bin/env python3
"""
Benchmark an Ollama model's generation speed (tokens/sec).

Runs one warm-up pass (to load the model into RAM so the measured runs
reflect steady-state speed, not cold-load time), then N measured runs,
and reports per-run and averaged results.

Uses only the Python standard library -- no pip installs needed.

Usage:
    ./ollama_bench.py MODEL [-n RUNS] [-p PROMPT] [--host URL]

Examples:
    ./ollama_bench.py qwen2.5:3b
    ./ollama_bench.py llama3.2:3b -n 10
    ./ollama_bench.py qwen2.5:1.5b --host http://nuc.your-tailnet.ts.net:11434
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

DEFAULT_PROMPT = (
    "Explain in detail how a turbocharger works in an internal combustion "
    "engine, including the role of the compressor, turbine, wastegate, and "
    "intercooler. Write at least four paragraphs."
)


def run_once(host, model, prompt):
    """Send one non-streaming generate request and return timing metrics."""
    url = f"{host.rstrip('/')}/api/generate"
    payload = json.dumps(
        {"model": model, "prompt": prompt, "stream": False}
    ).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )

    start = time.time()
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    wall = time.time() - start

    # Durations from Ollama are in nanoseconds.
    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)
    prompt_eval_count = data.get("prompt_eval_count", 0)
    prompt_eval_duration = data.get("prompt_eval_duration", 0)
    load_duration = data.get("load_duration", 0)

    gen_rate = eval_count / (eval_duration / 1e9) if eval_duration else 0.0
    prompt_rate = (
        prompt_eval_count / (prompt_eval_duration / 1e9)
        if prompt_eval_duration
        else 0.0
    )

    return {
        "gen_rate": gen_rate,
        "prompt_rate": prompt_rate,
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
        "load_s": load_duration / 1e9,
        "wall_s": wall,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark an Ollama model's tokens/sec."
    )
    parser.add_argument("model", help="Ollama model name, e.g. qwen2.5:3b")
    parser.add_argument(
        "-n", "--runs", type=int, default=5,
        help="Number of measured runs (default: 5)",
    )
    parser.add_argument(
        "-p", "--prompt", default=DEFAULT_PROMPT,
        help="Prompt to use for the benchmark",
    )
    parser.add_argument(
        "--host", default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434)",
    )
    args = parser.parse_args()

    if args.runs < 1:
        print("Error: --runs must be at least 1", file=sys.stderr)
        sys.exit(1)

    print(f"Model: {args.model}")
    print(f"Host:  {args.host}")
    print(f"Runs:  {args.runs} (plus 1 warm-up)")
    print()

    # Warm-up run: loads the model into RAM so measured runs are steady-state.
    print("Warm-up run (loading model, not counted)...", flush=True)
    try:
        warm = run_once(args.host, args.model, args.prompt)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"\nHTTP error {e.code} from Ollama: {body}", file=sys.stderr)
        print("Is the model name correct and pulled?", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\nCould not reach Ollama at {args.host}: {e.reason}", file=sys.stderr)
        print("Is Ollama running and listening on that address?", file=sys.stderr)
        sys.exit(1)
    print(f"  model load time: {warm['load_s']:.2f}s\n")

    gen_rates = []
    prompt_rates = []

    for i in range(1, args.runs + 1):
        r = run_once(args.host, args.model, args.prompt)
        gen_rates.append(r["gen_rate"])
        prompt_rates.append(r["prompt_rate"])
        print(
            f"Run {i}: {r['gen_rate']:6.1f} tok/s generation "
            f"({r['eval_count']} tokens)  |  "
            f"{r['prompt_rate']:6.1f} tok/s prompt"
        )

    line = "=" * 52
    print(f"\n{line}")
    print(f"Results for {args.model} over {args.runs} runs")
    print(line)
    print("Generation speed (the headline number):")
    print(f"  average: {statistics.mean(gen_rates):6.1f} tok/s")
    if len(gen_rates) > 1:
        print(f"  stdev:   {statistics.stdev(gen_rates):6.1f} tok/s")
        print(f"  min/max: {min(gen_rates):.1f} / {max(gen_rates):.1f} tok/s")
    print("Prompt processing speed:")
    print(f"  average: {statistics.mean(prompt_rates):6.1f} tok/s")
    print(line)


if __name__ == "__main__":
    main()