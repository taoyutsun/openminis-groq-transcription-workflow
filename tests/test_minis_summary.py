"""App bridge regression tests: synthetic text only, no credentials or network."""
import contextlib
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import groq_http
import groq_summary
import groq_transcribe as transcribe
import groq_workflow as workflow
import minis_summary as minis

MODEL = {'entry_id': 'example/gpt-6-sol', 'model_id': 'gpt-6-sol',
         'display_name': 'GPT-6-Sol', 'instance_label': 'example',
         'modalities': ['text_input', 'text_output'], 'context_window': 1000000}


class BridgeTests(unittest.TestCase):
    def test_android_catalog_resolves_exact_model(self):
        proc = subprocess.CompletedProcess([], 0, stdout=json.dumps({'models': [MODEL]}), stderr='')
        with mock.patch.object(minis.shutil, 'which', return_value='minis-model-use'), \
                mock.patch.object(minis.subprocess, 'run', return_value=proc):
            self.assertEqual(minis.resolve_model(), MODEL)

    def test_default_is_minis_and_exact_named_model(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            args = workflow.parser().parse_args([])
        self.assertEqual(args.summary_backend, 'minis')
        self.assertEqual(args.minis_model, 'gpt-6-sol')

    def test_exact_model_resolution_and_ambiguous_provider(self):
        other = dict(MODEL, entry_id='second/gpt-6-sol', instance_label='second')
        with mock.patch.object(minis, 'bridge', return_value={'models': [MODEL, other]}):
            with self.assertRaises(RuntimeError):
                minis.resolve_model()
            self.assertEqual(minis.resolve_model(provider='example'), MODEL)
            with self.assertRaises(RuntimeError):
                minis.resolve_model('GPT-6')

    def test_unexposed_or_nontext_model_is_rejected(self):
        for models in ([], [dict(MODEL, modalities=['image_output'])]):
            with mock.patch.object(minis, 'bridge', return_value={'models': models}):
                with self.assertRaises(RuntimeError):
                    minis.resolve_model()

    def test_bridge_errors_do_not_print_raw_output(self):
        with mock.patch.object(minis.shutil, 'which', return_value='minis-model-use'):
            for stdout in ('not json', '[]', '{"ok":false,"error":{"message":"private diagnostic"}}', '{"ok":true,"data":[]}'):
                proc = subprocess.CompletedProcess([], 0, stdout=stdout, stderr='private diagnostic')
                with mock.patch.object(minis.subprocess, 'run', return_value=proc):
                    with self.assertRaises(RuntimeError) as caught:
                        minis.bridge(['list'], 1)
                    self.assertNotIn('private diagnostic', str(caught.exception))

    def test_missing_command_and_timeout_are_actionable(self):
        with mock.patch.object(minis.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'minis-model-use'):
                minis.bridge(['list'], 1)
        with mock.patch.object(minis.shutil, 'which', return_value='minis-model-use'), \
                mock.patch.object(minis.subprocess, 'run', side_effect=subprocess.TimeoutExpired('command', 1, output='private diagnostic')):
            with self.assertRaisesRegex(RuntimeError, '逾時') as caught:
                minis.bridge(['run'], 1)
            self.assertNotIn('private diagnostic', str(caught.exception))


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = pathlib.Path(self.temp.name)
        self.transcript = self.folder / 'sample_逐字稿.txt'
        self.transcript.write_text('Synthetic transcript.', encoding='utf-8')
        self.dest = self.folder / 'sample_摘要.md'
        self.dest.write_text('Existing reviewed summary.', encoding='utf-8')

    def response(self, reason='completed', content='合成測試摘要。', mid='gpt-6-sol'):
        def call(arguments, timeout):
            source = pathlib.Path(arguments[arguments.index('--input') + 1])
            payload = json.loads(source.read_text(encoding='utf-8'))
            self.assertIn('Synthetic', payload['messages'][0]['content'])
            self.assertEqual(arguments[arguments.index('--model') + 1], MODEL['entry_id'])
            output = pathlib.Path(arguments[arguments.index('--output') + 1])
            output.write_text(json.dumps({'content': content, 'model_id': mid}), encoding='utf-8')
            return {'model_id': mid, 'stop_reason': reason}
        return call

    def test_successful_summary_and_temp_cleanup(self):
        with mock.patch.object(minis, 'bridge', side_effect=self.response()) as call:
            self.assertEqual(minis.generate(self.transcript, MODEL, 'Prompt'), '合成測試摘要。')
        output = pathlib.Path(call.call_args.args[0][call.call_args.args[0].index('--output') + 1])
        self.assertFalse(output.exists())

    def test_truncation_interruption_and_unknown_stop_are_rejected(self):
        for reason in ('max_tokens', 'length', 'incomplete', 'cancelled', None):
            with mock.patch.object(minis, 'bridge', side_effect=self.response(reason=reason)):
                with self.assertRaises(RuntimeError):
                    minis.generate(self.transcript, MODEL, 'Prompt')
            self.assertEqual(self.dest.read_text(encoding='utf-8'), 'Existing reviewed summary.')
            self.assertFalse((self.folder / '摘要設定.json').exists())

    def test_empty_and_wrong_model_are_rejected(self):
        for kwargs in ({'content': ''}, {'mid': 'another-model'}):
            with mock.patch.object(minis, 'bridge', side_effect=self.response(**kwargs)):
                with self.assertRaises(RuntimeError):
                    minis.generate(self.transcript, MODEL, 'Prompt')

    def test_context_budget_is_checked_before_model_call(self):
        with mock.patch.object(minis, 'bridge', side_effect=AssertionError('no cloud')):
            with self.assertRaisesRegex(RuntimeError, '預算'):
                minis.generate(self.transcript, dict(MODEL, context_window=100), 'Prompt')

    def test_exhausted_output_usage_is_rejected_even_if_stop_looks_normal(self):
        result = {'model_id': 'gpt-6-sol', 'stop_reason': 'end_turn', 'usage': {'output_tokens': 6000}}
        with mock.patch.object(minis, 'bridge', return_value=result):
            with self.assertRaisesRegex(RuntimeError, '輸出預算'):
                minis.generate(self.transcript, MODEL, 'Prompt')

    def test_cache_changes_for_model_prompt_and_transcript(self):
        first = minis.identity(self.transcript, MODEL, 'original', 'Prompt')
        self.assertNotEqual(first, minis.identity(self.transcript, MODEL, 'original', 'Changed'))
        self.assertNotEqual(first, minis.identity(self.transcript, dict(MODEL, entry_id='other/gpt-6-sol'), 'original', 'Prompt'))
        self.transcript.write_text('Changed transcript.', encoding='utf-8')
        self.assertNotEqual(first, minis.identity(self.transcript, MODEL, 'original', 'Prompt'))


class WorkflowSafetyTests(unittest.TestCase):
    def test_preflight_fails_before_upload_or_key_access(self):
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / 'sample.wav'
            source.write_bytes(b'synthetic')
            with mock.patch.object(workflow, 'choose_backend', return_value='sox'), \
                    mock.patch.object(minis, 'resolve_model', side_effect=RuntimeError('model unavailable')), \
                    mock.patch.object(workflow, 'api_key', side_effect=AssertionError('no key')), \
                    mock.patch.object(workflow, 'run', side_effect=AssertionError('no upload')), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(workflow.main(['--file', str(source)]), 1)

    def test_default_dry_run_does_not_invoke_model_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            source = pathlib.Path(temp) / 'sample.wav'
            source.write_bytes(b'synthetic')
            output = pathlib.Path(temp) / 'out'
            with mock.patch.object(workflow, 'choose_backend', return_value='sox'), \
                    mock.patch.object(minis, 'resolve_model', side_effect=AssertionError('no bridge')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(workflow.main(['--file', str(source), '--output', str(output), '--dry-run']), 0)
            self.assertFalse(output.exists())

    def test_backend_switch_reuses_asr_and_needs_no_groq_key_for_minis_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / 'sample.wav'
            source.write_bytes(b'synthetic')
            args = ['--file', str(source), '--output', str(root / 'out')]
            with mock.patch.object(workflow, 'choose_backend', return_value='sox'), \
                    mock.patch.object(transcribe, 'choose_backend', return_value='sox'), \
                    mock.patch.object(transcribe, 'duration_seconds', return_value=10), \
                    mock.patch.object(transcribe, 'convert_chunk'), \
                    mock.patch.object(transcribe, 'transcribe', return_value={'segments': [{'start': 0, 'end': 1, 'text': 'Synthetic'}]}) as asr, \
                    mock.patch.object(workflow, 'api_key'), \
                    mock.patch.object(workflow, 'chat_retry', return_value='Groq summary'), \
                    mock.patch('groq_summary.MIN_INTERVAL', 0), \
                    mock.patch.object(minis, 'resolve_model', return_value=MODEL), \
                    mock.patch.object(minis, 'generate_result', return_value=minis.SummaryResult('Minis summary', 'verified')) as summary, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(workflow.main(args + ['--summary-backend', 'groq']), 0)
                with mock.patch.object(workflow, 'api_key', side_effect=AssertionError('no Groq key')):
                    self.assertEqual(workflow.main(args + ['--summary-only']), 0)
                    self.assertEqual(workflow.main(args + ['--summary-only']), 0)
                self.assertEqual(asr.call_count, 1)
                self.assertEqual(summary.call_count, 1)
            folder = next((root / 'out').iterdir())
            self.assertEqual((folder / 'sample_摘要.md').read_text(encoding='utf-8'), 'Minis summary\n')
            self.assertTrue(list(folder.glob('sample_摘要_先前版本_*.md')))

    def test_source_hash_change_invalidates_complete_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            source = root / 'sample.wav'; source.write_bytes(b'AAAA')
            old = transcribe.settings(source, 'auto', 'whisper-large-v3-turbo', 'sox')
            source.write_bytes(b'BBBB')
            current = transcribe.settings(source, 'auto', 'whisper-large-v3-turbo', 'sox')
            meta = {'complete': True, 'source': source.name, 'settings': old}
            self.assertFalse(workflow.complete_transcription(root, source, meta, current))


class GroqHierarchyTests(unittest.TestCase):
    def test_retry_after_is_not_clipped_to_90_seconds(self):
        import urllib.error
        exc = urllib.error.HTTPError(groq_http.BASE, 429, 'rate limited', {'Retry-After': '125'}, io.BytesIO(b''))
        opener = mock.Mock()
        opener.open.side_effect = exc
        with mock.patch.dict('os.environ', {'GROQ_API_KEY': 'synthetic-test-key'}), \
                mock.patch.object(groq_http.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(groq_http.GroqError) as caught:
                groq_http._request('chat/completions', b'{}', 'application/json', 1)
        self.assertEqual(caught.exception.retry_after, 125)

    def test_split_preserves_text_and_bounded_inputs(self):
        text = '中文 English\n' * 1000
        chunks = groq_summary.split_text(text, 500)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(groq_summary.estimate(part) <= 500 for part in chunks))

    def test_413_splits_and_completed_nodes_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = pathlib.Path(temp)
            calls = []
            def call(messages, tokens):
                calls.append(messages[0]['content'])
                if len(calls) == 1:
                    raise groq_http.GroqError('too large', 413)
                return 'short note'
            with mock.patch('groq_summary.MIN_INTERVAL', 0):
                first = groq_summary.summarize('A' * 200, 'Prompt', folder, {'id': 'synthetic'}, call)
                second = groq_summary.summarize('A' * 200, 'Prompt', folder, {'id': 'synthetic'},
                                               mock.Mock(side_effect=AssertionError('must resume')))
            self.assertEqual(first, second)
            self.assertGreater(len(calls), 2)

    def test_groq_truncated_response_rejected(self):
        with mock.patch.object(groq_http, '_request', return_value={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]}):
            with self.assertRaises(groq_http.GroqError) as caught:
                groq_http.chat([], 'model')
        self.assertEqual(caught.exception.status, 413)


if __name__ == '__main__':
    unittest.main()
