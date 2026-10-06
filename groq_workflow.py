#!/usr/bin/env python3
"""User-selected audio folder -> original-language subtitles -> Traditional Chinese summary."""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import uuid

from groq_http import GroqError, api_key, chat, safe_error
from groq_transcribe import atomic_write, choose_backend, read_json, run, settings, sha256_file
from groq_transcribe import record_valid
import groq_summary
import minis_summary

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


def chat_retry(messages, model, max_tokens=2000):
    for attempt in range(4):
        try:
            return chat(messages, model, max_tokens)
        except GroqError as exc:
            if attempt == 3 or not exc.retryable:
                raise
            pause = max(60, exc.retry_after or 0) if exc.status == 429 else 5 * (attempt + 1)
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
    result = groq_summary.summarize(text, prompt_text(style), folder, identity,
                                   lambda messages, tokens: chat_retry(messages, model, tokens))
    save_summary(folder, stem, result, identity)


def save_summary(folder, stem, result, identity):
    dest = folder / (stem + '_摘要.md')
    # Preserve the old summary until the replacement is successfully generated.
    if dest.exists():
        backup = folder / (stem + '_摘要_先前版本_' + str(time.time_ns()) + '.md')
        atomic_write(backup, dest.read_text(encoding='utf-8'))
    atomic_write(dest, result + '\n')
    atomic_write(folder / '摘要設定.json', json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    # A verified rerun supersedes the active draft checkpoint, never its text file.
    if identity.get('backend') == 'minis':
        stamp = draft_stamp(folder, stem, identity)
        state = read_json(stamp)
        if isinstance(state, dict) and state.get('identity') == identity:
            state['verification'] = 'superseded_by_verified'
            atomic_write(stamp, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    print('摘要完成：' + dest.name, flush=True)


def draft_stamp(folder, stem, identity):
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()
    return folder / (stem + '_摘要草稿設定_' + digest + '.json')


def draft_ready(folder, stem, identity):
    """An edited draft is preserved, but cannot suppress a new model request."""
    state = read_json(draft_stamp(folder, stem, identity))
    if not isinstance(state, dict) or state.get('identity') != identity or state.get('verification') != 'completion_unverified':
        return None
    name = state.get('file')
    if not isinstance(name, str) or pathlib.Path(name).name != name or not name.startswith(stem + '_摘要_待核對草稿_') or not name.endswith('.md'):
        return None
    path = folder / name
    try:
        if path.is_file() and path.stat().st_size > 0 and state.get('sha256') == sha256_file(path):
            return path
    except OSError:
        pass
    return None


def save_draft(folder, stem, result, identity):
    # Unique immutable draft; never replace an edited draft or reviewed final summary.
    dest = folder / (stem + '_摘要_待核對草稿_' + uuid.uuid4().hex + '.md')
    with dest.open('x', encoding='utf-8') as handle:
        handle.write(result + '\n')
    state = {'verification': 'completion_unverified', 'identity': identity,
             'file': dest.name, 'sha256': sha256_file(dest)}
    atomic_write(draft_stamp(folder, stem, identity), json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    print('待核對草稿（缺少完成狀態，非完成摘要）：' + dest.name, flush=True)
    return dest


def complete_transcription(folder, source, meta, identity):
    """Require matching content hash and every expected validated checkpoint."""
    import math
    if not isinstance(meta, dict) or meta.get('complete') is not True:
        return False
    if meta.get('source') != source.name or meta.get('settings') != identity:
        return False
    try:
        duration = float(meta['duration_seconds'])
        seconds, overlap = identity['chunk_seconds'], identity['overlap']
        total = math.ceil(duration / seconds)
        if not math.isfinite(duration) or duration <= 0 or meta.get('chunks') != total:
            return False
        for index in range(total):
            offset = max(0, index * seconds - (overlap if index else 0))
            if not record_valid(read_json(folder / f'chunk_{index:03d}.json'), identity, index, offset):
                return False
        return all(path.is_file() and path.stat().st_size > 0 for path in
                   (folder / (source.stem + '.srt'), folder / (source.stem + '_逐字稿.txt')))
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return False


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
    p.add_argument('--summary-backend', choices=['minis', 'groq'],
                   default=os.environ.get('SUMMARY_BACKEND', 'minis'),
                   help='預設 minis：App 已授權模型；groq：明確選擇 Groq 分層摘要，不自動回退')
    p.add_argument('--minis-model', default=os.environ.get('MINIS_SUMMARY_MODEL', 'gpt-6-sol'))
    p.add_argument('--minis-provider', default=os.environ.get('MINIS_SUMMARY_PROVIDER'))
    p.add_argument('--minis-max-tokens', type=int, default=6000)
    p.add_argument('--minis-timeout', type=int, default=600)
    p.add_argument('--summary-style', choices=['original', 'cautious'], default='original')
    p.add_argument('--backend', choices=['auto', 'ffmpeg', 'sox'], default='auto')
    p.add_argument('--limit', type=int, default=1, help='最多處理幾份未完成檔，需大於零')
    p.add_argument('--force', action='store_true', help='另開新結果目錄重新轉錄')
    p.add_argument('--summary-only', action='store_true', help='只摘要本版本已完整轉錄的檔案')
    p.add_argument('--retry-summary', action='store_true', help='Minis 摘要重新呼叫，保留舊稿；搭配 --summary-only 不重傳音訊')
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
        if a.summary_backend not in ('groq', 'minis'):
            raise ValueError('SUMMARY_BACKEND 必須為 groq 或 minis')
        if not 1 <= a.minis_max_tokens <= 65536 or not 1 <= a.minis_timeout <= 3600:
            raise ValueError('Minis 輸出預算需為 1～65536，逾時需為 1～3600 秒')
        if a.retry_summary and a.summary_backend != 'minis':
            raise ValueError('--retry-summary 只適用 Minis 摘要')
        resolved = None
        if a.check:
            print('音訊工具：' + choose_backend(a.backend))
            print('GROQ_API_KEY：' + ('已設定' if os.environ.get('GROQ_API_KEY', '').strip() else '未設定'))
            if a.summary_backend == 'minis':
                resolved = minis_summary.resolve_model(a.minis_model, a.minis_provider)
                print('Minis 摘要模型已開放：' + resolved['model_id'])
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
        if a.summary_backend == 'minis' and not a.dry_run:
            # Fail before audio upload. Listing models never performs cloud inference.
            resolved = minis_summary.resolve_model(a.minis_model, a.minis_provider)
        count, failed = 0, 0
        stats = dict(verified=0, pending_review=0, failed=0, skipped_verified=0,
                     skipped_pending_review=0, skipped_no_transcript=0, previewed=0)
        output = a.output.expanduser()
        for source in files:
            identity = settings(source, a.language, a.model, backend)
            digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()[:12]
            folder = output / (source.stem + '__' + digest)
            meta = read_json(folder / 'metadata.json')
            transcript = folder / (source.stem + '_逐字稿.txt')
            complete = complete_transcription(folder, source, meta, identity)
            desired = None
            if complete and a.summary_backend == 'groq':
                desired = summary_identity(transcript, a.summary_model, a.summary_style)
            elif complete and resolved:
                desired = minis_summary.identity(transcript, resolved, a.summary_style,
                                                 prompt_text(a.summary_style), a.minis_max_tokens)
            pending_draft = False
            if complete and desired is not None and a.summary_backend == 'minis' and not a.force and not a.retry_summary:
                state = read_json(draft_stamp(folder, source.stem, desired))
                pending_draft = (isinstance(state, dict) and state.get('identity') == desired and
                                 state.get('verification') == 'completion_unverified')
                draft = draft_ready(folder, source.stem, desired)
                if draft:
                    print('已有待核對草稿（非完成摘要），略過呼叫：' + draft.name)
                    stats['skipped_pending_review'] += 1
                    continue
            if complete and desired is not None and summary_ready(folder, source.stem, desired) and not pending_draft and not a.force and not a.retry_summary:
                print('已完成，略過：' + source.name)
                stats['skipped_verified'] += 1
                continue
            if a.summary_only and not complete:
                print('沒有本版本完整轉錄，略過：' + source.name)
                stats['skipped_no_transcript'] += 1
                continue
            if not a.all and not a.file and count >= a.limit:
                break
            count += 1
            if a.force:
                folder = output / (source.stem + '__' + digest + '__redo_' + str(time.time_ns()))
                complete = False
                transcript = folder / (source.stem + '_逐字稿.txt')
            print(('預覽：' if a.dry_run else '處理：') + source.name + ' → ' + str(folder), flush=True)
            if a.dry_run:
                stats['previewed'] += 1
                continue
            try:
                if not complete or a.summary_backend == 'groq':
                    api_key()
                if not complete:
                    meta = run(source, folder, a.language, a.model, backend, identity=identity)
                    if not meta['complete']:
                        raise RuntimeError('轉錄尚未完成，未生成摘要')
                if not complete_transcription(folder, source, meta, identity):
                    raise RuntimeError('轉錄片段未完整驗證，禁止摘要')
                if a.summary_backend == 'minis':
                    desired = minis_summary.identity(transcript, resolved, a.summary_style,
                                                     prompt_text(a.summary_style), a.minis_max_tokens)
                    result = minis_summary.generate_result(transcript, resolved, prompt_text(a.summary_style),
                                                    a.minis_max_tokens, a.minis_timeout)
                    if result.verification == 'verified':
                        save_summary(folder, source.stem, result.text, desired)
                        stats['verified'] += 1
                    elif result.verification == 'completion_unverified':
                        save_draft(folder, source.stem, result.text, desired)
                        stats['pending_review'] += 1
                    else:
                        raise RuntimeError('未知摘要驗證狀態；未保存摘要')
                else:
                    summarize(folder, source.stem, a.summary_model, a.summary_style)
                    stats['verified'] += 1
            except KeyboardInterrupt:
                print('已中斷；相同設定重跑可使用已保存進度', file=sys.stderr)
                return 130
            except Exception as exc:
                print('失敗：' + source.name + '：' + safe_error(exc), file=sys.stderr, flush=True)
                failed += 1
        stats['failed'] = failed
        pending = stats['pending_review'] + stats['skipped_pending_review']
        status = 'failed' if failed else 'pending_review' if pending else 'preview' if a.dry_run else 'complete'
        print(f"本次處理 {count} 份；完成摘要 {stats['verified']} 份；新草稿 {stats['pending_review']} 份；既有待核對 {stats['skipped_pending_review']} 份；失敗 {failed} 份；輸出 {output}", flush=True)
        print('WORKFLOW_RESULT ' + json.dumps(dict(status=status, **stats), ensure_ascii=False), flush=True)
        return 1 if failed else 2 if pending else 0
    except Exception as exc:
        print(safe_error(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
