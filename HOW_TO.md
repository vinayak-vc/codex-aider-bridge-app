# How To Use the Codex Aider Bridge App
### A plain-English guide — no Python knowledge required

---

## What Does This Tool Do?

Imagine you have two AI helpers:

- **The Supervisor** (Codex, Claude, or similar) — a smart planner that reads your project and decides *what* needs to be built, step by step. It also checks the work after each step.
- **The Developer** (Aider + a local AI model) — a coding assistant that runs on your own computer and actually writes the code, file by file.

This app is the **bridge** between them. You tell it your goal in plain English, and it:

1. Asks the Supervisor to make a task-by-task plan
2. Sends each task to Aider to write the code
3. Shows the Supervisor what was changed
4. Gets a thumbs up (PASS) or correction (REWORK) before moving on

You do not need to write any Python. You only need to type one command.

---

## Before You Start — What You Need to Install

You need four things on your computer. Each has a download link and simple installer.

---

### 1. Python 3.10 or newer

Python is the language this app is written in. You need it to run the bridge.

**Windows:**
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click the big yellow **Download Python** button
3. Run the installer
4. **Important:** On the first screen, tick the box that says **"Add Python to PATH"** before clicking Install

**Check it worked** — open a new Command Prompt and type:
```
python --version
```
You should see something like `Python 3.12.0`

---

### 2. Aider

Aider is the AI coding assistant that writes the actual code.

Open **Command Prompt** (press `Windows key`, type `cmd`, press Enter) and paste:
```
pip install aider-chat
```

Wait for it to finish, then check it worked:
```
aider --version
```

---

### 3. A Local AI Model via Ollama

Ollama lets you run AI models on your own computer — no internet required for the coding work, and no per-token cost.

