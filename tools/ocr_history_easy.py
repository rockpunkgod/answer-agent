"""Optional local second OCR pass over the captured history panel.

Uses the already installed EasyOCR model with downloads disabled. Private text is
written only beside source screenshots; stdout reports counts, never messages.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    root = args.directory.resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    # Some Windows launcher environments omit USERNAME, which torch's local
    # cache helper expects. This does not alter the evidence or OCR content.
    os.environ.setdefault('USERNAME', 'local')
    import easyocr
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, download_enabled=False, verbose=False)
    written = reused = failed = 0
    for frame in manifest['frames']:
        image = Path(frame['file']).resolve()
        if image.parent != root or not image.is_file():
            failed += 1
            continue
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if frame.get('sha256') and digest != frame['sha256']:
            failed += 1
            continue
        output = image.with_suffix('.easyocr.json')
        if output.exists() and not args.force:
            try:
                if json.loads(output.read_text(encoding='utf-8')).get('image_sha256') == digest:
                    reused += 1
                    continue
            except (ValueError, OSError):
                pass
        try:
            with Image.open(image) as source:
                width, height = source.size
                # Early captures are whole app windows; newer captures are
                # right-panel-only. The main chat is deliberately excluded.
                left = 1572 if width > 1000 else 0
                top = 340 if width > 1000 else 0
                crop = np.array(source.crop((left, top, width, height)).convert('RGB'))
            found = reader.readtext(crop, detail=1, paragraph=False)
            lines = []
            for index, (points, value, confidence) in enumerate(found):
                xs = [float(point[0]) + left for point in points]
                ys = [float(point[1]) + top for point in points]
                lines.append({'index': index, 'text': value, 'confidence': float(confidence),
                              'rect': {'x': min(xs), 'y': min(ys),
                                       'width': max(xs)-min(xs), 'height': max(ys)-min(ys)}})
            record = {'image': str(image), 'image_sha256': digest,
                      'captured_at': frame.get('captured_at'), 'panel_only': width <= 1000,
                      'source': 'EasyOCR local model', 'network_used': False,
                      'width': width, 'height': height, 'lines': lines}
            output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            written += 1
        except Exception:
            failed += 1
    print(json.dumps({'manifest_frames': len(manifest['frames']), 'written': written,
                      'reused': reused, 'failed': failed, 'manifest_complete': manifest.get('complete', False)}))


if __name__ == '__main__':
    main()
