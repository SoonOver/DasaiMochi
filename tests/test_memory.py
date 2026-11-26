"""Tests for persistent companion memory and `[remember:...]` extraction."""

from types import SimpleNamespace

from dasai_mochi.llm import OpenCodeClient
from dasai_mochi.memory import MemoryStore, extract_memory_tags


def test_extract_memory_tags_and_store_persistence(tmp_path):
    facts, clean = extract_memory_tags("[mood:happy] Halo Budi! [remember: Nama pengguna adalah Budi]")
    assert facts == ["Nama pengguna adalah Budi"]
    assert clean == "[mood:happy] Halo Budi!"

    store = MemoryStore(path=tmp_path / "memory.json")
    assert store.add_facts(["Nama Budi", "nama budi", "Suka kopi"]) is True
    assert store.facts == ["Nama Budi", "Suka kopi"]

    reloaded = MemoryStore(path=tmp_path / "memory.json")
    reloaded.load()
    assert reloaded.facts == ["Nama Budi", "Suka kopi"]


def test_opencode_client_persists_facts_and_forgets(monkeypatch, tmp_path):
    mem_path = tmp_path / "mem.json"
    store = MemoryStore(path=mem_path)

    monkeypatch.setattr(
        "dasai_mochi.llm.requests.post",
        lambda *_a, **_kw: SimpleNamespace(
            status_code=200,
            encoding="utf-8",
            text='{"choices":[{"message":{"content":"[happy] Oke Budi! [remember: Nama user Budi]"}}]}',
            json=lambda: {"choices": [{"message": {"content": "[happy] Oke Budi! [remember: Nama user Budi]"}}]},
        ),
    )

    c1 = OpenCodeClient(backend="kilo", persist_memory=True, memory=store)
    r1 = c1.ask("Namaku Budi")
    assert r1.text == "Oke Budi!"

    c2 = OpenCodeClient(backend="kilo", persist_memory=True, memory=MemoryStore(path=mem_path))
    assert "Nama user Budi" in c2.memory.facts
    assert len(c2.history) == 2

    monkeypatch.setattr(
        "dasai_mochi.llm.requests.post",
        lambda *_a, **_kw: SimpleNamespace(
            status_code=200,
            encoding="utf-8",
            text='{"choices":[{"message":{"content":"[idle] Sudah kulupakan! [action:forget_memory]"}}]}',
            json=lambda: {"choices": [{"message": {"content": "[idle] Sudah kulupakan! [action:forget_memory]"}}]},
        ),
    )
    c2.ask("Lupakan semua")
    assert c2.memory.facts == []
    assert c2.history == []
