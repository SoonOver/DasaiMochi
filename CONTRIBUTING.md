# Contributing to Dasai Mochi

Terima kasih sudah tertarik ikut mengembangkan **Dasai Mochi (Terminal Edition)**!

## Setup Lingkungan Pengembangan

```bash
git clone https://github.com/SoonOver/DasaiMochi.git
cd DasaiMochi
uv sync --all-extras
uv run ruff check .
uv run pytest -v
```

Atau dengan `pip`:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -v
```

## Panduan Pull Request

- Pastikan semua test (`pytest`) dan lint (`ruff check .`) lulus sebelum membuka PR.
- Tambahkan unit test untuk ekspresi baru, backend suara, atau aksi kendali PC baru.
- Jaga kompatibilitas lintas platform (Linux & Windows 10/11).
