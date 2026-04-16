"""Aider configuration constants — error patterns, prompt patterns, standards files.

Extracted from aider_runner.py to keep the runner focused on execution.
"""
from __future__ import annotations

# Standards files auto-injected as --read context when found in the repo root.
STANDARDS_FILENAMES: list[str] = [
    "CODE_FORMAT_STANDARDS.md",
    "CODING_STANDARDS.md",
    "STYLE_GUIDE.md",
    ".editorconfig",
    "CONTRIBUTING.md",
]

# Patterns indicating Aider is waiting for interactive input.
INTERACTIVE_PROMPT_PATTERNS: tuple[str, ...] = (
    "add file to the chat",
    "attempt to fix lint errors",
    "(y)es/(n)o",
    "[y/n]",
    "open docs url",
)

# Fatal error patterns detected in Aider stdout/stderr.
# When matched, override exit-code-0 to failure so the bridge doesn't
# waste retries on a config/connection problem that will never self-heal.
# Each tuple: (substring_to_match, error_category, human_readable_message)
FATAL_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    ("litellm.BadRequestError", "config_error", "LLM provider rejected the request — check model name and provider prefix"),
    ("LLM Provider NOT provided", "config_error", "Model name missing provider prefix (e.g. ollama/)"),
    ("does not exist", "model_error", "Model not found — run 'ollama pull <model>' first"),
    ("model not found", "model_error", "Model not installed in Ollama"),
    ("connection refused", "connection_error", "Ollama is not running — start it with 'ollama serve'"),
    ("connection error", "connection_error", "Cannot reach LLM provider — check network/Ollama"),
    ("ConnectError", "connection_error", "Cannot connect to LLM provider"),
    ("api_error", "api_error", "LLM API returned an error"),
    ("rate_limit", "rate_limit", "Rate limited by LLM provider — wait and retry"),
    ("invalid_api_key", "auth_error", "Invalid API key for LLM provider"),
    ("AuthenticationError", "auth_error", "Authentication failed with LLM provider"),
    ("Could not connect to ollama", "connection_error", "Ollama is not reachable"),
    ("exceeds the", "context_overflow", "Prompt too large for model's context window — try a smaller file or larger model"),
    ("context length exceeded", "context_overflow", "Prompt exceeds model context window"),
    ("maximum context length", "context_overflow", "Prompt exceeds model context window"),
]

# Patterns that indicate the model gave a useless response because context
# was too large — the model "acknowledges" instead of actually coding.
USELESS_RESPONSE_PATTERNS: tuple[str, ...] = (
    "i will keep that in mind",
    "i'll keep that in mind",
    "let me know if you need",
    "please let me know",
    "if you have any questions",
    "i understand the task",
    "i'll help you with that",
    "<<<<<<< SEARCH\n=======\n>>>>>>> REPLACE"
)
