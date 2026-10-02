#!/usr/bin/env python3
"""User-selected audio folder -> original-language subtitles -> Traditional Chinese summary."""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

from groq_http import GroqError, api_key, chat, safe_error
from groq_transcribe import atomic_write, choose_backend, read_json, run, settings, sha256_file

DEFAULT_SOURCE = '/var/minis/shared/錄音輸入'
DEFAULT_OUTPUT = '/var/minis/shared/錄音逐字稿'
EXTENSIONS = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.mp4', '.aac', '.webm'}
# Keep the author's original prompt unchanged. Extra caution is opt-in.
PROMPT = '''音檔經由語音辨識轉換的文字內容，由於是語音辨識有些文字可能需要用前後文的脈絡去更正。 請使用 Markdown 語法並使用繁體中文，幫我總結關鍵資訊和詳細重點內容。你的回應應該以清晰的方式總結原文中的主要資訊和重要內容，使用適當的標題、標記和格式，以便易於閱讀和理解。 請注意，你的回應應該保留原文中的相關詳細資訊，同時以簡潔明了的方式呈現。你可以自由選擇要重點突出的內容，並使用適當的Markdown標記來強調。'''
CAUTIOUS = '若逐字稿中的專有名詞、人名或數字因語音辨識而不確定，請標示「待核對」，不要憑空補寫。請保留能確認的重要細節。'
SUMMARY_VERSION = 1


def prompt_text(style):
    return PROMPT if style == 'original' else PROMPT + '\n\n' + CAUTIOUS


def summary_identity(transcript, model, style):
    return {'model': model, 'style': style, 'summary_version': SUMMARY_VERSION,
            'prompt_sha256': hashlib.sha256(prompt_text(style).encode('utf-8')).hexdigest(),
            'transcript_sha256': sha256_file(transcript)}


def summary_ready(folder, stem, identity):
    dest = folder / (stem + '_摘要.md')
    return (dest.is_file() and dest.stat().st_size > 0 and
            read_json(folder / '摘要設定.json') == identity)


def chat_retry(messages, model):
    for attempt in range(4):
        try:
            return chat(messages, model)
        except GroqError as exc:
            if attempt == 3 or not exc.retryable:
                raise
            pause = min(90, exc.retry_after or (30 if exc.status == 429 else 5 * (attempt + 1)))
            print(f'摘要重試：{safe_error(exc)}；等待 {pause:.0f} 秒', flush=True)
            time.sleep(pause)


