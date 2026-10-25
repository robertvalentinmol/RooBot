"""Resolución de juegos de suma cero en forma matricial (turno simultáneo de VGC)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np


def regret_matching(M: Sequence[Sequence[float]], iters: int = 4000) -> Tuple[List[float], List[float], float]:
    """Regret matching+ con promedio lineal. M[i][j] = pago para el jugador fila (nosotros).

    Devuelve (estrategia_fila, estrategia_columna, valor_del_juego).
    """
    A = np.asarray(M, dtype=np.float64)
    R, C = A.shape
    r_reg = np.zeros(R)
    c_reg = np.zeros(C)
    r_sum = np.zeros(R)
    c_sum = np.zeros(C)

    def strat(reg):
        pos = np.maximum(reg, 0)
        s = pos.sum()
        return pos / s if s > 0 else np.full(len(reg), 1.0 / len(reg))

    for t in range(1, iters + 1):
        rs, cs = strat(r_reg), strat(c_reg)
        r_vals = A @ cs
        c_vals = -(rs @ A)
        r_reg = np.maximum(0, r_reg + r_vals - rs @ r_vals)
        c_reg = np.maximum(0, c_reg + c_vals - cs @ c_vals)
        r_sum += t * rs
        c_sum += t * cs
    row = r_sum / r_sum.sum()
    col = c_sum / c_sum.sum()
    return row.tolist(), col.tolist(), float(row @ A @ col)


def blend(row: Sequence[float], M: Sequence[Sequence[float]], worst_case_weight: float = 0.3) -> List[float]:
    """Mezcla el equilibrio con una política más segura (maximin puro), como hace mikumiku37
    al combinar 'peor caso' y 'valor esperado'. Devuelve una distribución sobre filas."""
    A = np.asarray(M)
    worst = A.min(axis=1)
    safe = np.zeros(len(row))
    safe[int(worst.argmax())] = 1.0
    p = (1 - worst_case_weight) * np.asarray(row) + worst_case_weight * safe
    return (p / p.sum()).tolist()
