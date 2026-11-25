"""Nomes canônicos de curso. Não altera a proveniência histórica das planilhas."""
import unicodedata

def _key(value):
    return ' '.join(''.join(ch for ch in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(ch)).casefold().split())

ALIASES={
    _key('Engenharia Civil - Macaé'):'Engenharia Civil',
    _key('Engenharia Eletrônica e Automação'):'Engenharia de Controle e Automação',
    _key('Engenharia Mecânica/ Escola de Música'):'Engenharia Mecânica',
    _key('Engenharia Metalúrgica e de Materiais'):'Engenharia Metalúrgica',
}

def canonical(value):
    return ALIASES.get(_key(value),value)
