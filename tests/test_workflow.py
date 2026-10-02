"""Offline regression tests. Fixtures are generated, not real recordings."""
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import wave
from unittest import mock

import groq_http as http
import groq_transcribe as transcription
import groq_workflow as workflow

TEST_KEY = 'audit' + '-not-a-real-key-123'


def asr_data():
    return {'segments': [{'start': 3.0, 'end': 4.0, 'text': 'This is a synthetic test.'}]}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'GROQ_API_KEY': TEST_KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_request_keeps_key_in_header_and_not_subprocess(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(http.urllib.request, 'build_opener', return_value=opener), \
                mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('No subprocess allowed')):
            self.assertEqual(http._request('chat/completions', b'{}', 'application/json', 1), {'ok': True})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TEST_KEY)
        self.assertNotIn(TEST_KEY, request.full_url)
        self.assertNotIn(TEST_KEY.encode(), request.data)

    def test_http_error_does_not_expose_body_header_or_reason(self):
        exc = urllib.error.HTTPError(http.BASE, 401, TEST_KEY, {}, io.BytesIO(TEST_KEY.encode()))
        opener = mock.Mock()
        opener.open.side_effect = exc
        with mock.patch.object(http.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(http.GroqError) as caught:
                http._request('chat/completions', b'{}', 'application/json', 1)
        self.assertEqual(str(caught.exception), 'Groq HTTP 401')
        self.assertNotIn(TEST_KEY, str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_network_error_does_not_expose_underlying_message(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(TEST_KEY)
        with mock.patch.object(http.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(http.GroqError) as caught:
                http._request('audio/transcriptions', b'{}', 'application/json', 1)
        self.assertNotIn(TEST_KEY, str(caught.exception))
        self.assertTrue(caught.exception.retryable)

    def test_redirect_is_disabled(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError('offline')
        with mock.patch.object(http.urllib.request, 'build_opener', return_value=opener) as build:
            with self.assertRaises(http.GroqError):
                http._request('chat/completions', b'{}', 'application/json', 1)
        redirect = build.call_args.args[0]
        self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://example.invalid'))

    def test_key_is_redacted_even_without_known_provider_prefix(self):
        self.assertNotIn(TEST_KEY, http.safe_error(RuntimeError('command Authorization: Bearer ' + TEST_KEY)))
        self.assertNotIn('other-token', http.safe_error(RuntimeError('Bearer other-token')))

    def test_multipart_does_not_send_original_filename_and_auto_omits_language(self):
        with tempfile.TemporaryDirectory() as temp:
            audio = pathlib.Path(temp) / 'private-name.flac'
            audio.write_bytes(b'fake audio')
            with mock.patch.object(http, '_request', return_value=asr_data()) as request:
                http.transcribe(audio, 'whisper-large-v3-turbo', 'auto')
            body = request.call_args.args[1]
            self.assertIn(b'filename="chunk.flac"', body)
            self.assertNotIn(b'private-name', body)
            self.assertNotIn(b'name="language"', body)
            self.assertNotIn(TEST_KEY.encode(), body)

    def test_empty_summary_is_rejected(self):
        with mock.patch.object(http, '_request', return_value={'choices': [{'message': {'content': ''}}]}):
            with self.assertRaises(http.GroqError):
                http.chat([], 'example')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / 'input'
        self.source.mkdir()
        self.output = self.root / 'output'
        for name in ('課程一.m4a', '訪談二.mp3', '英文三.wav'):
            (self.source / name).write_bytes(('fixture:' + name).encode())
        self.base = ['--source', str(self.source), '--output', str(self.output)]

    def invoke(self, extra):
        log = io.StringIO()
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = workflow.main(self.base + extra)
        return code, log.getvalue()

    def test_list_has_no_key_media_or_network_dependency(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(workflow, 'choose_backend', side_effect=AssertionError('no media')), \
                mock.patch.object(workflow, 'api_key', side_effect=AssertionError('no key')):
            code, log = self.invoke(['--list'])
        self.assertEqual(code, 0)
        self.assertIn('課程一.m4a', log)
        self.assertFalse(self.output.exists())

    def test_dry_run_default_all_and_selected_multiple_do_not_write(self):
        with mock.patch.object(workflow, 'choose_backend', return_value='ffmpeg'), \
                mock.patch.object(workflow, 'run', side_effect=AssertionError('no transcription')), \
                mock.patch.object(workflow, 'summarize', side_effect=AssertionError('no summary')), \
                mock.patch.object(workflow, 'api_key', side_effect=AssertionError('no key')):
            for extra, expected in (([], 1), (['--all'], 3),
                                    (['--file', '課程一.m4a', '--file', '訪談二.mp3'], 2)):
                code, log = self.invoke(extra + ['--dry-run'])
                self.assertEqual(code, 0)
                self.assertEqual(log.count('預覽：'), expected)
        self.assertFalse(self.output.exists())

    def test_recursive_scan_excludes_output(self):
        nested = self.source / 'nested'
        nested.mkdir()
        (nested / 'nested.wav').write_bytes(b'fixture')
        self.output = self.source / 'output'
        self.output.mkdir()
        (self.output / 'do-not-pick.wav').write_bytes(b'fixture')
        args = workflow.parser().parse_args(['--source', str(self.source), '--output', str(self.output)])
        self.assertEqual(len(workflow.select_files(args)), 3)
        args.recursive = True
        self.assertEqual(len(workflow.select_files(args)), 4)

    def test_source_environment_default_and_cli_override(self):
        with mock.patch.dict(os.environ, {'GROQ_SOURCE_DIR': str(self.source)}):
            self.assertEqual(workflow.parser().parse_args([]).source, self.source)
            self.assertEqual(workflow.parser().parse_args(['--source', '/explicit']).source, pathlib.Path('/explicit'))

    def test_conflicting_or_unbounded_flags_rejected(self):
        for extra in (['--all', '--file', '課程一.m4a'], ['--force', '--summary-only'], ['--limit', '0']):
            code, _ = self.invoke(extra)
            self.assertEqual(code, 1)

    def test_summary_only_does_not_summarize_partial_result(self):
        with mock.patch.object(workflow, 'choose_backend', return_value='ffmpeg'), \
                mock.patch.object(workflow, 'summarize', side_effect=AssertionError('no summary')):
            code, log = self.invoke(['--summary-only'])
        self.assertEqual(code, 0)
        self.assertIn('沒有本版本完整轉錄', log)
        self.assertFalse(self.output.exists())

    def test_transcript_same_size_edit_changes_summary_identity(self):
        transcript = self.root / 'transcript.txt'
        transcript.write_text('AAAA', encoding='utf-8')
        before = workflow.summary_identity(transcript, 'model', 'original')
        transcript.write_text('BBBB', encoding='utf-8')
        self.assertNotEqual(before, workflow.summary_identity(transcript, 'model', 'original'))

    def test_cautious_only_appends_to_original_prompt(self):
        self.assertEqual(workflow.prompt_text('original'), workflow.PROMPT)
        self.assertEqual(workflow.prompt_text('cautious'), workflow.PROMPT + '\n\n' + workflow.CAUTIOUS)

    def test_failed_new_summary_preserves_existing_summary(self):
        folder = self.root / 'summary'
        folder.mkdir()
        (folder / 'sample_逐字稿.txt').write_text('Test transcript.', encoding='utf-8')
        dest = folder / 'sample_摘要.md'
        dest.write_text('Existing reviewed summary.', encoding='utf-8')
        with mock.patch.object(workflow, 'chat_retry', side_effect=http.GroqError('HTTP 401', 401)):
            with self.assertRaises(http.GroqError):
                workflow.summarize(folder, 'sample', 'model', 'original')
        self.assertEqual(dest.read_text(encoding='utf-8'), 'Existing reviewed summary.')

    def test_summary_resume_reuses_completed_notes(self):
        folder = self.root / 'summary'
        folder.mkdir()
        (folder / 'sample_逐字稿.txt').write_text('A' * 8000 + '\n' + 'B' * 8000, encoding='utf-8')
        with mock.patch.object(workflow, 'chat_retry', side_effect=['note one', http.GroqError('HTTP 401', 401)]):
            with self.assertRaises(http.GroqError):
                workflow.summarize(folder, 'sample', 'model', 'original')
        with mock.patch.object(workflow, 'chat_retry', side_effect=['note two', 'final summary']) as chat:
            workflow.summarize(folder, 'sample', 'model', 'original')
            self.assertEqual(chat.call_count, 2)
        self.assertEqual((folder / 'sample_摘要.md').read_text(encoding='utf-8'), 'final summary\n')
        with mock.patch.object(workflow, 'chat_retry', side_effect=AssertionError('must skip')):
            workflow.summarize(folder, 'sample', 'model', 'original')

    def test_chunk_resume_and_corrupt_cache_repair(self):
        source = self.source / '英文三.wav'
        out = self.root / 'chunks'
        def convert(src, target, start, length, backend):
            target.write_bytes(b'synthetic')
        with mock.patch.object(transcription, 'choose_backend', return_value='ffmpeg'), \
                mock.patch.object(transcription, 'duration_seconds', return_value=500), \
                mock.patch.object(transcription, 'convert_chunk', side_effect=convert), \
                mock.patch.object(transcription, 'transcribe', side_effect=[asr_data(), http.GroqError('HTTP 401', 401)]):
            with self.assertRaises(http.GroqError):
                transcription.run(source, out)
        self.assertTrue((out / 'chunk_000.json').exists())
        self.assertFalse(transcription.read_json(out / 'metadata.json')['complete'])
        with mock.patch.object(transcription, 'choose_backend', return_value='ffmpeg'), \
                mock.patch.object(transcription, 'duration_seconds', return_value=500), \
                mock.patch.object(transcription, 'convert_chunk', side_effect=convert), \
                mock.patch.object(transcription, 'transcribe', return_value=asr_data()) as call:
            self.assertTrue(transcription.run(source, out)['complete'])
            self.assertEqual(call.call_count, 2)
            (out / 'chunk_001.json').write_text('{broken', encoding='utf-8')
            call.reset_mock()
            transcription.run(source, out)
            self.assertEqual(call.call_count, 1)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg / ffprobe not installed')
class MediaIntegrationTests(unittest.TestCase):
    def test_generated_m4a_mocked_end_to_end_and_summary_mode_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / 'input'
            source.mkdir()
            wav = source / 'synthetic.wav'
            with wave.open(str(wav), 'wb') as file:
                file.setnchannels(1)
                file.setsampwidth(2)
                file.setframerate(16000)
                file.writeframes(b'\0\0' * 16000 * 5)
            audio = source / 'synthetic.m4a'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(wav),
                            '-c:a', 'aac', str(audio)], check=True, capture_output=True)
            self.assertAlmostEqual(transcription.duration_seconds(wav, 'ffmpeg'), 5.0, delta=0.2)
            self.assertAlmostEqual(transcription.duration_seconds(audio, 'ffmpeg'), 5.0, delta=0.2)
            args = ['--source', str(source), '--output', str(root / 'output'),
                    '--file', 'synthetic.m4a', '--language', 'en']
            with mock.patch.dict(os.environ, {'GROQ_API_KEY': TEST_KEY}), \
                    mock.patch.object(transcription, 'transcribe', return_value=asr_data()) as asr, \
                    mock.patch.object(workflow, 'chat', return_value='合成測試摘要。') as chat, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(workflow.main(args), 0)
                self.assertEqual(asr.call_count, 1)
                self.assertEqual(chat.call_count, 2)
                self.assertEqual(workflow.main(args), 0)
                self.assertEqual(asr.call_count, 1)
                self.assertEqual(chat.call_count, 2)
                self.assertEqual(workflow.main(args + ['--summary-style', 'cautious']), 0)
                self.assertEqual(asr.call_count, 1)
                self.assertEqual(chat.call_count, 4)
            folder = next((root / 'output').iterdir())
            self.assertIn('This is a synthetic test.', (folder / 'synthetic.srt').read_text(encoding='utf-8'))
            self.assertTrue(list(folder.glob('synthetic_摘要_先前版本_*.md')))
            self.assertTrue(transcription.read_json(folder / 'metadata.json')['complete'])


if __name__ == '__main__':
    unittest.main()
