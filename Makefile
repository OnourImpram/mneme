.PHONY: install-dev test test-parity lint bench-all bench-rc-gate bench-retrieval \
        bench-latency bench-cost bench-migration bench-head-to-head \
        bench-longmemeval bench-compaction-recall clean help

PY ?= python
PYTEST ?= $(PY) -m pytest
PNPM ?= pnpm
COVERAGE_ROOT ?= $(CURDIR)

BENCH_OUT ?= benchmarks/_runs
BENCH_SEED ?= 42

help:
	@echo "mneme development targets:"
	@echo "  make install-dev          install editable Python + pnpm workspace"
	@echo "  make test                 run pytest + vitest across all packages"
	@echo "  make lint                 ruff + mypy + biome"
	@echo "  make bench-all            run all seven benchmarks (A through G)"
	@echo "  make bench-rc-gate        run the deterministic 3.6 release-candidate gate"
	@echo "  make bench-retrieval      benchmark A: synthetic retrieval quality"
	@echo "  make bench-latency        benchmark B: Stop hook and retrieval p95"
	@echo "  make bench-cost           benchmark C: Adaptive Context Layer token savings"
	@echo "  make bench-migration      benchmark D: claude-mem migration validation"
	@echo "  make bench-head-to-head   benchmark E: head-to-head adapter comparison"
	@echo "  make bench-longmemeval    benchmark F: LongMemEval FTS5 recall on synthetic fixture"
	@echo "  make bench-compaction-recall  benchmark G: CCE compaction-recall self-heal"
	@echo "  make clean                remove caches and build artifacts"

install-dev:
	cd packages/mneme-core && $(PY) -m pip install -e ".[dev]"
	$(PY) -m pip install -e "packages/mneme-cc-plugin[dev]"
	$(PY) -m pip install -e "packages/mneme-graph[dev]"
	$(PY) -m pip install -e "packages/mneme-code[dev]"
	$(PNPM) install --frozen-lockfile

test:
	COVERAGE_FILE="$(COVERAGE_ROOT)/.coverage.mneme-core" $(PYTEST) packages/mneme-core/tests -q --cov-config=packages/mneme-core/pyproject.toml
	COVERAGE_FILE="$(COVERAGE_ROOT)/.coverage.mneme-cc-plugin" $(PYTEST) packages/mneme-cc-plugin/tests -q --cov-config=packages/mneme-cc-plugin/pyproject.toml
	COVERAGE_FILE="$(COVERAGE_ROOT)/.coverage.mneme-graph" $(PYTEST) packages/mneme-graph/tests -q --cov-config=packages/mneme-graph/pyproject.toml
	COVERAGE_FILE="$(COVERAGE_ROOT)/.coverage.mneme-code" $(PYTEST) packages/mneme-code/tests -q --cov-config=packages/mneme-code/pyproject.toml
	$(PYTEST) tests/parity -q
	$(PNPM) --filter mneme-mcp-server test:coverage

test-parity:
	$(PYTEST) tests/parity -q

lint:
	cd packages/mneme-core && ruff check . && mypy --strict src/
	cd packages/mneme-cc-plugin && ruff check . && mypy --strict src/
	cd packages/mneme-graph && ruff check . && mypy --strict mneme_graph/
	cd packages/mneme-code && ruff check . && mypy --strict mneme_code/
	$(PNPM) --filter mneme-mcp-server lint

$(BENCH_OUT):
	@mkdir -p $(BENCH_OUT)

bench-retrieval: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/retrieval/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --hardware-output $(BENCH_OUT)/retrieval-hardware.json \
	  --output $(BENCH_OUT)/retrieval.json
	$(PY) benchmarks/retrieval/regression_guard.py $(BENCH_OUT)/retrieval.json

bench-latency: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/latency/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --hardware-output $(BENCH_OUT)/latency-hardware.json \
	  --output $(BENCH_OUT)/latency.json
	$(PY) benchmarks/latency/p95_guard.py $(BENCH_OUT)/latency.json --threshold-ms=1000

bench-cost: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/cost/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --hardware-output $(BENCH_OUT)/cost-hardware.json \
	  --output $(BENCH_OUT)/cost.json

bench-migration: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/migration/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --hardware-output $(BENCH_OUT)/migration-hardware.json \
	  --output $(BENCH_OUT)/migration.json

bench-head-to-head: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/head-to-head/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --hardware-output $(BENCH_OUT)/head-to-head-hardware.json \
	  --output $(BENCH_OUT)/head-to-head.json

bench-longmemeval: $(BENCH_OUT)
	$(PY) benchmarks/longmemeval/run.py --output $(BENCH_OUT)/longmemeval.json
	$(PY) benchmarks/longmemeval/regression_guard.py $(BENCH_OUT)/longmemeval.json

bench-compaction-recall: $(BENCH_OUT)
	MNEME_BENCH_SEED=$(BENCH_SEED) $(PY) benchmarks/compaction-recall/run.py \
	  --output-format=json --seed=$(BENCH_SEED) \
	  --output $(BENCH_OUT)/compaction-recall.json
	$(PY) benchmarks/compaction-recall/regression_guard.py $(BENCH_OUT)/compaction-recall.json

bench-all: bench-retrieval bench-latency bench-cost bench-migration bench-head-to-head bench-longmemeval bench-compaction-recall
	@echo "All benchmarks complete. Results in $(BENCH_OUT)/."

bench-rc-gate: $(BENCH_OUT)
	$(PY) -m mneme_core.bench.gate all --output $(BENCH_OUT)/mneme-3.6-rc-gate.json

clean:
	$(PY) -c "import pathlib; [p.unlink() for p in pathlib.Path('.').rglob('.coverage*') if p.is_file()]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('.pytest_cache')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('.mypy_cache')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('.ruff_cache')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('node_modules')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('dist')]"
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p,ignore_errors=True) for p in pathlib.Path('.').rglob('build')]"
	$(PY) -c "import shutil,pathlib; shutil.rmtree(pathlib.Path('packages/mneme-mcp/coverage'),ignore_errors=True)"
