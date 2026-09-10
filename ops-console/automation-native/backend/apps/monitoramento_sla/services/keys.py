# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, time


def date_key(d: date | None) -> int | None:
    if d is None:
        return None
    return d.year * 10000 + d.month * 100 + d.day


def key_cwn(id_cliente: int, id_workflow: int, id_nh: int) -> int:
    return int(id_cliente) * 100_000_000 + int(id_workflow) * 100_000 + int(id_nh)


def chave_projecao(key: int, dkey: int) -> int:
    return int(key) * 100_000_000 + int(dkey)


def chave_nh(protocolo: str | int, id_nh: int) -> str:
    return f"{protocolo}|{id_nh}"


def time_to_seconds(t: time | None) -> int | None:
    if t is None:
        return None
    return t.hour * 3600 + t.minute * 60 + t.second


def seconds_to_time(sec: int) -> time:
    sec = max(0, int(sec)) % 86400
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return time(h, m, s)


def normalize_nome(nome: str | None) -> str:
    return " ".join((nome or "").strip().casefold().split())
