# Contributing to amiagi

Thanks for contributing.

## Development setup

Choose one environment strategy:

### Option A: local virtualenv (`.venv`)

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Option B: Conda (any environment name)

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create -n <your_env_name> python=3.10 -y
conda activate <your_env_name>
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

## Run checks

```bash
pytest -q
```

## Pull request guidelines

- Keep changes focused and minimal.
- Update tests when behavior changes.
- Update `README.md` and `README.pl.md` when setup/runtime behavior changes.
- Avoid committing generated runtime artifacts (`logs/*.jsonl`, `data/*.db`, temporary files in `amiagi-my-work/*`).
