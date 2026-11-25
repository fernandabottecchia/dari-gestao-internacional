"""Nomes oficiais das etapas dos acordos no DARI (independentes dos códigos da planilha).

A sequência de 1 a 5 é preservada pelo mapeamento, não pelo texto exibido.
Valores desconhecidos são preservados e não classificados sem comprovação.
"""
from __future__ import annotations
import re

PROGRESS_STAGES = (
    'Em negociação',
    'Em elaboração (SIG+)',
    'Em tramitação (SEI)',
    'Aguardando assinatura',
    'Assinado',
)
_SOURCE_STAGE_MAP = {f'{i}_{label}': label for i, label in enumerate(PROGRESS_STAGES, 1)}
_STAGE_ALIASES = {label.casefold():label for label in PROGRESS_STAGES}


def normalize_progress(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw in _SOURCE_STAGE_MAP:
        return _SOURCE_STAGE_MAP[raw]
    folded = raw.casefold()
    if folded in _STAGE_ALIASES:
        return _STAGE_ALIASES[folded]
    # Aceitar número/código antes do rótulo somente quando corresponder à etapa.
    match = re.fullmatch(r'([1-5])\s*_\s*(.+)',raw)
    if match:
        expected = PROGRESS_STAGES[int(match.group(1))-1]
        if match.group(2).strip().casefold() == expected.casefold():
            return expected
    return raw


def progress_order(value):
    label = normalize_progress(value)
    if label in PROGRESS_STAGES:
        return PROGRESS_STAGES.index(label)
    return 5 if label and label != 'Não informado' else 6


def is_pending_progress(value):
    label = normalize_progress(value)
    return label in PROGRESS_STAGES[:4]
