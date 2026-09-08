<div align="center">

# OmegaUse-SOP

**SOP Engineering for Professional Computer Use from Human Demonstrations**

Baidu, Inc. &nbsp;×&nbsp; Ningxia Electric Power Engineering Co., Ltd.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Platform](https://img.shields.io/badge/Platform-Windows_10%2F11-0078D6?logo=windows&logoColor=white)](#requirements)

[📺 Demo Video](https://www.youtube.com/watch?v=FQO_eyL_seE) &nbsp;·&nbsp;
[📄 Citation](#citation) &nbsp;·&nbsp;
[🚀 Getting Started](#getting-started)

<br>

<img src="assets/sop-overview.png" alt="Comparison between GUI agents without SOP (top) and with SOP (bottom)" width="880">

<p>
<em>
General computer use succeeds on general tasks but fails on professional workflows <b>(top)</b>.<br>
With SOP Engineering — Observe → Reason → Configure → Execute plus iterative refinement —<br>
demonstrations become stable, reusable SOP skills that make professional workflows succeed <b>(bottom)</b>.
</em>
</p>

</div>

## Overview

OmegaUse-SOP is a **human-in-the-loop SOP Engineering system** that transforms human demonstrations of professional computer use into reusable **SOP skills** for GUI agents.

General-purpose GUI agents perform well on open-ended computer-use benchmarks, but professional workflows are different: they depend on **domain-specific standard operating procedures (SOPs)**, software-specific conventions, implicit expert knowledge, configurable task parameters, and task-level verification. A professional workflow is not merely a long sequence of clicks — it is a trained practice.

Analogous to *prompt engineering*, **SOP Engineering** iteratively refines demonstrations, execution rules, domain knowledge, and task-specific parameters until a GUI agent can reliably reproduce a professional procedure — record once, run many times.

## How It Works

```
┌──────────┐     ┌──────────┐     ┌───────────┐     ┌──────────┐
│ Observe  │ ──▶ │  Reason  │ ──▶ │ Configure │ ──▶ │ Execute  │
└──────────┘     └──────────┘     └───────────┘     └──────────┘
 Record expert    VLM abstracts    User adds         Step-wise
 operations as    low-level events domain rules &    grounding, action
 multimodal GUI   into semantic    task-specific     generation, and
 traces           instructions     parameters        verification
```

1. **Observe** — Records an expert demonstration as a multimodal GUI trace: pre-action screenshots, mouse/keyboard events, timestamps, and visual interaction targets (UI elements detected via OmniParser + PaddleOCR and cropped at the clicked position). Output: `recording.json`.
2. **Reason** — A vision-language model grounds each recorded action in its visual context and generates a step-level natural-language instruction describing the operated element, its relative position, and the intended interaction — so the agent can *locate the same target* on a future screen instead of blindly replaying a pixel coordinate. Output: `prompt.json`.
3. **Configure** — The user makes implicit expert knowledge explicit through two editable files: `domain.md` (domain SOP guidance: professional rules and software-specific execution constraints) and `params.md` (task-specific parameters: which recorded values are variables that should be replaced at run time).
4. **Execute** — Applies the configured SOP skill in a live GUI environment. To avoid context overload on long SOPs, step-related information (raw trace + semantic instruction + domain rules + parameters) is **progressively disclosed** one step at a time. Each step goes through screen grounding → action generation → low-level execution → **result verification** against the demonstration's expected post-action screen, with a human-in-the-loop option to continue, retry, or stop when a deviation is detected.

## Results

Case study on five professional photovoltaic-simulation SOP tasks in **PVsyst 7.2**, derived from real-world power-sector client workflows:

| Model | w/o SOP | w/ OmegaUse-SOP |
|---|:---:|:---:|
| Qwen3-VL-235B-A22B-Instruct | 1/5 | **5/5** |
| GPT-5.5 | 3/5 | **5/5** |
| Opus-4.7 | 2/5 | **5/5** |

Ablation: removing the **Reason** module drops Qwen3-VL from 5/5 to 2/5 — semantic abstraction of demonstrations is essential for reusable SOPs.

## Getting Started

### Requirements

| | |
|---|---|
| **OS** | Windows 10/11 — the agent drives desktop applications through Windows UI automation |
| **Python** | 3.12 or newer (CPython; the pinned version is in `.python-version`) |
| **Services** | A reachable **OmniParser** endpoint and a **PaddleOCR** endpoint for UI-element detection during Observe |
| **Model** | A VLM API key for an OpenAI-compatible endpoint (default: Qwen3-VL via [Baidu Qianfan](https://qianfan.baidubce.com)) |

### Installation

```bash
git clone https://github.com/baidu-frontier-research/omegause-sop.git
cd omegause-sop

python -m venv .venv
.venv\Scripts\activate        # Windows

pip install -e .
pip install openai qwen-agent tqdm
```

### Configuration

Copy `.env.example` to `.env` and fill in your endpoints:

```ini
# UI-element detection during Observe
OMNIPARSER_URL=http://your-omniparser-server:8101/
PADDLEX_URL=http://your-paddleocr-server:8101/ocr

# VLM API key (Baidu Qianfan ModelBuilder, OpenAI-compatible)
QIANFAN_API_KEY=your-api-key
```

Optional overrides:

| Variable | Default | Used by |
|---|---|---|
| `REASON_MODEL_NAME` / `ENRICHER_MODEL_NAME` | `qwen3-vl-235b-a22b-instruct` | Reason |
| `REASON_API_URL` / `ENRICHER_API_URL` | `https://qianfan.baidubce.com/v2` | Reason |
| `MODEL_NAME` / `API_URL` | same as above | Execute |

### Usage

> [!WARNING]
> Recording captures your **whole screen and every keystroke**, and Execute drives your
> **real mouse and keyboard**. Read [Safety and Privacy](#safety-and-privacy) first.

```bash
python cli_en.py
```

An interactive wizard walks you through the four stages (run them one by one, or pick **Full Pipeline**):

1. **AI Observe** — name your application and session, then perform the workflow normally. Press `Ctrl+Alt+R` to stop recording.
2. **AI Reason** — the VLM converts the recording into step-level semantic instructions.
3. **Customize** — edit `domain.md` and `params.md` (templates for PVsyst are provided in `domain_knowledge_template/`).
4. **AI Execute** — the agent reproduces the workflow on the live screen. Keep your hands off the mouse; you'll be prompted to continue / retry / stop if verification detects a deviation.

Everything for one demonstration lives in a single session directory:

```
sop/{app_name}/{session_name}/
├── screenshots/       # [observe]   pre-action full-screen captures
├── clicked_boxes/     # [observe]   cropped visual interaction targets
├── recording.json     # [observe]   low-level multimodal GUI trace
├── prompt.json        # [reason]    step-level semantic instructions
├── domain.md          # [configure] domain SOP guidance (user-edited)
├── params.md          # [configure] task-specific parameters (user-edited)
├── replay_logs/       # [execute]   execution logs per run
└── replay_temp/       # [execute]   temporary screenshots during execution
```

**SOP Engineering is iterative**: if an execution run fails, refine `domain.md` with the rule the agent missed (or adjust `params.md`) and execute again — no re-recording needed.

## Safety and Privacy

Read this before recording a demonstration.

- **Recording captures everything, not just the target application.** Observe installs a *global* keyboard hook (`agents/observe/observer.py`) and takes full-screen screenshots. Anything you type while recording is active — in any window, including passwords, tokens and chat messages — is written in plain text into `recording.json`, and whatever is on screen is saved under `screenshots/`. Recordings are not encrypted or redacted. Stop the recording (`Ctrl+Alt+R`) before switching to unrelated windows, and review a session directory before sharing it.
- **Execute drives your real mouse and keyboard.** It acts on the live desktop, not a sandbox. Run it on a machine and account where mis-clicks are acceptable, and keep your hands off the input devices while a run is in progress.
- **PyAutoGUI's fail-safe is disabled** in `agents/execute/executor.py`, `agents/execute/action_executor.py` and `agents/observe/observer.py` (`pyautogui.FAILSAFE = False`), so moving the pointer to a screen corner will *not* abort a run. Set it back to `True` if you want that emergency stop.
- **Prompts and model responses are printed to the console**, including the contents of `params.md`. Avoid putting secrets in the configuration files, and be careful when sharing terminal output or recordings of a run.

## Repository Layout

```
omegause-sop/
├── cli_en.py                    # CLI entry point (English)
├── cli.py                       # CLI entry point (Chinese)
├── agents/
│   ├── observe/                 # Observe module: event recording + screenshot + UI-element parsing
│   ├── reason/                  # Reason module: VLM semantic abstraction of the trace
│   └── execute/                 # Execute module: grounding, action generation, verification
├── ui/, ui_en.py                # interactive CLI interface (rich + questionary)
├── prompts/                     # system prompts and page-readiness prompts
├── utils/                       # Computer-Use function-call tools, notifications
└── domain_knowledge_template/   # example domain.md / params.md for PVsyst
```

The full source of every module is included in this repository.

## License

Released under the [Apache License 2.0](LICENSE). Portions of
`utils/agent_function_call.py` and `agents/execute/executor.py` are adapted from
the Qwen-VL cookbooks, also Apache-2.0; see [NOTICE](NOTICE) for attribution.

## Citation

```bibtex
@article{xiao2026omegausesop,
  title   = {OmegaUse-SOP: SOP Engineering for Professional Computer Use from Human Demonstrations},
  author  = {Xiao, Yixiong and An, Lang and Yang, Hucheng and Ma, Pinxue and Chen, Yongquan and
             Cao, Jingjia and Zhao, Yusai and Wang, Ting and Liu, Ting and Bao, Siqi and
             Zhou, Jingbo and Wu, Hua},
  year    = {2026}
}
```

## Related Work

- [OmegaUse](https://arxiv.org/abs/2601.20380) — Building a General-Purpose GUI Agent for Autonomous Task Execution
- [OmniParser](https://arxiv.org/abs/2408.00203) — Pure-vision-based screen parsing used by the Observe module




