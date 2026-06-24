#!/usr/bin/env python3
"""
Benchmark generation speed (tokens/sec) for a local LLM served by either
Ollama or a llama.cpp server (llama-server).

Runs one warm-up pass (to load the model into RAM/VRAM so the measured
runs reflect steady-state speed, not cold-load time), then N measured
runs, and reports per-run and averaged results.

--max-tokens applies to BOTH backends, ensuring each run generates the
same number of tokens so generation rates are directly comparable.

llama.cpp router mode: when llama-server is running with --models-preset,
pass the model name defined in the preset .ini file as the MODEL argument.
The server uses it to route the request to the correct child process.

Single-model llama.cpp: omit MODEL or pass any label -- the field is
included in the request but ignored by a non-router server.

Uses only the Python standard library -- no pip installs needed.

Usage:
    ./llm_bench.py MODEL --backend ollama  [-n RUNS] [-p PROMPT] [--host URL] [--max-tokens N]
    ./llm_bench.py MODEL --backend llamacpp [-n RUNS] [-p PROMPT] [--host URL] [--max-tokens N]

Examples:
    # Ollama (model name required)
    ./llm_bench.py qwen2.5:3b --backend ollama
    ./llm_bench.py llama3.1:8b --backend ollama -n 10 --max-tokens 400
    ./llm_bench.py qwen2.5:3b --backend ollama --host http://nuc.your-tailnet.ts.net:11434

    # llama.cpp single-model server (model name optional, used as display label only)
    ./llm_bench.py --backend llamacpp
    ./llm_bench.py "Gemma4-E2B" --backend llamacpp --host http://localhost:8080

    # llama.cpp router mode (model name must match the [section] in models.ini)
    ./llm_bench.py gemma-e2b --backend llamacpp
    ./llm_bench.py gemma-e4b --backend llamacpp -n 10

    # Fair apples-to-apples comparison across backends
    ./llm_bench.py qwen2.5:3b --backend ollama --max-tokens 400
    ./llm_bench.py "Qwen2.5-3B" --backend llamacpp --max-tokens 400

    # Benchmark every model in the router in one shell loop
    # for model in gemma-e2b gemma-e4b; do
    #     ./llm_bench.py "$model" --backend llamacpp --max-tokens 400
    # done
"""

import argparse
import json
import statistics
import sys
import urllib.error
import urllib.request

DEFAULT_PROMPT = (
    "Explain in detail how a turbocharger works in an internal combustion "
    "engine, including the role of the compressor, turbine, wastegate, and "
    "intercooler. Write at least four paragraphs."
)

DEFAULT_HOSTS = {
    "ollama": "http://localhost:11434",
    "llamacpp": "http://localhost:8080",
}


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_once_ollama(host, model, prompt, max_tokens):
    """One non-streaming /api/generate call against Ollama."""
    url = f"{host.rstrip('/')}/api/generate"
    data = post_json(url, {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Match the token cap used by the llama.cpp backend so generation
        # counts are comparable across backends.
        "options": {"num_predict": max_tokens},
        # Disable Ollama's prompt KV-cache between runs so prompt_eval_count
        # reflects real prefill work rather than cache hits. Without this,
        # repeated identical prompts report artificially inflated prompt rates.
        "keep_alive": 0,
    })

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
        "gen_tokens": eval_count,
        "prompt_tokens": prompt_eval_count,
        "load_s": load_duration / 1e9,
    }


def run_once_llamacpp(host, model, prompt, max_tokens):
    """
    One non-streaming /completion call against a llama.cpp server.

    In single-model mode the 'model' field is ignored by the server.
    In router mode it is required and tells the router which child
    process (i.e. which [section] from models.ini) should handle the
    request.
    """
    url = f"{host.rstrip('/')}/completion"
    payload = {"prompt": prompt, "n_predict": max_tokens, "stream": False}
    if model:
        # Include the model name so the router can select the right
        # child process. Safe to include for single-model servers too --
        # they ignore the field rather than returning an error.
        payload["model"] = model

    data = post_json(url, payload)

    timings = data.get("timings", {})

    # Prefer the rate fields llama.cpp computes directly; fall back to
    # deriving rate from token count / duration if a field is missing,
    # since exact field names have shifted slightly across server versions.
    gen_rate = timings.get("predicted_per_second")
    if gen_rate is None:
        predicted_n = timings.get("predicted_n", data.get("tokens_predicted", 0))
        predicted_ms = timings.get("predicted_ms")
        gen_rate = predicted_n / (predicted_ms / 1000) if predicted_ms else 0.0

    prompt_rate = timings.get("prompt_per_second")
    if prompt_rate is None:
        prompt_n = timings.get("prompt_n", data.get("tokens_evaluated", 0))
        prompt_ms = timings.get("prompt_ms")
        prompt_rate = prompt_n / (prompt_ms / 1000) if prompt_ms else 0.0

    gen_tokens = timings.get("predicted_n", data.get("tokens_predicted", 0))
    prompt_tokens = timings.get("prompt_n", data.get("tokens_evaluated", 0))

    # Reflect back the model the router actually served, if it tells us.
    # Useful confirmation that the router routed to the right child.
    served_model = data.get("model") or model

    return {
        "gen_rate": gen_rate,
        "prompt_rate": prompt_rate,
        "gen_tokens": gen_tokens,
        "prompt_tokens": prompt_tokens,
        "load_s": None,   # model loaded at server startup, not per-request
        "served_model": served_model,
    }


