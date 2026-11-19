# API keys (not committed)

The model profiles in `../llm/*.yaml` read their key from an environment
variable (`credential: {env: DEEPSEEK_API_KEY}` / `{env: GPT6ASTRA_API_KEY}`).
If the variable is not set in the shell, the loader (`utils/config.py`) looks in
`$CODEINDOOR_ENV_FILE`, then in the nearest `runtime/.env` or `.env` above the
profile, i.e. the repository's shared `runtime/.env`.

So normally you only fill in `runtime/.env` (copied from `.env.example`).

Key files in this folder (`deepseek_key.txt`, ...) are still supported: a
profile can use `credential: {file: ../keys/deepseek_key.txt}`, and the old
`generate_with_deepseek*.py` scripts read them as a fallback. Everything here
except this README is gitignored.
