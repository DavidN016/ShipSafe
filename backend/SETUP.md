# ShipSafe Backend – Setup

## 1. API keys and environment

- Copy `.env.example` to `.env` in the **project root** (same folder as this `backend/` directory’s parent):
  ```bash
  cp .env.example .env
  ```
- Edit `.env` and set at least one of:
  - **OPENAI_API_KEY** – for Detector and Remediator (and Auditor if you don’t use CodeBERT).
  - **ANTHROPIC_API_KEY** – optional; used when the model name is Claude.
- Do **not** commit `.env` (it should be in `.gitignore`).

If your app doesn’t load `.env` automatically, load it before starting the server, e.g. in `main.py` or your run script:

```python
from dotenv import load_dotenv
load_dotenv()
```

## 2. Install dependencies

From the project root:

```bash
pip install -r backend/requirements.txt
```

This installs:

- FastAPI, LangChain, LangGraph, ChromaDB, etc.
- **OpenAI** (and optionally **Anthropic** if you `pip install langchain-anthropic`).
- **Hugging Face stack** for the Auditor when using CodeBERT/CodeT5: `langchain-huggingface`, `transformers`, `torch`, `accelerate`.

First run may download models (e.g. sentence-transformers for Chroma, or CodeT5 for the Auditor), so ensure you have enough disk space and a stable connection.

## 3. Use CodeBERT-style model as the Auditor

The **Auditor** agent (and patch verification step) can use a local, code-focused model instead of the main API LLM:

1. In `.env`, set:

   ```bash
   SHIPSAFE_AUDITOR_MODEL=codebert
   ```

   This uses **microsoft/CodeT5-small** under the hood. (CodeBERT itself is encoder-only and cannot generate the JSON response; CodeT5 is in the same family and can.)

2. Optional: use another Hugging Face model:

   ```bash
   SHIPSAFE_AUDITOR_MODEL=microsoft/CodeT5-small
   ```

   Or any other `text-generation` / `text2text-generation` model ID from Hugging Face.

3. Ensure the extra dependencies are installed (they are in `requirements.txt`):

   ```bash
   pip install langchain-huggingface transformers torch accelerate
   ```

4. First invocation will download the model (e.g. ~250MB for CodeT5-small). GPU is recommended for speed; CPU works but is slower.

If `SHIPSAFE_AUDITOR_MODEL` is not set, the Auditor uses the same LLM as the Detector/Remediator (OpenAI or Anthropic).

## 4. Quick check

- Start the API (from project root):
  ```bash
  uvicorn backend.main:app --reload
  ```
- Call `GET /` – you should get `{"status":"ok"}`.
- Ensure `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) is set when triggering the agent workflow; otherwise the Detector/Remediator will raise an error.

## 5. GitHub webhook (`POST /webhook/github`)

1. Expose your API (e.g. ngrok) and add a repository webhook:
   - **Payload URL:** `https://<host>/webhook/github`
   - **Content type:** `application/json`
   - **Secret:** optional; if set, put the same value in `GITHUB_WEBHOOK_SECRET` in `.env`.
   - **Events:** at least **Pull requests** (and optionally **Pushes**).
2. Set `GITHUB_TOKEN` in `.env` to a PAT with **`repo`** scope (needed to download the PR/compare diff from the GitHub API).
3. On `pull_request` (`opened`, `synchronize`, `reopened`, `ready_for_review`), ShipSafe fetches the unified diff, splits it by file, and runs the LangGraph pipeline per file (up to `SHIPSAFE_WEBHOOK_MAX_FILES`, default 25).
