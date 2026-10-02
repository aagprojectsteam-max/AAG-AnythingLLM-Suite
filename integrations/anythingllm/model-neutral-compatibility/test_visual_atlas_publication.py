"""Public V2 metadata/provenance checks; no generation and no pixel payload needed."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from visual_atlas import VisualAtlas


def read(relative):
    return json.loads((ROOT / relative).read_text())


class AtlasPublicationTests(unittest.TestCase):
    def test_all_current_images_have_exact_prompt_bindings(self):
        manifest = read('visual-atlas/manifest/atlas-manifest.json')
        products = read('atlas-assets-manifest.json')
        preview = read('visual-atlas/manifest/preview-index.json')
        self.assertEqual(manifest['image_set'], 'visual-atlas-v2')
        entries = manifest['entries']
        self.assertEqual(len(entries), 493)
        by_id = {e['canonical_id']: e for e in entries}
        self.assertEqual(len(by_id), 493)
        self.assertEqual(set(by_id), {e['key'] for e in products['entries']})
        for entry in entries:
            with self.subTest(canonical_id=entry['canonical_id']):
                proof = entry['image_provenance']
                self.assertEqual(entry['canonical_id'], entry['family_id'] + '/' + entry['subfamily_id'])
                self.assertEqual(proof['canonical_id'], entry['canonical_id'])
                self.assertEqual(proof['image_sha256'], entry['sha256'])
                self.assertEqual(proof['prompt_sha256'], entry['prompt_sha256'])
                self.assertEqual(hashlib.sha256(entry['generation_prompt'].encode()).hexdigest(), entry['prompt_sha256'])
                for private in ('job_id', 'attempt_id', 'engine_prompt_id', 'backup', 'source_path', 'reviewed_at'):
                    self.assertNotIn(private, entry)
                    self.assertNotIn(private, proof)
        for item in products['entries']:
            self.assertEqual(item['reference']['sha256'], by_id[item['key']]['sha256'])
        for item in preview['entries']:
            entry = by_id[item['family_id'] + '/' + item['subfamily_id']]
            self.assertEqual(item['sha256'], entry['sha256'])
            self.assertEqual(item['prompt_sha256'], entry['prompt_sha256'])

    def test_catalog_discloses_prompts_without_injecting_them_into_selection(self):
        atlas = VisualAtlas()
        cards = [(f, s) for f in atlas.catalog()['families'] for s in f['subfamilies']]
        self.assertEqual(len(cards), 493)
        for family, style in cards:
            pair = (family['id'], style['id'])
            entry = atlas.entry(*pair)
            self.assertEqual(style['atlas']['prompt'], entry['generation_prompt'])
            self.assertEqual(style['atlas']['prompt_sha256'], entry['prompt_sha256'])
            selection = atlas._selection(pair, confidence=1, matched=[])
            self.assertEqual(selection['style_descriptor'], entry['style_descriptor'])
            self.assertNotIn('prompt', selection)
            self.assertNotIn('generation_prompt', selection)

    def test_product_metadata_and_aggregate_hashes_are_current(self):
        product = read('atlas-assets-manifest.json')
        self.assertEqual(product, read('visual-atlas/manifest/product-assets.json'))
        for relative, record in product['metadata'].items():
            content = (ROOT / relative).read_bytes()
            self.assertEqual(len(content), record['bytes'], relative)
            self.assertEqual(hashlib.sha256(content).hexdigest(), record['sha256'], relative)
        for role in ('reference', 'thumbnail'):
            rows = sorted(f"{e['key']}\0{e[role]['sha256']}\0{e[role]['bytes']}\n" for e in product['entries'])
            self.assertEqual(hashlib.sha256(''.join(rows).encode()).hexdigest(), product[role + '_set_sha256'])

    def test_installer_keeps_public_metadata_and_rejects_changed_pixels(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); source = base / 'source'; target = base / 'target'
            records = {}
            for role, relative in [('reference', 'images/test/style/preview.png'), ('thumbnail', 'thumbs/test/style.webp')]:
                path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
                content = ('fixture-' + role).encode(); path.write_bytes(content)
                records[role] = {'path': 'visual-atlas/' + relative, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
            (source / 'manifest').mkdir(); (source / 'manifest/atlas-manifest.json').write_text('{"private_source_state": true}')
            (source / 'images/unlisted.png').write_text('not in the pack')
            manifest = base / 'fixture.json'; manifest.write_text(json.dumps({'entries': [{'key': 'test/style', **records}]}))
            command = [sys.executable, str(ROOT / 'tools/atlas-assets.py'), 'install', '--source', str(source), '--target', str(target), '--manifest', str(manifest)]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((target / 'manifest/atlas-manifest.json').read_bytes(), (ROOT / 'visual-atlas/manifest/atlas-manifest.json').read_bytes())
            self.assertFalse((target / 'images/unlisted.png').exists())
            (source / 'images/test/style/preview.png').write_bytes(b'changed')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
