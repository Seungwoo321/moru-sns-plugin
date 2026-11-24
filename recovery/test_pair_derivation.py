import copy
import json
import sqlite3
import unittest
from unittest import mock

import reconcile as r
import candidate_pair as pair
import candidate_review as review
import test_candidate_pair as base


class PairDerivationTests(unittest.TestCase):
    setUp=base.CandidatePairTests.setUp
    write=base.CandidatePairTests.write
    artifact=base.CandidatePairTests.artifact
    register_pair=base.CandidatePairTests.register_pair
    video=base.CandidatePairTests.video
    review_doc=base.CandidatePairTests.review_doc
    approve=base.CandidatePairTests.approve
    publisher=base.CandidatePairTests.publisher
    finish=base.CandidatePairTests.finish

    def setup_derivative(self):
        self.register_pair()
        self.original=self.media
        self.derived=self.write('approved-format.mp4',b'synthetic-format-only-derived-bytes')
        self.derivation={'version':1,'runtime_id':'pair-test','pair_id':'one-approved-pair','account_id':'999','job_id':'pair-instagram',
                         'source_sha256':self.source_sha,'derived_sha256':review.file_digest(self.derived),'frame_count':922,
                         'frames_preserved':True,'audio_preserved':True,'transform_kind':'format_only','approval_record':'private-approval',
                         'inspection_sha256':self.artifact(b'Synthetic exact source/derivative frame sequence and music review')}
        self.media=self.derived;self.source_sha=self.derivation['derived_sha256']

    def register_mapping(self,doc=None,counts=(922,1)):
        with mock.patch.object(pair,'video_counts',return_value=counts),mock.patch.object(r.p,'prepare_video',return_value=self.video('instagram')):
            return pair.register_derivation(self.dest,doc or self.derivation,self.original,self.derived,self.artifacts)

    def test_exact_derived_ig_then_threads_preserves_original_pair(self):
        self.setup_derivative()
        ledger=r.p.Ledger(self.dest)
        try:before=tuple(ledger.db.execute('SELECT document,digest,legacy FROM candidate_pair').fetchone())
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.approve('instagram')
        self.register_mapping();self.approve('instagram');self.finish('instagram')
        self.approve('threads');self.finish('threads')
        ledger=r.p.Ledger(self.dest)
        try:
            self.assertEqual(tuple(ledger.db.execute('SELECT document,digest,legacy FROM candidate_pair').fetchone()),before)
            original_pair=json.loads(before[0]);self.assertEqual(original_pair['source']['sha256'],self.derivation['source_sha256'])
            self.assertNotEqual(original_pair['source']['sha256'],self.source_sha)
            self.assertEqual(pair.derivation(ledger,'one-approved-pair')['derived_sha256'],self.source_sha)
            self.assertEqual(ledger.get(('threads','123','legacy-unknown'))['state'],'unknown')
            self.assertFalse(json.loads(ledger.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])['inventory_complete'])
        finally:ledger.close()

    def test_arbitrary_file_wrong_binding_and_unapproved_transform_blocked(self):
        self.setup_derivative()
        for field,value in [('derived_sha256','0'*64),('source_sha256','0'*64),('job_id','other-job'),('account_id','123'),
                            ('frames_preserved',False),('audio_preserved',False),('transform_kind','new-material'),('inspection_sha256','0'*64)]:
            doc=copy.deepcopy(self.derivation);doc[field]=value
            with self.subTest(field=field),self.assertRaises((r.p.SafeError,OSError)):self.register_mapping(doc)
        with self.assertRaises(r.p.SafeError):self.register_mapping(counts=(921,1))
        with self.assertRaises(r.p.SafeError):self.register_mapping(counts=(922,0))

    def test_one_time_append_no_replacement_after_review_or_claim(self):
        self.setup_derivative();self.register_mapping()
        with self.assertRaises(sqlite3.IntegrityError):self.register_mapping()
        self.approve('instagram')
        with self.assertRaises(r.p.SafeError):self.register_mapping()

    def test_known_candidate_attempt_and_changed_legacy_still_block_mapping(self):
        self.setup_derivative();ledger=r.p.Ledger(self.dest)
        try:ledger.db.execute("INSERT INTO jobs(platform,account_id,job_id,state) VALUES('instagram','999','pair-instagram','unknown')")
        finally:ledger.close()
        with self.assertRaises(r.p.SafeError):self.register_mapping()

    def test_only_registered_derivative_can_approve_not_other_or_original(self):
        self.setup_derivative();self.register_mapping()
        for path in (self.original,self.write('unrelated.mp4',b'unrelated')):
            self.media=path;self.source_sha=review.file_digest(path)
            with self.assertRaises(r.p.SafeError):self.approve('instagram')

    def test_actual_frame_probe_and_native_profile(self):
        import subprocess
        # Real local fixtures; no provider calls. Both copies contain exactly 922 video frames and one audio track.
        source=self.root/'real-source.mp4';target=self.root/'real-derived.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=black:s=64x64:r=30','-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                        '-frames:v','922','-t','30.733333','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True,capture_output=True)
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-map','0','-c','copy','-metadata','comment=approved-format-fixture',str(target)],check=True,capture_output=True)
        source.chmod(0o600);target.chmod(0o600)
        self.assertEqual(pair.video_counts(source),(922,1));self.assertEqual(pair.video_counts(target),(922,1))
        self.assertNotEqual(review.file_digest(source),review.file_digest(target))
        self.assertEqual(r.p.prepare_video('instagram',str(target),allow_missing_url=True).preflight,'verified_local_profile')

if __name__=='__main__':unittest.main()
