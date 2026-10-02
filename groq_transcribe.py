#!/usr/bin/env python3
"""Chunked original-language transcription with validated, atomic checkpoints."""
import argparse
import hashlib
import json
import math
import pathlib
import shutil
import subprocess
import tempfile
import time

from groq_http import GroqError, safe_error, transcribe

TRANSCRIPTION_VERSION = 1


def atomic_write(path, text):
    path = pathlib.Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(path.parent),
                                     prefix='.' + path.name, suffix='.tmp', delete=False) as file:
        temp = pathlib.Path(file.name)
        try:
            file.write(text)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def choose_backend(name='auto'):
    if name in ('auto', 'ffmpeg') and shutil.which('ffmpeg') and shutil.which('ffprobe'):
        return 'ffmpeg'
    if name in ('auto', 'sox') and shutil.which('sox'):
        return 'sox'
    raise RuntimeError('找不到音訊工具；請安裝 ffmpeg（含 ffprobe），或用支援來源格式的 sox')


def media_command(command):
    try:
        return subprocess.check_output(command, stderr=subprocess.PIPE, text=True)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError('音訊工具執行失敗；請確認檔案可讀、格式與解碼器支援') from None


def duration_seconds(source, backend):
    if backend == 'ffmpeg':
        raw = media_command(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                             '-show_entries', 'format=duration', '-of',
                             'default=noprint_wrappers=1:nokey=1', str(source)])
    else:
        raw = media_command(['sox', '--i', '-D', str(source)])
    try:
        seconds = float(raw.strip())
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError()
        return seconds
    except ValueError:
        raise RuntimeError('無法取得有效音訊長度') from None


def convert_chunk(source, target, start, length, backend):
    if backend == 'ffmpeg':
        command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source),
                   '-ss', str(start), '-t', str(length), '-map', '0:a:0',
                   '-ar', '16000', '-ac', '1', '-c:a', 'flac', str(target)]
    else:
        command = ['sox', str(source), '-r', '16000', '-c', '1', str(target),
                   'trim', str(start), str(length)]
    media_command(command)


def stamp(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def settings(source, language, model, backend, chunk_seconds=240, overlap=3):
    return {'source_sha256': sha256_file(source), 'language': language, 'model': model,
            'backend': backend, 'chunk_seconds': chunk_seconds, 'overlap': overlap,
            'transcription_version': TRANSCRIPTION_VERSION}


def record_valid(record, identity, index, offset):
    if not isinstance(record, dict):
        return False
    if record.get('settings') != identity or record.get('index') != index or record.get('offset') != offset:
        return False
    data = record.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('segments'), list):
        return False
    for segment in data['segments']:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str):
            return False
        try:
            start, end = float(segment['start']), float(segment['end'])
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                return False
        except (KeyError, ValueError, TypeError):
            return False
    return True


def run(source, output, language='auto', model='whisper-large-v3-turbo', backend='auto',
        chunk_seconds=240, overlap=3, max_chunks=0, identity=None):
    source, output = pathlib.Path(source), pathlib.Path(output)
    if chunk_seconds <= 0 or overlap < 0 or overlap >= chunk_seconds or max_chunks < 0:
        raise ValueError('分段秒數需大於零；重疊秒數需小於分段秒數；max-chunks 不可為負數')
    backend = choose_backend(backend)
    expected = settings(source, language, model, backend, chunk_seconds, overlap)
    if identity is not None and identity != expected:
        raise RuntimeError('來源檔案或設定在啟動後改變，請重新執行')
    identity = expected
    duration = duration_seconds(source, backend)
    total_chunks = math.ceil(duration / chunk_seconds)
    chunks = min(total_chunks, max_chunks) if max_chunks else total_chunks
    output.mkdir(parents=True, exist_ok=True)
    # An interrupted rerun must not leave an old completed marker in place.
    meta = {'source': source.name, 'duration_seconds': duration, 'settings': identity,
            'chunks': chunks, 'complete': False}
    atomic_write(output / 'metadata.json', json.dumps(meta, ensure_ascii=False, indent=2))
    print(f'音訊 {duration:.1f} 秒；{chunks} 段；{backend}', flush=True)
    records = []
    for index in range(chunks):
        start = max(0, index * chunk_seconds - (overlap if index else 0))
        length = min(duration - start, chunk_seconds + (overlap if index else 0))
        dest = output / f'chunk_{index:03d}.json'
        record = read_json(dest)
        if not record_valid(record, identity, index, start):
            with tempfile.TemporaryDirectory() as temp:
                audio = pathlib.Path(temp) / 'chunk.flac'
                convert_chunk(source, audio, start, length, backend)
                for attempt in range(4):
                    try:
                        data = transcribe(audio, model, language)
                        break
                    except GroqError as exc:
                        print('轉錄重試：' + safe_error(exc), flush=True)
                        if attempt == 3 or not exc.retryable:
                            raise
                        time.sleep(min(90, exc.retry_after or 3 * (attempt + 1)))
                record = {'settings': identity, 'index': index, 'offset': start, 'data': data}
                if not record_valid(record, identity, index, start):
                    raise RuntimeError('缺少有效的時間戳 segments，未保存此片段')
                atomic_write(dest, json.dumps(record, ensure_ascii=False))
            print(f'[{index + 1}/{chunks}] 已保存', flush=True)
        else:
            print(f'[{index + 1}/{chunks}] 使用續跑片段', flush=True)
        records.append(record)
    segments = []
    for index, record in enumerate(records):
        cutoff = index * chunk_seconds
        for segment in record['data']['segments']:
            start = record['offset'] + float(segment['start'])
            end = min(duration, record['offset'] + float(segment['end']))
            text = segment['text'].strip()
            if not text or end <= cutoff - 0.3:
                continue
            if index and start < cutoff:
                start = cutoff
            if end > start:
                segments.append((start, end, text))
    srt = '\n'.join(f'{i}\n{stamp(start)} --> {stamp(end)}\n{text}\n'
                    for i, (start, end, text) in enumerate(segments, 1))
    transcript = '\n'.join(f'[{stamp(start)}] {text}' for start, end, text in segments)
    atomic_write(output / (source.stem + '.srt'), srt + '\n')
    atomic_write(output / (source.stem + '_逐字稿.txt'), transcript + '\n')
    meta.update(complete=chunks == total_chunks, segments=len(segments))
    atomic_write(output / 'metadata.json', json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def main():
    parser = argparse.ArgumentParser(description='分段轉錄並輸出原語言 SRT／逐字稿')
    parser.add_argument('source', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--language', choices=['auto', 'en', 'zh'], default='auto')
    parser.add_argument('--model', default='whisper-large-v3-turbo')
    parser.add_argument('--backend', choices=['auto', 'ffmpeg', 'sox'], default='auto')
    parser.add_argument('--chunk-seconds', type=int, default=240)
    parser.add_argument('--overlap', type=int, default=3)
    parser.add_argument('--max-chunks', type=int, default=0)
    args = parser.parse_args()
    try:
        run(**vars(args))
    except Exception as exc:
        parser.exit(1, safe_error(exc) + '\n')


if __name__ == '__main__':
    main()
