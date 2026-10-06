"""Cross-platform bridge and draft tests. Synthetic data, no network or phone access."""
import contextlib
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import groq_http as http
import groq_transcribe as transcribe
import groq_workflow as workflow
import minis_summary as minis

MODEL = dict(entry_id='example/custom-model', model_id='custom-model',
             display_name='Custom Model', instance_label='example',
             modalities=['text_input', 'text_output'], context_window=1000000)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = pathlib.Path(self.temp.name)
        self.transcript = self.folder / 'sample_逐字稿.txt'
        self.transcript.write_text('Synthetic transcript.', encoding='utf-8')

    def generate(self, envelope, output):
        def invoke(command, **kwargs):
            target = pathlib.Path(command[command.index('--output') + 1])
            target.write_text(output, encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(envelope), stderr='')
        with mock.patch.object(minis.shutil, 'which', return_value='minis-model-use'), \
                mock.patch.object(minis.subprocess, 'run', side_effect=invoke):
            return minis.generate_result(self.transcript, MODEL, 'Prompt')

    def android(self, **extra):
        return dict(model='custom-model', text='Synthetic summary.', **extra)

    def ios(self, **extra):
        return dict(ok=True, data=dict(model_id='custom-model', **extra))

    def test_android_plain_text_becomes_pending_draft(self):
        self.assertEqual(self.generate(self.android(), 'Synthetic summary.\n'),
                         minis.SummaryResult('Synthetic summary.', 'completion_unverified'))

    def test_android_future_normal_completion_can_be_verified(self):
        self.assertEqual(self.generate(self.android(stop_reason='end_turn'), 'Synthetic summary.').verification,
                         'verified')

    def test_android_explicit_truncation_and_interrupt_rejected(self):
        for reason in ('length', 'max_tokens', 'incomplete', 'cancelled', 'unknown'):
            with self.subTest(reason=reason), self.assertRaises(RuntimeError):
                self.generate(self.android(stop_reason=reason), 'Synthetic summary.')

    def test_android_finish_reason_truncation_is_not_ignored(self):
        with self.assertRaises(RuntimeError):
            self.generate(self.android(finish_reason='length'), 'Synthetic summary.')

    def test_android_incomplete_status_is_not_ignored(self):
        with self.assertRaises(RuntimeError):
            self.generate(self.android(status='incomplete'), 'Synthetic summary.')

    def test_android_output_budget_exhaustion_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '輸出預算'):
            self.generate(self.android(usage={'output_tokens': 6000}), 'Synthetic summary.')

    def test_malformed_output_usage_rejected(self):
        for tokens in ('6000', True, -1, 1.5):
            with self.subTest(tokens=tokens), self.assertRaises(RuntimeError):
                self.generate(self.android(usage={'output_tokens': tokens}), 'Synthetic summary.')

    def test_android_stdout_and_file_mismatch_rejected(self):
        with self.assertRaises(RuntimeError):
            self.generate(self.android(), 'Different text.')

    def test_android_empty_text_rejected(self):
        with self.assertRaises(RuntimeError):
            self.generate(dict(model='custom-model', text='  '), '  ')

    def test_android_foreign_model_rejected(self):
        with self.assertRaisesRegex(RuntimeError, '身分'):
            self.generate(dict(model='other-model', text='Synthetic summary.'), 'Synthetic summary.')

    def test_ios_json_and_normal_completion_still_verified(self):
        result = self.generate(self.ios(stop_reason='completed'), json.dumps({'content': 'Synthetic summary.'}))
        self.assertEqual(result, minis.SummaryResult('Synthetic summary.', 'verified'))

    def test_ios_array_file_has_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, '格式不符'):
            self.generate(self.ios(stop_reason='completed'), '[]')

    def test_ios_missing_completion_is_not_verified(self):
        with self.assertRaisesRegex(RuntimeError, '完成狀態'):
            self.generate(self.ios(), json.dumps({'content': 'Synthetic summary.'}))

    def test_ios_foreign_file_model_rejected(self):
        with self.assertRaises(RuntimeError):
            self.generate(self.ios(stop_reason='end_turn'),
                          json.dumps({'content': 'Synthetic summary.', 'model_id': 'other'}))

    def test_error_envelope_cannot_masquerade_as_android_success(self):
        for envelope in (dict(self.android(), ok=False), dict(self.android(), error='private diagnostic')):
            with self.subTest(envelope=envelope), self.assertRaises(RuntimeError) as caught:
                self.generate(envelope, 'Synthetic summary.')
            self.assertNotIn('private diagnostic', str(caught.exception))

    def test_malformed_catalog_entry_is_skipped(self):
        with mock.patch.object(minis, 'bridge', return_value={'models': [dict(MODEL, modalities=None)]}):
            with self.assertRaisesRegex(RuntimeError, '摘要模型'):
                minis.resolve_model('custom-model')

    def test_user_agent_on_both_groq_endpoints_with_tls_and_no_redirect(self):
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{}'
        with mock.patch.dict('os.environ', {'GROQ_API_KEY': 'synthetic-test-key'}, clear=True), \
                mock.patch.object(http.urllib.request, 'build_opener', return_value=opener) as build, \
                mock.patch.object(http.ssl, 'create_default_context', return_value='verified-tls') as tls:
            for endpoint in ('audio/transcriptions', 'chat/completions'):
                http._request(endpoint, b'{}', 'application/json', 1)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header('User-agent'), http.USER_AGENT)
                self.assertIn('OpenMinis-Transcription-Workflow/', http.USER_AGENT)
                handler = build.call_args.args[0]
                self.assertIsNone(handler.redirect_request(None, None, None, None, None, None))
                self.assertEqual(build.call_args.args[1]._context, 'verified-tls')
            self.assertEqual(tls.call_count, 2)


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = pathlib.Path(self.temp.name)
        self.identity = dict(backend='minis', model='example/custom-model', style='original')

    def save(self, text='Draft.', identity=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return workflow.save_draft(self.folder, 'sample', text, identity or self.identity)

    def test_draft_filename_is_model_independent(self):
        draft = self.save()
        self.assertIn('_摘要_待核對草稿_', draft.name)
        self.assertNotIn('GPT', draft.name)
        self.assertNotIn('custom-model', draft.name)
        self.assertEqual(workflow.draft_ready(self.folder, 'sample', self.identity), draft)

    def test_draft_does_not_modify_reviewed_summary_or_final_stamp(self):
        final = self.folder / 'sample_摘要.md'
        stamp = self.folder / '摘要設定.json'
        final.write_text('Reviewed summary.', encoding='utf-8')
        stamp.write_text('{"reviewed":true}', encoding='utf-8')
        self.save()
        self.assertEqual(final.read_text(encoding='utf-8'), 'Reviewed summary.')
        self.assertEqual(stamp.read_text(encoding='utf-8'), '{"reviewed":true}')

    def test_manual_edit_creates_new_draft_without_overwriting(self):
        old = self.save()
        old.write_text('User edited draft.', encoding='utf-8')
        self.assertIsNone(workflow.draft_ready(self.folder, 'sample', self.identity))
        new = self.save('New draft.')
        self.assertNotEqual(old, new)
        self.assertEqual(old.read_text(encoding='utf-8'), 'User edited draft.')
        self.assertEqual(workflow.draft_ready(self.folder, 'sample', self.identity), new)

    def test_model_or_settings_change_keeps_both_drafts(self):
        old = self.save()
        for changed in (dict(self.identity, model='other/model'), dict(self.identity, style='cautious')):
            self.assertIsNone(workflow.draft_ready(self.folder, 'sample', changed))
            new = self.save('Changed draft.', changed)
            self.assertNotEqual(old, new)
            self.assertEqual(workflow.draft_ready(self.folder, 'sample', changed), new)
        self.assertEqual(workflow.draft_ready(self.folder, 'sample', self.identity), old)

    def test_untrusted_checkpoint_path_rejected(self):
        stamp = workflow.draft_stamp(self.folder, 'sample', self.identity)
        for name in ('../sample_摘要_待核對草稿_escape.md', '/sample_摘要_待核對草稿_escape.md', 'other.md'):
            stamp.write_text(json.dumps(dict(identity=self.identity, verification='completion_unverified',
                                            file=name, sha256='synthetic')), encoding='utf-8')
            self.assertIsNone(workflow.draft_ready(self.folder, 'sample', self.identity))


class WorkflowResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.sources = [self.root / name for name in ('first.wav', 'second.wav')]
        for source in self.sources:
            source.write_bytes(b'synthetic')
        patches = (
            mock.patch.dict('os.environ', {}, clear=True),
            mock.patch.object(workflow, 'choose_backend', return_value='sox'),
            mock.patch.object(transcribe, 'choose_backend', return_value='sox'),
            mock.patch.object(transcribe, 'duration_seconds', return_value=10),
            mock.patch.object(transcribe, 'convert_chunk'),
            mock.patch.object(transcribe, 'transcribe', return_value={'segments': [{'start': 0, 'end': 1, 'text': 'Synthetic'}]}),
            mock.patch.object(workflow, 'api_key'),
            mock.patch.object(minis, 'resolve_model', return_value=MODEL),
        )
        self.mocks = []
        for patch in patches:
            self.mocks.append(patch.start())
            self.addCleanup(patch.stop)
        self.asr = self.mocks[5]
        self.args = ['--source', str(self.root), '--output', str(self.root / 'out'), '--minis-model', 'custom-model']

    def execute(self, result, *extra):
        stdout = io.StringIO()
        with mock.patch.object(minis, 'generate_result', side_effect=result if isinstance(result, list) else None,
                               return_value=result) as generate, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = workflow.main(self.args + list(extra))
        report = json.loads(next(line[len('WORKFLOW_RESULT '):] for line in stdout.getvalue().splitlines()
                                 if line.startswith('WORKFLOW_RESULT ')))
        return code, report, generate.call_count

    def test_pending_draft_exit_and_reuse_do_not_upload_or_infer_again(self):
        result = minis.SummaryResult('Draft.', 'completion_unverified')
        code, report, calls = self.execute(result)
        self.assertEqual((code, report['status'], report['pending_review'], calls), (2, 'pending_review', 1, 1))
        code, report, calls = self.execute(result, '--summary-only')
        self.assertEqual((code, report['skipped_pending_review'], calls), (2, 1, 0))
        self.assertEqual(self.asr.call_count, 1)
        folder = next((self.root / 'out').iterdir())
        self.assertFalse((folder / '摘要設定.json').exists())
        self.assertFalse((folder / 'first_摘要.md').exists())

    def test_explicit_retry_preserves_old_draft_and_reuses_asr(self):
        result = minis.SummaryResult('Draft.', 'completion_unverified')
        self.execute(result)
        with mock.patch.object(workflow, 'api_key', side_effect=AssertionError('no key access')):
            code, report, calls = self.execute(result, '--summary-only', '--retry-summary')
        self.assertEqual((code, report['pending_review'], calls), (2, 1, 1))
        self.assertEqual(self.asr.call_count, 1)
        self.assertEqual(len(list((self.root / 'out').rglob('*_待核對草稿_*.md'))), 2)

    def test_edited_draft_rerun_is_not_blocked(self):
        result = minis.SummaryResult('Draft.', 'completion_unverified')
        self.execute(result)
        old = next((self.root / 'out').rglob('*_待核對草稿_*.md'))
        old.write_text('User edits.', encoding='utf-8')
        code, report, calls = self.execute(result, '--summary-only')
        self.assertEqual((code, report['pending_review'], calls), (2, 1, 1))
        self.assertEqual(old.read_text(encoding='utf-8'), 'User edits.')

    def test_mixed_batch_distinguishes_verified_pending_and_failure(self):
        result = [minis.SummaryResult('Verified.', 'verified'), minis.SummaryResult('Draft.', 'completion_unverified')]
        code, report, calls = self.execute(result, '--all')
        self.assertEqual((code, report['verified'], report['pending_review'], report['failed'], calls), (2, 1, 1, 0, 2))
        result = [RuntimeError('failed'), minis.SummaryResult('Draft.', 'completion_unverified')]
        code, report, calls = self.execute(result, '--all', '--summary-only', '--retry-summary')
        self.assertEqual((code, report['status'], report['verified'], report['pending_review'], report['failed']),
                         (1, 'failed', 0, 1, 1))

    def test_verified_rerun_retains_completion_and_can_be_explicitly_retried(self):
        result = minis.SummaryResult('Verified.', 'verified')
        self.assertEqual(self.execute(result)[0], 0)
        code, report, calls = self.execute(result, '--summary-only')
        self.assertEqual((code, report['skipped_verified'], calls), (0, 1, 0))
        code, report, calls = self.execute(result, '--summary-only', '--retry-summary')
        self.assertEqual((code, report['verified'], calls), (0, 1, 1))
        draft_result = minis.SummaryResult('New pending draft.', 'completion_unverified')
        code, report, calls = self.execute(draft_result, '--summary-only', '--retry-summary')
        self.assertEqual((code, report['pending_review']), (2, 1))
        code, report, calls = self.execute(draft_result, '--summary-only')
        self.assertEqual((code, report['skipped_pending_review'], calls), (2, 1, 0))
        edited = next((self.root / 'out').rglob('*_待核對草稿_*.md'))
        edited.write_text('User edited pending draft.', encoding='utf-8')
        code, report, calls = self.execute(draft_result, '--summary-only')
        self.assertEqual((code, report['pending_review'], calls), (2, 1, 1))
        self.assertEqual(edited.read_text(encoding='utf-8'), 'User edited pending draft.')
        code, report, calls = self.execute(result, '--summary-only', '--retry-summary')
        self.assertEqual((code, report['verified'], calls), (0, 1, 1))
        code, report, calls = self.execute(result, '--summary-only')
        self.assertEqual((code, report['skipped_verified'], calls), (0, 1, 0))
        self.assertTrue(list((self.root / 'out').rglob('*_待核對草稿_*.md')))
        self.assertEqual(self.asr.call_count, 1)

    def test_known_failure_preserves_old_final_and_no_pending_success(self):
        self.execute(minis.SummaryResult('Reviewed.', 'verified'))
        code, report, calls = self.execute([RuntimeError('length')], '--summary-only', '--retry-summary')
        self.assertEqual((code, report['failed'], report['pending_review']), (1, 1, 0))
        final = next((self.root / 'out').rglob('*_摘要.md'))
        self.assertEqual(final.read_text(encoding='utf-8'), 'Reviewed.\n')


if __name__ == '__main__':
    unittest.main()