def run_once(backend, host, model, prompt, max_tokens):
    if backend == "ollama":
        result = run_once_ollama(host, model, prompt, max_tokens)
        result["served_model"] = model
        return result
    return run_once_llamacpp(host, model, prompt, max_tokens)


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark tokens/sec for Ollama or llama.cpp (including router mode)."
    )
    parser.add_argument(
        "model", nargs="?", default=None,
        help=(
            "Model name. "
            "Required for --backend ollama (selects the model to run). "
            "For --backend llamacpp: required in router mode (must match the "
            "[section] name in models.ini); optional for a single-model server "
            "(used as a display label and included in the request, but the "
            "server ignores it)."
        ),
    )
    parser.add_argument(
        "--backend", choices=["ollama", "llamacpp"], default="ollama",
        help="Which server to benchmark against (default: ollama)",
    )
    parser.add_argument(
        "-n", "--runs", type=int, default=5,
        help="Number of measured runs (default: 5)",
    )
    parser.add_argument(
        "-p", "--prompt", default=DEFAULT_PROMPT,
        help="Prompt to use for the benchmark",
    )
    parser.add_argument(
        "--host", default=None,
        help=(
            "Server host URL "
            "(default: http://localhost:11434 for ollama, "
            "http://localhost:8080 for llamacpp)"
        ),
    )
    parser.add_argument(
        "--max-tokens", type=int, default=400,
        dest="max_tokens",
        help=(
            "Max tokens to generate per request for both backends "
            "(default: 400). Keeping this consistent is what makes "
            "generation rates comparable across Ollama and llama.cpp."
        ),
    )
    args = parser.parse_args()

    if args.backend == "ollama" and not args.model:
        print(
            "Error: a model name is required for --backend ollama "
            "(e.g. qwen2.5:3b)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.runs < 1:
        print("Error: --runs must be at least 1", file=sys.stderr)
        sys.exit(1)

    host = args.host or DEFAULT_HOSTS[args.backend]
    label = args.model or "(single-model llama-server)"

    print(f"Backend:    {args.backend}")
    print(f"Model:      {label}")
    print(f"Host:       {host}")
    print(f"Max tokens: {args.max_tokens}")
    print(f"Runs:       {args.runs} (plus 1 warm-up)")
    if args.backend == "llamacpp" and args.model:
        print(f"Mode:       router (routing to [{args.model}])")
    elif args.backend == "llamacpp":
        print(f"Mode:       single-model")
    print()

    # Warm-up: loads model into RAM/VRAM and primes the router's child
    # process so measured runs reflect steady-state generation speed.
    print("Warm-up run (not counted)...", flush=True)
    try:
        warm = run_once(args.backend, host, args.model, args.prompt, args.max_tokens)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"\nHTTP error {e.code} from server: {body}", file=sys.stderr)
        if args.backend == "ollama":
            print("Is the model name correct and pulled?", file=sys.stderr)
        elif args.model:
            print(
                f"Is llama-server running in router mode and does [{args.model}] "
                "exist in your models.ini?",
                file=sys.stderr,
            )
        else:
            print("Is llama-server running with a model loaded?", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\nCould not reach server at {host}: {e.reason}", file=sys.stderr)
        print("Is the server running and listening on that address?", file=sys.stderr)
        sys.exit(1)

    if warm["load_s"] is not None:
        print(f"  model load time: {warm['load_s']:.2f}s")
    if warm.get("served_model") and warm["served_model"] != label:
        print(f"  server confirmed model: {warm['served_model']}")
    print()

    gen_rates = []
    prompt_rates = []

    for i in range(1, args.runs + 1):
        r = run_once(args.backend, host, args.model, args.prompt, args.max_tokens)
        gen_rates.append(r["gen_rate"])
        prompt_rates.append(r["prompt_rate"])
        print(
            f"Run {i}: {r['gen_rate']:6.1f} tok/s generation "
            f"({r['gen_tokens']} tokens)  |  "
            f"{r['prompt_rate']:6.1f} tok/s prompt"
        )

    line = "=" * 56
    print(f"\n{line}")
    print(f"Results for {label} ({args.backend}) over {args.runs} runs")
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