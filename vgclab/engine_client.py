"""Cliente Python del motor Node (engine/engine.js).

Un `Engine` es un proceso Node; un `EnginePool` reparte la búsqueda entre varios procesos
(uno por núcleo) para que la matriz de pagos se rellene en paralelo.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ENGINE_JS = ROOT / "engine" / "engine.js"
DEFAULT_FORMAT = os.environ.get("VGCLAB_FORMAT", "gen9championsvgc2026regmc")


class EngineError(RuntimeError):
    pass


def _node_bin() -> str:
    node = os.environ.get("VGCLAB_NODE") or shutil.which("node")
    if not node:
        raise EngineError("No encuentro 'node'. Instala Node.js 20+ (ver README).")
    return node


class Engine:
    """Un proceso del motor. Seguro para usar desde varios hilos (una petición a la vez)."""

    def __init__(self, format: str = DEFAULT_FORMAT):
        self.format = format
        env = dict(os.environ)
        env.setdefault("VGCLAB_FORMAT", format)
        self.proc = subprocess.Popen(
            [_node_bin(), "--max-old-space-size=2048", str(ENGINE_JS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=env,
        )
        self._lock = threading.Lock()
        self._id = 0
        self._stderr: List[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.call("ping")

    def _drain_stderr(self):
        for line in self.proc.stderr:  # type: ignore[union-attr]
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 200:
                self._stderr = self._stderr[-100:]

    def call(self, cmd: str, **kwargs) -> Any:
        with self._lock:
            self._id += 1
            req = {"id": self._id, "cmd": cmd, **kwargs}
            req.setdefault("format", self.format)
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
                line = self.proc.stdout.readline()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError) as e:
                raise EngineError(f"El motor se cerró: {e}\n" + "\n".join(self._stderr[-20:]))
            if not line:
                raise EngineError("El motor no respondió.\n" + "\n".join(self._stderr[-20:]))
            resp = json.loads(line)
            if not resp.get("ok"):
                raise EngineError(resp.get("error", "error desconocido"))
            return resp["result"]

    def close(self):
        try:
            self.proc.stdin.close()  # type: ignore[union-attr]
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class EnginePool:
    """Varios procesos del motor para búsquedas en paralelo."""

    def __init__(self, n: Optional[int] = None, format: str = DEFAULT_FORMAT):
        n = n or max(1, min(6, (os.cpu_count() or 2) - 1))
        self.engines = [Engine(format) for _ in range(n)]
        self.exec = ThreadPoolExecutor(max_workers=n)

    @property
    def main(self) -> Engine:
        return self.engines[0]

    def search(self, worlds: List[dict], K: int = 8, Kopp: int = 8, seeds: int = 16,
               mega_policy: str = "asap", extra_ours: Optional[list] = None,
               extra_theirs: Optional[list] = None, engines: Optional[List["Engine"]] = None,
               lookahead: Optional[dict] = None, only_rows: Optional[list] = None,
               only_cols: Optional[list] = None, endgame: Optional[dict] = None) -> Dict[str, Any]:
        """Rellena la matriz KxK repartiendo semillas entre procesos y la resuelve.

        extra_ours / extra_theirs: jugadas concretas a incluir sí o sí (especificación por ranura,
        ver choiceFromSpec en engine/search.js).
        lookahead={"K2": 3}: cada hoja mira un turno más (mini-juego K2 x K2). only_rows / only_cols:
        sólo se simulan esas celdas (el resto de la matriz no es válido)."""
        engs = engines or self.engines
        n = len(engs)
        futs = [
            self.exec.submit(e.call, "search", worlds=worlds, K=K, Kopp=Kopp, seeds=seeds,
                             megaPolicy=mega_policy, partial=True, shard=i, nShards=n,
                             extraOurs=extra_ours or [], extraTheirs=extra_theirs or [],
                             **({"lookahead": lookahead} if lookahead else {}),
                             **({"endgame": endgame} if endgame else {}),
                             **({"onlyRows": only_rows} if only_rows is not None else {}),
                             **({"onlyCols": only_cols} if only_cols is not None else {}))
            for i, e in enumerate(engs)
        ]
        parts = [f.result() for f in futs]
        ours, theirs = parts[0]["ours"], parts[0]["theirs"]
        for p in parts[1:]:
            if [o["choice"] for o in p["ours"]] != [o["choice"] for o in ours]:
                raise EngineError("Los workers generaron candidatos distintos (no debería pasar)")
        R, C = len(ours), len(theirs)
        S = [[0.0] * C for _ in range(R)]
        N = [[0] * C for _ in range(R)]
        for p in parts:
            for i in range(R):
                for j in range(C):
                    S[i][j] += p["sum"][i][j]
                    N[i][j] += p["counts"][i][j]
        bad_rows = {i for p in parts for i in p.get("badRows", [])}
        bad_cols = {j for p in parts for j in p.get("badCols", [])}
        M = [[(S[i][j] / N[i][j]) if N[i][j] else (-1.0 if i in bad_rows else (1.0 if j in bad_cols else 0.0))
              for j in range(C)] for i in range(R)]
        from .solver import regret_matching
        row, col, value = regret_matching(M)
        return {"ours": ours, "theirs": theirs, "matrix": M, "row": row, "col": col, "value": value,
                "ms": max(p["ms"] for p in parts), "cells": sum(sum(r) for r in N),
                "bad_rows": sorted(bad_rows), "bad_cols": sorted(bad_cols)}

    def close(self):
        for e in self.engines:
            e.close()
        self.exec.shutdown(wait=False)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
