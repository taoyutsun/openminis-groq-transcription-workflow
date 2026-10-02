#!/usr/bin/env python3
"""Build a source ZIP using an explicit allowlist, never the entire workspace."""
import argparse
import hashlib
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = '0.1.0-preview'
FILES = (
    '.gitignore', '.gitattributes', 'README.md', 'README.en.md', 'LICENSE', 'CHANGELOG.md',
    'groq_http.py', 'groq_transcribe.py', 'groq_workflow.py',
    'docs/PRIVACY.md', 'docs/TESTING.md', 'tests/test_workflow.py', 'scripts/package.py',
)
TOKEN = re.compile(r'(?:gsk_|sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}')
PRIVATE = re.compile(r'(?i)(?:C:[/\\]Users[/\\][A-Za-z0-9_.-]+|'
                     r'/home/[A-Za-z0-9_.-]+/|'
                     r'[A-F0-9]{8}-(?:[A-F0-9]{4}-){3}[A-F0-9]{12})')


def main():
    parser = argparse.ArgumentParser(description='只打包明確核准的公開原始碼與文件')
    parser.add_argument('--output', type=pathlib.Path, default=ROOT.parent / 'release-work')
    args = parser.parse_args()
    contents = {}
    for name in FILES:
        data = (ROOT / name).read_bytes()
        text = data.decode('utf-8')
        if TOKEN.search(text) or PRIVATE.search(text):
            raise SystemExit('公開前檢查未通過：' + name + '；不顯示疑似敏感內容')
        contents[name] = data
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / ('openminis-groq-transcription-workflow-' + VERSION + '.zip')
    if target.exists():
        raise SystemExit('打包檔案已存在；請使用另一個 --output，避免覆寫')
    prefix = 'openminis-groq-transcription-workflow-' + VERSION
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in contents.items():
            package.writestr(prefix + '/' + name, data)
    with zipfile.ZipFile(target, 'r') as package:
        if package.testzip() is not None or len(package.namelist()) != len(FILES):
            raise SystemExit('ZIP 驗證失敗')
        for name, data in contents.items():
            if package.read(prefix + '/' + name) != data:
                raise SystemExit('ZIP 內容比對失敗：' + name)
    print(str(target))
    print('Files: ' + str(len(FILES)))
    print('SHA256: ' + hashlib.sha256(target.read_bytes()).hexdigest())
    return 0


if __name__ == '__main__':
    sys.exit(main())
