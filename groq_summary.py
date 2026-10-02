"""Bounded hierarchical Groq summaries with persisted nodes and explicit pacing."""
import hashlib
import json
import time

from groq_http import GroqError
from groq_transcribe import atomic_write, read_json

INPUT_BUDGET = 3500
OUTPUT_TOKENS = 2000
MIN_INTERVAL = 60
_last_call = None


def estimate(text):
    # Deliberately approximate; account/provider tokenization can differ.
    return sum(1.5 if ord(ch) > 127 else 0.5 for ch in text)


def split_text(text, budget):
    if budget < 1:
        raise RuntimeError('摘要 prompt 已超過輸入預算')
    chunks, start, used = [], 0, 0
    for index, char in enumerate(text):
        cost = estimate(char)
        if used + cost > budget and index > start:
            chunks.append(text[start:index])
            start, used = index, 0
        used += cost
    if start < len(text):
        chunks.append(text[start:])
    return chunks


def summarize(text, prompt, folder, identity, call):
    if not text.strip():
        raise RuntimeError('逐字稿為空，禁止摘要')
    progress = folder / '摘要進度.json'
    prior = read_json(progress)
    cache = (prior.get('cache', {}) if isinstance(prior, dict) and
             prior.get('summary_config') == identity and prior.get('algorithm') == 'bounded-tree-v2' else {})
    if not isinstance(cache, dict):
        cache = {}

    def save():
        atomic_write(progress, json.dumps({'summary_config': identity,
                     'algorithm': 'bounded-tree-v2', 'cache': cache}, ensure_ascii=False, indent=2))

    def reduce(part, instruction, depth=0):
        global _last_call
        if depth > 24:
            raise RuntimeError('摘要分層超過安全上限；進度保留，請分檔')
        prefix = prompt + '\n\n' + instruction + '\n'
        budget = INPUT_BUDGET - estimate(prefix)
        key = hashlib.sha256((prefix + '\0' + part).encode('utf-8')).hexdigest()
        if isinstance(cache.get(key), str) and cache[key].strip():
            return cache[key]
        pieces = split_text(part, budget)
        if len(pieces) == 1:
            if _last_call is not None:
                wait = max(0, MIN_INTERVAL - (time.monotonic() - _last_call))
                if wait:
                    print(f'Groq 摘要節流：等待 {wait:.0f} 秒', flush=True)
                    time.sleep(wait)
            _last_call = time.monotonic()
            try:
                value = call([{'role': 'user', 'content': prefix + part}], OUTPUT_TOKENS)
                cache[key] = value
                save()
                return value
            except GroqError as exc:
                if exc.status != 413 or len(part) < 2:
                    raise
                middle = len(part) // 2
                pieces = [part[:middle], part[middle:]]
        notes = [reduce(piece, '請摘要此連續片段，保留時間、數字與重要細節：', depth + 1)
                 for piece in pieces]
        joined = '\n\n'.join(notes)
        if estimate(joined) >= estimate(part) and estimate(joined) > budget:
            raise RuntimeError('分層摘要無法縮減；保留進度，請分檔或選擇其他摘要後端')
        value = reduce(joined, '請整合以下連續摘要，保留時間、數字與重要細節：', depth + 1)
        cache[key] = value
        save()
        return value

    instruction = '以下是逐字稿的一部分；請摘要此部分以便整合，保留時間、數字與重要細節：'
    batches = split_text(text, INPUT_BUDGET - estimate(prompt + '\n\n' + instruction + '\n'))
    notes = []
    for index, part in enumerate(batches, 1):
        print(f'摘要前處理 {index}/{len(batches)}', flush=True)
        notes.append(reduce(part, instruction))
    while len(notes) > 1:
        notes = [reduce('\n\n'.join(notes[i:i+2]), '請整合以下連續摘要，保留重要細節：')
                 if len(notes[i:i+2]) > 1 else notes[i] for i in range(0, len(notes), 2)]
    return reduce(notes[0], '請統整成完整繁體中文摘要，保留主要資訊與重要細節：')
