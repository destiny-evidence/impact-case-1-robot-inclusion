FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-default-groups

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-editable

ENV SEARCH_QUERY=/app/.configs/search-query.txt \
    MODEL_PREFILTER=/app/.configs/models/high-recall-svm.sklearn \
    PROMPT_HIGH_RECALL=/app/.configs/prompts/high_recall.txt \
    PROMPT_BALANCED=/app/.configs/prompts/balanced.txt \
    PROMPT_HIGH_PRECISION=/app/.configs/prompts/high_precision.txt

CMD ["robot"]
