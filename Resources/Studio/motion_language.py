"""Lossless normalization of short Portuguese locomotion commands for Kimodo."""
import re
import unicodedata


def kimodo_prompt(text):
    value=''.join(c for c in unicodedata.normalize('NFD',text.lower()) if not unicodedata.combining(c))
    value=re.sub(r'[.!?]+$','',value.strip())
    actions=[(r'(?:caminhar|caminhe|caminhas|andar|ande)(?: para (?:a )?frente)?','walks forward'),
        (r'(?:correr|corra|corrida)(?: para (?:a )?frente)?','runs forward'),
        (r'(?:pular|pule|saltar|salte)','jumps'),(r'(?:agachar|agache)','crouches down'),
        (r'(?:ficar parado|parado|repouso)','stands in a relaxed idle pose')]
    for pattern,action in actions:
        if re.fullmatch(pattern,value):return 'A person '+action+'.'
    return text
