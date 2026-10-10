#!/usr/bin/env bash
# Run Tradie Capture on this computer.
#   Website:      http://localhost:8000/
#   Capture app:  http://localhost:8000/app/
#
# Usage, from the capture-app folder:   bash scripts/run_local.sh
# Stop it with Ctrl+C.
#
# - First run: makes .venv and installs the packages (a minute or two).
# - No .env yet: copies .env.example to .env.
# - No OPENAI_API_KEY in .env: runs without AI (captures save; no transcript or AI draft).
#   Paste your key after OPENAI_API_KEY= in .env and run this again for the full app.
#   Never paste the key in a chat, a screenshot or GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. A Python that is 3.10 or newer (macOS's built-in python3 can be 3.9).
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

# 2. Virtual environment and packages (reinstalled only when requirements.txt changes).
if [ ! -x .venv/bin/python ]; then
  echo "Setting up .venv with $("$PY" --version) ..."
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "The existing .venv uses an old Python. Rename or delete the .venv folder, then run this again."
  exit 1
fi
want="$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("requirements.txt", "rb").read()).hexdigest())')"
have="$(cat .venv/.requirements.sha256 2>/dev/null || true)"
if [ "$want" != "$have" ]; then
  echo "Installing packages ..."
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$want" > .venv/.requirements.sha256
fi

# 3. Settings. Values exported here win over .env (uvicorn never overrides the real environment).
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Made .env from .env.example."
fi
env_value() {  # prints the value of KEY in .env without quotes or spaces (the last line wins, as in uvicorn)
  grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"' \r' || true
}
if [ -z "$(env_value OPENAI_API_KEY)" ]; then
  echo
  echo "No OPENAI_API_KEY in .env: running WITHOUT AI. Captures save, but there is no transcript or AI draft."
  echo "For the full app: open .env (open -e .env), paste your key after OPENAI_API_KEY=, save, run this again."
  echo
  export STT_PROVIDER=mock EXTRACT_PROVIDER=rules
else
  # The models the deployed app uses; .env can name others.
  [ -n "$(env_value STT_MODEL)" ] || export STT_MODEL=gpt-4o-mini-transcribe
  [ -n "$(env_value EXTRACT_MODEL)" ] || export EXTRACT_MODEL=gpt-4o-mini
  echo "Using OpenAI for speech-to-text and drafts (key found in .env; not shown)."
fi

# 4. A free port, starting at 8000.
PORT="$(.venv/bin/python - <<'EOF'
import socket
for port in range(8000, 8020):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
EOF
)"
if [ -z "$PORT" ]; then echo "Ports 8000-8019 are all busy. Close the other server and try again."; exit 1; fi

echo "Website:      http://localhost:$PORT/"
echo "Capture app:  http://localhost:$PORT/app/   (allow the microphone, camera and location when asked)"
echo "Stop with Ctrl+C."
if command -v open >/dev/null 2>&1; then (sleep 2; open "http://localhost:$PORT/") & fi
exec .venv/bin/python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port "$PORT"