1. Go to [ollama.com](https://ollama.com) and click **Download**
2. Install it like a normal Windows program
3. Open Command Prompt and download a coding model:

```
ollama pull mistral
```

This downloads the Mistral model (~4 GB). Good alternatives if you want better code quality:
```
ollama pull codellama
ollama pull deepseek-coder
```

> **Which model should I pick?**
> - `mistral` — fast, good for most things
> - `codellama` — designed for coding, slower but more accurate
> - `deepseek-coder` — excellent for code, recommended if your PC can handle it

**Check Ollama is running** — after installation, Ollama runs quietly in the background. You can see its icon in the system tray (bottom-right of your screen).

---

### 4. A Supervisor Agent — Codex or Claude CLI

The Supervisor is the AI that plans your project and reviews the code. You need one of these:

#### Option A — Codex CLI (OpenAI)
Follow the setup at [github.com/openai/codex](https://github.com/openai/codex). You will need an OpenAI account.

#### Option B — Claude CLI (Anthropic)
Follow the setup at [claude.ai/download](https://claude.ai/download). You will need an Anthropic account.

You only need one of these. Either works fine.

---

### 5. Download This App

1. Go to the project page on GitHub
2. Click the green **Code** button → **Download ZIP**
3. Extract the ZIP to a folder, for example: `C:\tools\codex-aider-bridge-app`

---

## Your First Run

### Step 1 — Open Command Prompt in the app folder

1. Open File Explorer and navigate to where you extracted the app
2. Click the address bar at the top
3. Type `cmd` and press Enter

A Command Prompt window will open already inside the right folder.

---

### Step 2 — Run the bridge

Type this command, replacing the parts in `< >` with your own values:

```
python main.py "Your goal here" --repo-root "C:\path\to\your\project" --aider-model ollama/mistral
```

**Example — building a feature in an existing project:**
```
python main.py "Add a user login page" --repo-root "C:\MyProject\website" --aider-model ollama/mistral
```

**Example — working on a game project with a brief:**
```
python main.py "Build the first playable level" --repo-root "C:\MyGame\GameProject" --idea-file "C:\MyGame\GAME_IDEA.md" --aider-model ollama/deepseek-coder
```

**Example — just see the plan without changing any files (safe preview):**
```
python main.py "Refactor the settings page" --repo-root "C:\MyProject" --aider-model ollama/mistral --dry-run
```

Press Enter and watch it go.

---

## What You Will See

The app prints status messages as it works. Here is what they mean:

```
Bridge starting — repo: C:\MyProject
```
The app started and found your project folder.

```
Requesting plan — attempt 1 of 3
Supervisor produced 5 task(s)
```
The Supervisor read your project and made a 5-step plan.

```
Task 1 — attempt 1/3 — files: src/login.py
```
Aider is now working on task 1, editing the file `src/login.py`.

```
Task 1: supervisor approved
```
The Supervisor looked at what Aider changed and gave it a thumbs up.

```
Task 1 — supervisor requested rework: Add input validation for the email field
```
The Supervisor was not happy and gave Aider a more specific instruction to try again.

```
{"status": "success", "tasks": 5}
```
All 5 tasks are done. Your project has been updated.

---

## Common Options Explained Simply

You put these after `python main.py "your goal"`:

| What you type | What it does |
|---|---|
| `--repo-root "C:\MyProject"` | Tells the app where your project is |
| `--aider-model ollama/mistral` | Which local AI model Aider should use |
| `--idea-file "C:\path\to\brief.md"` | A text file describing your project in detail |
| `--dry-run` | Shows the plan but does **not** change any files |
| `--plan-file "myplan.json"` | Use a saved plan instead of asking the Supervisor |
| `--plan-output-file "plan.json"` | Save the generated plan to a file |
| `--max-task-retries 3` | How many times to retry a task that fails (default: 2) |
| `--log-level DEBUG` | Show much more detail — useful if something goes wrong |

---

## Using a Project Brief (Recommended for Game / App Projects)

If you have a detailed document describing what you want to build, the Supervisor will use it to make a much better plan.

Create a plain text file called `GAME_IDEA.md` or `PROJECT_BRIEF.md` anywhere on your computer. Write in plain English:

```
My project is a mobile puzzle game called Stack Pulse.
The player taps the screen to drop a block onto a stack.
If the block is aligned perfectly, the stack grows. If not, the edges are trimmed.
The game ends when the block misses completely.
Features needed: scoring, combo system, increasing difficulty, a restart button.
```

Then run:
```
python main.py "Build the core gameplay loop" --repo-root "C:\MyGame" --idea-file "C:\MyGame\PROJECT_BRIEF.md" --aider-model ollama/mistral
```

---

## Using a Different Supervisor

By default the app uses Codex. To switch to Claude CLI:

```
python main.py "Add error handling" --repo-root "C:\MyProject" --supervisor-command "claude --print" --aider-model ollama/mistral
```

---

## Saving and Reusing a Plan

To save the plan the Supervisor makes (useful for reviewing or rerunning):
```
python main.py "Add a search feature" --repo-root "C:\MyProject" --aider-model ollama/mistral --plan-output-file "search-plan.json"
```

To run again from the saved plan (skips the Supervisor planning step):
```
python main.py --plan-file "search-plan.json" --repo-root "C:\MyProject" --aider-model ollama/mistral
```

---

## Logs

Every run saves a detailed log to `logs\bridge-app.log` inside your project folder. If something goes wrong, open that file in Notepad to see exactly what happened.

---

## Troubleshooting

### "python is not recognized"
You forgot to tick **"Add Python to PATH"** during installation. Re-run the Python installer, choose **Modify**, and tick that option.

### "aider is not recognized"
Run `pip install aider-chat` again in Command Prompt.

### "ollama is not recognized"
Make sure Ollama is installed and its icon is visible in the system tray. Try restarting your computer.

### The plan keeps failing / "Supervisor failed to produce a valid plan"
This means the Supervisor agent could not produce a usable plan after 3 attempts. Options:
- Add an `--idea-file` brief to give the Supervisor more context
- Write a plan manually and use `--plan-file` (see the `example plan.json` file in the app folder for the format)
- Try `--max-plan-attempts 5` to give it more attempts

### Aider is changing the wrong files
Use `--dry-run` first to inspect the plan and make sure the Supervisor picked the right files before running for real.

### Nothing happens / it hangs
The Supervisor or Aider is still thinking. Large projects and slow models can take a few minutes per task. Watch the log file for progress or add `--log-level DEBUG` to see more detail in real time.

### I want to stop it mid-run
Press `Ctrl + C` in the Command Prompt window. Any files already changed by Aider will remain. Re-run from a `--plan-file` to continue from where you left off.

---

## Environment Variables (Advanced — Skip If Unsure)

If you always use the same settings, you can save them as Windows environment variables so you do not have to type them every time.

1. Press `Windows key`, search for **"Edit the system environment variables"**, open it
2. Click **Environment Variables**
3. Under **User variables**, click **New** and add:

| Variable name | Example value |
|---|---|
| `BRIDGE_AIDER_MODEL` | `ollama/mistral` |
| `BRIDGE_SUPERVISOR_COMMAND` | `codex.cmd exec --skip-git-repo-check --color never` |
| `BRIDGE_DEFAULT_VALIDATION` | `python -m pytest` |

After saving, restart Command Prompt. Now you can run just:
```
python main.py "Add a search feature" --repo-root "C:\MyProject"
```

---

## Quick Reference Card

```
python main.py "<your goal>" [options]

Essential options:
  --repo-root "C:\path\to\project"     Where your project lives
  --aider-model ollama/mistral          Which local AI model to use

Useful options:
  --dry-run                             Preview the plan, no file changes
  --idea-file "C:\path\to\brief.md"    Project description for the Supervisor
  --plan-output-file "plan.json"        Save the plan to a file
  --plan-file "plan.json"              Run from a saved plan
  --max-task-retries 3                  More retries per task
  --supervisor-command "claude --print" Use Claude instead of Codex
  --log-level DEBUG                     Show more detail
```
