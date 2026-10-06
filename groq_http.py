"""Groq requests using the Python standard library; credentials stay out of argv."""
import json
import math
import os
import re
import socket
import ssl
import urllib.error
import urllib.request
import uuid

BASE = 'https://api.groq.com/openai/v1/'
USER_AGENT = 'OpenMinis-Transcription-Workflow/0.2.1'


class GroqError(RuntimeError):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self):
        return self.status is None or self.status in (429, 500, 502, 503, 504)


def api_key():
    key = os.environ.get('GROQ_API_KEY', '').strip()
    if not key:
        raise GroqError('缺少 GROQ_API_KEY 環境變數', status=0)
    if '\r' in key or '\n' in key:
        raise GroqError('GROQ_API_KEY 含有換行，請重新設定', status=0)
    return key


def safe_error(exc):
    """Defence in depth for callers; never print an entire command or HTTP body."""
    text = str(exc)
    key = os.environ.get('GROQ_API_KEY', '').strip()
    if key:
        text = text.replace(key, '[REDACTED]')
    text = re.sub(r'(?i)(?:gsk_|sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}',
                  '[REDACTED]', text)
    text = re.sub(r'(?i)Bearer\s+[^\s\'";,]+', 'Bearer [REDACTED]', text)
    return text[:500]


def _request(endpoint, body, content_type, timeout):
    # Pin endpoint and reject redirects so Authorization cannot follow a redirect.
    if endpoint not in ('audio/transcriptions', 'chat/completions'):
        raise GroqError('不支援的 API 端點', status=0)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    req = urllib.request.Request(BASE + endpoint, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + api_key(),
        'Content-Type': content_type,
        'Accept': 'application/json',
        'User-Agent': USER_AGENT,
    })
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        retry_after = None
        try:
            seconds = float(exc.headers.get('Retry-After', ''))
            retry_after = max(1, seconds) if math.isfinite(seconds) else None
        except (ValueError, TypeError, AttributeError):
            pass
        exc.close()
        raise GroqError('Groq HTTP ' + str(status), status, retry_after) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
        raise GroqError('Groq 連線失敗或逾時；請檢查網路及 TLS 憑證') from None
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise GroqError('Groq 回傳無法解析的 JSON', status=0) from None
    if not isinstance(data, dict):
        raise GroqError('Groq 回傳格式不符合預期', status=0)
    return data


def transcribe(audio, model, language):
    boundary = 'minis-' + uuid.uuid4().hex
    parts = []
    fields = [('model', model), ('response_format', 'verbose_json'),
              ('timestamp_granularities[]', 'segment')]
    if language != 'auto':
        fields.append(('language', language))
    for name, value in fields:
        parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' +
                      name + '"\r\n\r\n' + value + '\r\n').encode('utf-8'))
    parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; '
                  'filename="chunk.flac"\r\nContent-Type: audio/flac\r\n\r\n').encode('ascii'))
    parts.extend((audio.read_bytes(), b'\r\n', ('--' + boundary + '--\r\n').encode('ascii')))
    return _request('audio/transcriptions', b''.join(parts),
                    'multipart/form-data; boundary=' + boundary, 180)


def chat(messages, model, max_tokens=2000):
    payload = json.dumps({'model': model, 'messages': messages, 'stream': False,
                          'temperature': 0.2, 'max_completion_tokens': max_tokens},
                         ensure_ascii=False).encode('utf-8')
    data = _request('chat/completions', payload, 'application/json', 150)
    try:
        text = data['choices'][0]['message'].get('content') or ''
    except (KeyError, IndexError, AttributeError, TypeError):
        raise GroqError('Groq 摘要回應格式不符合預期', status=0) from None
    if data['choices'][0].get('finish_reason') not in ('stop',):
        raise GroqError('Groq 摘要未正常完成；請分檔或調整摘要模式', status=413)
    if not isinstance(text, str) or not text.strip():
        raise GroqError('文字模型回傳空內容；請檢查摘要模型與 token 限制', status=0)
    return text.strip()