def summarize(folder, stem, model, style):
    transcript = folder / (stem + '_逐字稿.txt')
    identity = summary_identity(transcript, model, style)
    if summary_ready(folder, stem, identity):
        print('摘要設定與逐字稿相符，略過', flush=True)
        return
    text = transcript.read_text(encoding='utf-8')
    if not text.strip():
        raise RuntimeError('逐字稿為空，未呼叫摘要 API；請檢查音訊及辨識結果')
    batches, current, length = [], [], 0
    for line in text.splitlines():
        # Bound individual lines too, rather than assuming all ASR segments are short.
        parts = [line[i:i + 9000] for i in range(0, len(line), 9000)] or ['']
        for part in parts:
            if length + len(part) + 1 > 9000 and current:
                batches.append('\n'.join(current))
                current, length = [], 0
            current.append(part)
            length += len(part) + 1
    if current:
        batches.append('\n'.join(current))
    progress = folder / '摘要進度.json'
    prior = read_json(progress)
    notes = []
    if isinstance(prior, dict) and prior.get('summary_config') == identity:
        candidate = prior.get('notes')
        if (isinstance(candidate, list) and len(candidate) <= len(batches) and
                all(isinstance(note, str) and note.strip() for note in candidate)):
            notes = candidate
    prompt = prompt_text(style)
    for index, part in enumerate(batches[len(notes):], len(notes) + 1):
        print(f'摘要前處理 {index}/{len(batches)}', flush=True)
        notes.append(chat_retry([{'role': 'user', 'content': prompt +
                    '\n\n以下是逐字稿的一部分；請先摘要這一部分，以便稍後整合完整摘要：\n' + part}], model))
        atomic_write(progress, json.dumps({'summary_config': identity, 'notes': notes},
                                          ensure_ascii=False, indent=2))
    print('整合繁中摘要', flush=True)
    result = chat_retry([{'role': 'user', 'content': prompt +
                        '\n\n以下是同一份逐字稿的分段摘要；請統整成一份完整摘要，保留主要資訊與重要細節：\n\n' +
                        '\n\n'.join(notes)}], model)
    dest = folder / (stem + '_摘要.md')
    # Preserve the old summary until the replacement is successfully generated.
    if dest.exists():
        backup = folder / (stem + '_摘要_先前版本_' + str(time.time_ns()) + '.md')
        atomic_write(backup, dest.read_text(encoding='utf-8'))
    atomic_write(dest, result + '\n')
    atomic_write(folder / '摘要設定.json', json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    print('摘要完成：' + dest.name, flush=True)


def parser():
    p = argparse.ArgumentParser(description='Open Minis 通用錄音轉錄與摘要 workflow')
    p.add_argument('--source', type=pathlib.Path,
                   default=os.environ.get('GROQ_SOURCE_DIR', DEFAULT_SOURCE))
    p.add_argument('--output', type=pathlib.Path,
                   default=os.environ.get('GROQ_OUTPUT_DIR', DEFAULT_OUTPUT))
    p.add_argument('--file', type=pathlib.Path, action='append', help='指定單檔或多檔，可重複使用')
    p.add_argument('--all', action='store_true', help='處理全部未完成音檔')
    p.add_argument('--recursive', action='store_true', help='掃描來源下的子資料夾')
    p.add_argument('--language', choices=['auto', 'en', 'zh'], default='auto')
    p.add_argument('--model', default='whisper-large-v3-turbo')
    p.add_argument('--summary-model', default='openai/gpt-oss-120b')
    p.add_argument('--summary-style', choices=['original', 'cautious'], default='original')
    p.add_argument('--backend', choices=['auto', 'ffmpeg', 'sox'], default='auto')
    p.add_argument('--limit', type=int, default=1, help='最多處理幾份未完成檔，需大於零')
    p.add_argument('--force', action='store_true', help='另開新結果目錄重新轉錄')
    p.add_argument('--summary-only', action='store_true', help='只摘要本版本已完整轉錄的檔案')
    p.add_argument('--dry-run', action='store_true', help='預覽處理範圍，不上傳、不寫入')
    p.add_argument('--list', action='store_true', help='列出候選音檔，不上傳、不寫入')
    p.add_argument('--check', action='store_true', help='只檢查工具與金鑰是否設定，不呼叫 API')
    return p


def select_files(a):
    source, output = a.source.expanduser(), a.output.expanduser()
    if a.all and a.file:
        raise ValueError('--all 與 --file 不可併用')
    if a.force and a.summary_only:
        raise ValueError('--force 與 --summary-only 不可併用')
    if a.limit <= 0:
        raise ValueError('--limit 必須大於零；處理全部請明確使用 --all')
    if a.file:
        files = list(dict.fromkeys(path.expanduser() if path.is_absolute() else source / path
                                   for path in a.file))
        if any(not path.is_file() or path.suffix.lower() not in EXTENSIONS for path in files):
            raise ValueError('指定檔案不存在或副檔名不支援')
        return files
    if not source.is_dir():
        raise ValueError('來源資料夾不存在；請先匯出音檔或掛載資料夾，再指定 --source')
    # A recursive scan must not pick up files under the workflow output directory.
    output_root = output.resolve()
    files = []
    for path in (source.rglob('*') if a.recursive else source.iterdir()):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        resolved = path.resolve()
        if output_root == resolved or output_root in resolved.parents:
            continue
        files.append(path)
    return sorted(files, key=lambda path: str(path))


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    try:
        if a.check:
            print('音訊工具：' + choose_backend(a.backend))
            print('GROQ_API_KEY：' + ('已設定' if os.environ.get('GROQ_API_KEY', '').strip() else '未設定'))
            print('檢查僅限本機；未驗證金鑰有效性、來源權限或 API 可用性')
            return 0
        files = select_files(a)
        if not files:
            print('未找到支援的音檔')
            return 0
        if a.list:
            for path in files:
                print(str(path))
            return 0
        backend = choose_backend(a.backend)
        count, failed = 0, 0
        output = a.output.expanduser()
        for source in files:
            identity = settings(source, a.language, a.model, backend)
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()[:12]
            folder = output / (source.stem + '__' + digest)
            meta = read_json(folder / 'metadata.json')
            transcript = folder / (source.stem + '_逐字稿.txt')
            complete = (isinstance(meta, dict) and meta.get('complete') is True and
                        meta.get('source') == source.name and meta.get('settings') == identity and
                        (folder / (source.stem + '.srt')).is_file() and transcript.is_file())
            desired = summary_identity(transcript, a.summary_model, a.summary_style) if complete else None
            if complete and summary_ready(folder, source.stem, desired) and not a.force:
                print('已完成，略過：' + source.name)
                continue
            if a.summary_only and not complete:
                print('沒有本版本完整轉錄，略過：' + source.name)
                continue
            if not a.all and not a.file and count >= a.limit:
                break
            count += 1
            if a.force:
                folder = output / (source.stem + '__' + digest + '__redo_' + str(time.time_ns()))
                complete = False
            print(('預覽：' if a.dry_run else '處理：') + source.name + ' → ' + str(folder), flush=True)
            if a.dry_run:
                continue
            try:
                api_key()
                if not complete:
                    meta = run(source, folder, a.language, a.model, backend, identity=identity)
                    if not meta['complete']:
                        raise RuntimeError('轉錄尚未完成，未生成摘要')
                summarize(folder, source.stem, a.summary_model, a.summary_style)
            except KeyboardInterrupt:
                print('已中斷；相同設定重跑可使用已保存進度', file=sys.stderr)
                return 130
            except Exception as exc:
                print('失敗：' + source.name + '：' + safe_error(exc), file=sys.stderr, flush=True)
                failed += 1
        print(f'本次處理 {count} 份；失敗 {failed} 份；輸出 {output}', flush=True)
        return 1 if failed else 0
    except Exception as exc:
        print(safe_error(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
