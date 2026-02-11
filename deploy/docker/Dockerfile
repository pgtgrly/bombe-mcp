# Stage 1: Build the Rust extension
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

RUN pip install --no-cache-dir maturin

WORKDIR /build
COPY crates /build/crates
COPY pyproject.toml README.md /build/
COPY src /build/src

RUN maturin build --release --manifest-path crates/bombe-core/Cargo.toml --interpreter python3.11

# Stage 2: Runtime image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

# Install the Rust wheel from the builder stage, then the Python package
COPY --from=builder /build/target/wheels/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "bombe.server"]
CMD ["--repo", "/workspace", "serve"]
